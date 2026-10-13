"""Prova de que a unificação não perdeu nada: cada workflow antigo (em tests/legacy) gera o MESMO grafo de API
que o workflow unificado + prompt/modo/LoRAs correspondentes. Comparação estrutural: ignora ids de nós,
sementes, ordem da cadeia de LoRAs (compara como conjunto) e o texto do prompt (vem de outro lugar)."""
import json
from pathlib import Path

import pytest
import yaml

from clefflow.converter import Converter, node_id
from clefflow import pvars
from clefflow.loras import resolve
from clefflow.store import Store

ROOT = Path(__file__).resolve().parent.parent
LEG = ROOT / "tests" / "legacy"
IGNORE = {"seed", "noise_seed", "filename_prefix"}
LORA_TYPES = {"LoraLoader", "LoraLoaderModelOnly"}


def base(n):
    # o ClefFlow só usa a LoRA de consistência final: o arquivo "-2000" dos workflows antigos vale como ela
    return str(n).replace("\\", "/").rsplit("/", 1)[-1].lower().replace("consistency-2000", "consistency")


def collapse_loras(api):
    """Troca a cadeia de LoRAs por um nó só com o CONJUNTO (arquivo, forças) das que estão ligadas."""
    chain = {k: v for k, v in api.items() if v["class_type"] in LORA_TYPES or v["class_type"].startswith("Power Lora")}
    if not chain:
        return api
    entries, src_model, src_clip = [], None, None
    for k, n in chain.items():
        i = n["inputs"]
        if n["class_type"] in LORA_TYPES:
            sm = float(i.get("strength_model", 0))
            sc = float(i.get("strength_clip", sm)) if n["class_type"] == "LoraLoader" else sm
            if sm or sc:
                entries.append((base(i["lora_name"]), round(sm, 4), round(sc, 4) if n["class_type"] == "LoraLoader" else round(sm, 4)))
        for key, kind in (("model", "m"), ("clip", "c")):
            v = i.get(key)
            if isinstance(v, list) and str(v[0]) not in chain:
                if kind == "m":
                    src_model = v
                else:
                    src_clip = v
    out = {k: v for k, v in api.items() if k not in chain}
    out["LORASET"] = {"class_type": "LORASET", "inputs": {"set": sorted(entries), "model": src_model, "clip": src_clip}}
    for node in out.values():
        for k, v in list(node["inputs"].items()):
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in chain:
                node["inputs"][k] = ["LORASET", v[1]]
    return out


def signature(api):
    memo = {}

    def sig(nid):
        if nid in memo:
            return memo[nid]
        n = api[nid]
        items = []
        for k, v in sorted(n["inputs"].items()):
            if k in IGNORE:
                continue
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and str(v[0]) in api:
                items.append((k, ("link", sig(str(v[0])), v[1])))
            else:
                items.append((k, json.dumps(v, sort_keys=True)))
        memo[nid] = (n["class_type"], tuple(items))
        return memo[nid]
    outs = sorted(sig(k) for k, n in api.items() if n["class_type"] in ("SaveImage", "PreviewImage"))
    return outs, sorted(sig(k) for k in api)


def build(oi, wf, m, filled_nodes, prompt_node, prompt_widget, extra_overrides=None, bypass=None, chain=None, loras=None):
    overrides = {(node_id(prompt_node), prompt_widget): "PROMPT"}
    overrides.update(extra_overrides or {})
    drop = {str(n) for n in m.get("drop", [])}
    keep = set()
    for s in m["images"]:
        n = node_id(s["node"])
        if str(n) in filled_nodes:
            overrides[(n, "image")] = f"up_{filled_nodes[str(n)]}.png"
            keep.add(str(n))
        else:
            drop.add(str(n))
    for c in m.get("set", []):
        overrides[(node_id(c["node"]), c["widget"])] = c["value"]
    api = Converter(oi, randomize_seeds=False).convert(wf, overrides, drop, "up_1.png", keep, bypass, chain, loras)
    return api


def legacy_graph(oi, wid, n_images):
    m = yaml.safe_load((LEG / "registry" / f"{wid}.yaml").read_text(encoding="utf-8"))
    path = next(p for p in (ROOT / "defaults" / "workflows" / m["file"], LEG / "workflows" / m["file"]) if p.exists())
    wf = json.loads(path.read_text(encoding="utf-8"))
    filled = {str(s["node"]): i + 1 for i, s in enumerate(m["images"][:n_images])}
    p = m["prompt"]
    return build(oi, wf, m, filled, p["node"], p.get("widget", "value")), m


def unified_graph(oi, store, wid, mode, preset_id, n_images, extras=(), choices=None):
    m = store.manifest(wid, mode)
    preset = next(p for p in store.prompts(wid)["presets"] if p["id"] == preset_id)
    if preset.get("vars"):
        preset = pvars.render(preset, pvars.fit(preset, choices or {}, n_images))
    res = resolve(store.loras(), m, preset, list(extras), mode)
    # as LoRAs do teste existem no object_info; usa o arquivo como está na biblioteca
    filled = {str(s["node"]): i + 1 for i, s in enumerate(m["images"][:n_images])}
    wf = store.workflow_json(wid, mode)
    p = m["prompt"]
    api = build(oi, wf, m, filled, p["node"], p.get("widget", "value"), res["overrides"], res["bypass"],
                m.get("lora_chain"), res["stack"] if m.get("lora_chain") else None)
    return api


