"""Valida registro + workflows contra o `object_info` do ComfyUI em execução.

Responde, para cada workflow registrado: os nós existem? as ligações do manifesto (imagens,
prompt, caixas, saída) apontam para nós e widgets reais? os modelos/LoRAs citados estão instalados?
"""
from __future__ import annotations

from typing import Any

from .converter import ConversionError, Converter, flatten_subgraphs, node_id, widget_list
from .store import Store


def validate_workflow(store: Store, wid: str, oi: dict) -> dict:
    m = store.manifest(wid)
    res: dict[str, Any] = {"id": wid, "title": m.get("title", wid), "errors": [], "warnings": []}
    try:
        wf = store.workflow_json(wid)
    except Exception as e:  # noqa: BLE001
        res["errors"].append(str(e))
        return _final(res)
    if "nodes" in wf:
        flat, _, _ = flatten_subgraphs(wf)
        nodes = {n["id"]: n for n in flat["nodes"]}
        nodes.update({n["id"]: n for n in wf["nodes"]})          # o nó-subgraph de fora também conta
    else:
        nodes = {node_id(k): {"type": v["class_type"]} for k, v in wf.items()}

    def node_type(nid):
        return nodes.get(node_id(nid), {}).get("type")

    for s in m.get("images", []):
        if node_type(s["node"]) != "LoadImage":
            res["errors"].append(f"Entrada '{s['key']}': o nó {s['node']} não é um LoadImage ({node_type(s['node'])}).")
    if m.get("prompt"):
        p = m["prompt"]
        t = node_type(p["node"])
        if not t:
            res["errors"].append(f"Prompt: o nó {p['node']} não existe.")
        elif t in oi and p.get("widget", "value") not in [w.name for w in widget_list(oi[t])]:
            res["errors"].append(f"Prompt: {t} não tem o widget '{p.get('widget', 'value')}'.")
    for p in m.get("params", []):
        t = node_type(p["node"])
        if not t:
            res["errors"].append(f"Parâmetro '{p['key']}': o nó {p['node']} não existe.")
        elif t in oi:
            have = {w.name for w in widget_list(oi[t])}
            for w in p.get("widgets", []):
                if w not in have:
                    res["errors"].append(f"Parâmetro '{p['key']}': {t} não tem o widget '{w}'.")
    for o in m.get("outputs", []):
        if node_type(o) not in ("SaveImage", "PreviewImage", "SaveImageAdvanced"):
            res["errors"].append(f"Saída: o nó {o} não é SaveImage/PreviewImage ({node_type(o)}).")
    if res["errors"]:
        return _final(res)

    overrides: dict[tuple[int, str], Any] = {}
    drop = {str(n) for n in m.get("drop", [])}
    keep = set()
    for s in m.get("images", []):
        overrides[(node_id(s["node"]), "image")] = "__doctor__.png"
        keep.add(str(s["node"]))
    conv = Converter(oi)
    try:
        api = conv.convert(wf, overrides, drop, "__doctor__.png", keep)
    except ConversionError as e:
        res["errors"].append(str(e))
        return _final(res)
    for nid, t, wn, old, new in conv.fixes:
        res["warnings"].append(f"{t} #{nid}: '{old}' foi trocado por '{new}' (o arquivo está em outra subpasta; corrigido na hora de rodar).")
    for o in m.get("outputs", []):
        if str(o) not in api:
            res["errors"].append(f"A saída {o} desapareceu do grafo convertido.")
    res["errors"].extend(check_api(api, oi))
    for nid, node in api.items():
        info = oi.get(node["class_type"], {})
        for w in widget_list(info):
            v = node["inputs"].get(w.name)
            if w.kind == "COMBO" and v is not None and w.options and v not in w.options and node["class_type"] != "LoadImage":
                res["warnings"].append(f"{node['class_type']} #{nid}: '{v}' não está instalado em '{w.name}'.")
    return _final(res)


SKIP_REQUIRED = ("COMFY_AUTOGROW_V3", "COMFY_MATCHTYPE_V3")


def check_api(api: dict, oi: dict) -> list[str]:
    """O que o ComfyUI checaria ao receber o grafo: nó existe, entradas obrigatórias presentes,
    ligações apontam para uma saída que existe, números dentro dos limites."""
    errs: list[str] = []
    for nid, node in api.items():
        ct = node["class_type"]
        info = oi.get(ct)
        if info is None:
            errs.append(f"#{nid}: o nó '{ct}' não está instalado.")
            continue
        for key, spec in ((info.get("input") or {}).get("required") or {}).items():
            kind = spec[0] if spec else None
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if key in node["inputs"] or kind in SKIP_REQUIRED or opts.get("socketless"):
                continue
            errs.append(f"{ct} #{nid}: falta a entrada obrigatória '{key}'.")
        for key, val in node["inputs"].items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[1], int):
                src = api.get(str(val[0]))
                if src is None:
                    errs.append(f"{ct} #{nid}: '{key}' vem do nó {val[0]}, que não está no grafo.")
                    continue
                outs = (oi.get(src["class_type"]) or {}).get("output") or []
                if outs and val[1] >= len(outs):
                    errs.append(f"{ct} #{nid}: '{key}' usa a saída {val[1]} de {src['class_type']}, que só tem {len(outs)}.")
        for w in widget_list(info):
            v = node["inputs"].get(w.name)
            if w.kind in ("INT", "FLOAT") and isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = w.opts.get("min"), w.opts.get("max")
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    errs.append(f"{ct} #{nid}: '{w.name}'={v} fora do limite ({lo}..{hi}).")
    return errs


def _final(res: dict) -> dict:
    res["ok"] = not res["errors"]
    return res


def validate_all(store: Store, oi: dict) -> list[dict]:
    return [validate_workflow(store, wid, oi) for wid in store.manifests()]