def canon(api, mode):
    api = collapse_loras(api)
    if mode == "turbo":                  # o guia sem CFG não usa o negativo: só o texto digitado importa
        for n in api.values():
            if n["class_type"] == "TextEncodeQwenImage21":
                n["inputs"].pop("negative_prompt", None)
    return signature(api)


CASES = [
    # (id antigo, modo, imagens, prompt unificado, LoRAs extras do Clef, respostas das variáveis do prompt)
    ("edit_turbo", "turbo", 1, "livre", (), None),
    ("edit_turbo", "turbo", 2, "estilo", (), None),
    ("edit_turbo", "turbo", 3, "exposicao", (), None),
    ("edit_quality", "quality", 1, "livre", (), None),
    ("edit_quality", "quality", 2, "estilo", (), None),
    ("edit_quality", "quality", 3, "livre", (), None),
    ("bodyswap_turbo", "turbo", 2, "trocar_pessoas", (), {"people": "1", "scope": "body"}),
    ("bodyswap_turbo", "turbo", 3, "trocar_pessoas", (), {"people": "1", "scope": "body", "place": "place"}),
    ("bodyswap_turbo", "turbo", 2, "livre", ("bfs_body",), None),         # o Clef liga a LoRA sozinho
    ("bodyswap_quality", "quality", 2, "trocar_pessoas", (), {"people": "1", "scope": "body"}),
    ("bodyswap_quality", "quality", 2, "livre", ("bfs_body",), None),
    ("headswap_turbo", "turbo", 2, "trocar_pessoas", (), {"people": "1", "scope": "head"}),
    ("headswap_turbo", "turbo", 2, "livre", ("bfs_head",), None),
    ("headswap_quality", "quality", 2, "trocar_pessoas", (), {"people": "1", "scope": "head"}),
    ("headswap_quality", "quality", 2, "livre", ("bfs_head",), None),
]


def _id(c):
    v = "+".join(f"{k}={x}" for k, x in (c[5] or {}).items())
    return f"{c[0]}-{c[2]}img-{c[3]}{'+' + c[4][0] if c[4] else ''}{'-' + v if v else ''}"


@pytest.mark.parametrize("legacy,mode,n,preset,extras,choices", CASES, ids=[_id(c) for c in CASES])
def test_unified_graph_equals_the_old_workflow(project, oi, legacy, mode, n, preset, extras, choices):
    old, _m = legacy_graph(oi, legacy, n)
    new = unified_graph(oi, Store(project), "edit", mode, preset, n, extras, choices)
    assert canon(new, mode) == canon(old, mode)


def test_t2i_turbo_is_the_old_workflow_without_images(project, oi):
    old, _ = legacy_graph(oi, "t2i_turbo", 0)
    new = unified_graph(oi, Store(project), "t2i", "turbo", "livre", 0)
    assert canon(new, "turbo") == canon(old, "turbo")


@pytest.mark.parametrize("wid,legacy_turbo,legacy_quality", [("object_move", "object_move_turbo", "object_move"),
                                                              ("transfer_object", "transfer_object_turbo", "transfer_object")])
def test_two_file_pairs_became_modes_without_changing_their_wiring(project, wid, legacy_turbo, legacy_quality):
    store = Store(project)
    for mode, old_id in (("turbo", legacy_turbo), ("quality", legacy_quality)):
        old = yaml.safe_load((LEG / "registry" / f"{old_id}.yaml").read_text(encoding="utf-8"))
        new = store.manifest(wid, mode)
        for k in ("images", "prompt", "params", "file", "outputs"):
            assert new.get(k) == old.get(k), (wid, mode, k)
    old_p = yaml.safe_load((LEG / "prompts" / f"{legacy_turbo}.yaml").read_text(encoding="utf-8"))
    assert [p["id"] for p in store.prompts(wid)["presets"]] == [p["id"] for p in old_p["presets"]]


def test_every_old_prompt_survives_as_a_preset(project):
    """Nenhum texto de prompt dos workflows antigos se perdeu (eles viraram prompts do 'edit' ou instruções de LoRA)."""
    store = Store(project)
    texts = {" ".join(p["text"].split()) for p in store.prompts("edit")["presets"]}
    for lo in store.loras().values():
        pr = lo.get("prompt")
        texts.update(" ".join(t.split()) for t in (pr.values() if isinstance(pr, dict) else [pr or ""]))
    blob = "\n".join(texts)
    for old_id in ("edit_turbo", "edit_quality", "headswap_turbo", "headswap_quality", "bodyswap_quality", "multi_bodyswap"):
        for p in yaml.safe_load((LEG / "prompts" / f"{old_id}.yaml").read_text(encoding="utf-8"))["presets"]:
            body = " ".join(p["text"].replace("Extra instructions: {request}", "").replace("{request}", "").split())
            if len(body) < 12:
                continue
            assert body[:60] in blob, (old_id, p["id"], body[:60])
