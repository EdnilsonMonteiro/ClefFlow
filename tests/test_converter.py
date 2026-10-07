import copy
import json
import random
from pathlib import Path

import pytest

from clefflow.converter import Converter, ConversionError
from clefflow.doctor import check_api, validate_all, validate_workflow
from clefflow.store import Store

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / "defaults" / "workflows"


def load(name):
    return json.loads((WF / name).read_text(encoding="utf-8"))


def test_widgets_follow_saved_order_and_control_values(oi):
    api = Converter(oi, randomize_seeds=False).convert(load("Qwen2.1 Image Edit Quality.json"))
    ks = next(n for n in api.values() if n["class_type"] == "KSampler")["inputs"]
    assert (ks["steps"], ks["cfg"], ks["sampler_name"], ks["scheduler"], ks["denoise"]) == (25, 4, "er_sde", "beta", 1)
    assert ks["positive"][1] == 0 and isinstance(ks["model"], list)


def test_randomize_seed_changes_only_when_control_is_randomize(oi):
    wf = load("Qwen2.1 Image Edit Quality.json")
    a = Converter(oi, rng=random.Random(1)).convert(wf)
    b = Converter(oi, rng=random.Random(2)).convert(wf)
    sa = next(n for n in a.values() if n["class_type"] == "KSampler")["inputs"]["seed"]
    sb = next(n for n in b.values() if n["class_type"] == "KSampler")["inputs"]["seed"]
    assert sa != sb
    fixed = copy.deepcopy(wf)
    for n in fixed["nodes"]:
        if n["type"] == "KSampler":
            n["widgets_values"][1] = "fixed"
            n["widgets_values_named"]["control_after_generate"] = "fixed"
    sc = next(n for n in Converter(oi, rng=random.Random(3)).convert(fixed).values()
              if n["class_type"] == "KSampler")["inputs"]["seed"]
    assert sc == next(n for n in fixed["nodes"] if n["type"] == "KSampler")["widgets_values"][0]


def test_named_values_are_used_but_position_still_works_for_old_files(oi):
    wf = load("Qwen2.1 Image Edit Quality.json")
    old = copy.deepcopy(wf)
    for n in old["nodes"]:
        n.pop("widgets_values_named", None)
    a = Converter(oi, randomize_seeds=False).convert(wf)
    b = Converter(oi, randomize_seeds=False).convert(old)
    assert a == b


def test_linked_widget_uses_link_not_value(oi):
    api = Converter(oi).convert(load("Qwen2.1 BodySwap Turbo.json"))
    enc = api["18"]["inputs"]
    assert enc["prompt"] == ["6", 0]
    assert enc["images.image_1"] == ["40", 0] and enc["images.image_2"] == ["41", 0]


def test_notes_are_removed(oi):
    for name in ("Qwen2.1 BodySwap Turbo.json", "Qwen2.1 Image Edit Quality.json"):
        api = Converter(oi).convert(load(name))
        assert not any(n["class_type"] == "MarkdownNote" for n in api.values())
        for n in api.values():
            for v in n["inputs"].values():
                if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int):
                    assert v[0] in api


def test_rgthree_power_lora_loader(oi):
    """Workflows seus com o Power Lora Loader (rgthree): ligado sai no formato do rgthree, tudo desligado some."""
    oi2 = {**oi, "Power Lora Loader (rgthree)": {"input": {"required": {}, "optional": {"model": ["MODEL", {}], "clip": ["CLIP", {}]}},
                                                 "input_order": {"required": [], "optional": ["model", "clip"]}, "output": ["MODEL", "CLIP"]}}
    wf = load("Qwen2.1 Image Edit Quality.json")
    n = next(n for n in wf["nodes"] if n["type"] == "LoraLoader")
    n["type"] = "Power Lora Loader (rgthree)"
    n["widgets_values"] = [None, {"type": "PowerLoraLoaderHeaderWidget"},
                           {"on": True, "lora": "flymy_realism.safetensors", "strength": 0.5}, None, ""]
    n.pop("widgets_values_named", None)
    pl = next(v for v in Converter(oi2).convert(wf).values() if v["class_type"] == "Power Lora Loader (rgthree)")["inputs"]
    assert pl["lora_1"]["on"] is True and pl["➕ Add Lora"] == "" and "PowerLoraLoaderHeaderWidget" in pl
    n["widgets_values"][2]["on"] = False
    off = Converter(oi2).convert(wf)
    assert not any(v["class_type"] == "Power Lora Loader (rgthree)" for v in off.values())


def test_overrides_and_loader_upload(oi):
    wf = load("Qwen2.1 Image Edit Quality.json")
    api = Converter(oi).convert(wf, {(6, "value"): "hello", (26, "image"): "clefflow/x.png"}, set(), "clefflow/x.png", {26})
    assert api["6"]["inputs"]["value"] == "hello" and api["26"]["inputs"]["image"] == "clefflow/x.png"


def test_dropping_optional_image_removes_dotted_input_and_keeps_graph_valid(oi):
    api = Converter(oi).convert(load("Qwen2.1 Image Edit Quality.json"), drop_nodes={27})
    assert "27" not in api and "images.image_2" not in api["18"]["inputs"] and "images.image_1" in api["18"]["inputs"]


def test_dropping_required_chain_cascades_through_scalers(oi):
    api = Converter(oi).convert(load("Qwen2.1 Multi BodySwap.json"), drop_nodes={11})
    assert "11" not in api and "15" not in api           # loader e o scaler que dependia dele
    enc = api["17"]["inputs"]
    assert "images.image_4" not in enc and "images.image_3" in enc


def test_stale_loader_file_is_replaced_by_fallback(oi):
    oi2 = copy.deepcopy(oi)
    oi2["LoadImage"]["input"]["required"]["image"] = [["so_este.png"], {}]
    api = Converter(oi2).convert(load("Qwen2.1 Image Edit Quality.json"), {}, set(), "clefflow/up.png", set())
    assert api["26"]["inputs"]["image"] == "clefflow/up.png"         # o arquivo salvo no workflow não existe mais


def test_missing_custom_node_is_reported(oi):
    oi2 = {k: v for k, v in oi.items() if k != "ViggleTurboLora"}
    with pytest.raises(ConversionError, match="ViggleTurboLora"):
        Converter(oi2).convert(load("Qwen2.1 Image Edit Turbo.json"))


# ------------------------------------------------------------------ subgraphs
def test_subgraph_is_flattened_with_outer_values_and_prompt_override(oi):
    wf = load("image_qwen_image_2_1_background_removal.json")
    api = Converter(oi).convert(wf, {(459, "prompt"): "BYE", (470, "image"): "a.png"}, {"472"}, "a.png", {470})
    assert "459" not in api and "472" not in api                       # o nó-subgraph some, a comparação foi descartada
    enc = api["459:474"]["inputs"]
    assert enc["prompt"] == "BYE" and enc["images.image_1"] == ["470", 0] and enc["resolution"] == 0
    assert api["461"]["inputs"]["images"] == ["459:457", 0]            # a saída do subgraph liga no SaveImageAdvanced
    ks = api["459:458"]["inputs"]
    assert ks["steps"] == 25 and ks["cfg"] == 1 and ks["sampler_name"] == "euler"
    assert check_api(api, oi) == []


def test_dynamic_combo_keys_are_emitted(oi):
    api = Converter(oi).convert(load("image_qwen_image_2_1_background_removal.json"), {}, {"472"}, "a.png", set())
    save = api["461"]["inputs"]
    assert save["format"] == "png" and save["format.bit_depth"] == "8-bit" and save["format.input_color_space"] == "sRGB"


def test_subgraph_text_reaches_switch_input_and_branch_can_be_dropped(oi):
    wf = load("image_qwen_image_2_1_t2i.json")
    drop = {"459:471", "459:473", "459:475"}
    api = Converter(oi).convert(wf, {(459, "prompt"): "HELLO"}, drop)
    assert api["459:474"]["inputs"]["on_false"] == "HELLO" and "on_true" not in api["459:474"]["inputs"]
    assert api["459:452"]["inputs"]["prompt"] == ["459:472", 0]
    assert not any(k in api for k in drop)
    assert api["459:456"]["inputs"]["width"] == ["13", 0]              # tamanho vem do ResolutionSelector de fora
    assert check_api(api, oi) == []


def test_flatten_never_modifies_the_source(oi):
    wf = load("image_qwen_image_2_1_t2i.json")
    before = json.dumps(wf, sort_keys=True)
    Converter(oi).convert(wf)
    assert json.dumps(wf, sort_keys=True) == before


def test_api_format_passthrough_with_overrides(oi):
    api_wf = {"1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "a"}},
              "2": {"class_type": "KSampler", "inputs": {"seed": 5}}}
    out = Converter(oi).convert(api_wf, {(1, "value"): "b"})
    assert out["1"]["inputs"]["value"] == "b" and out["2"]["inputs"]["seed"] != 5


def test_original_files_are_never_modified(oi):
    p = WF / "Qwen2.1 Image Edit Quality.json"
    before = p.read_bytes()
    Converter(oi).convert(json.loads(before))
    assert p.read_bytes() == before


# --------------------------------------------------------------------- doctor
def test_every_default_workflow_validates_against_real_object_info(project, oi):
    store = Store(project)
    results = validate_all(store, oi)
    assert len(results) == 15
    bad = {r["id"]: r["errors"] for r in results if not r["ok"]}
    assert not bad, bad
    assert all(not r["warnings"] for r in results), [r for r in results if r["warnings"]]


def test_defaults_ship_no_personal_or_nsfw_text():
    import re
    bad = re.compile(r"nsfw|naked|penis|boob|nude|nipple|4060\s?ti", re.I)
    for p in (ROOT / "defaults").rglob("*"):
        if p.is_file():
            assert not bad.search(p.read_text(encoding="utf-8")), p


def test_doctor_flags_wrong_binding_and_missing_model(project, oi):
    store = Store(project)
    import yaml
    store.ensure_custom_dirs()
    m = yaml.safe_load((project / "defaults" / "registry" / "edit_quality.yaml").read_text(encoding="utf-8"))
    m["images"][0]["node"] = 18          # TextEncode, não LoadImage
    (project / "custom" / "registry" / "edit_quality.yaml").write_text(yaml.dump(m), encoding="utf-8")
    r = validate_workflow(store, "edit_quality", oi)
    assert not r["ok"] and "LoadImage" in r["errors"][0]
    (project / "custom" / "registry" / "edit_quality.yaml").unlink()
    oi2 = json.loads(json.dumps(oi))
    oi2["UNETLoader"]["input"]["required"]["unet_name"] = [["outro.safetensors"], {}]
    r2 = validate_workflow(store, "edit_quality", oi2)
    assert r2["ok"] and any("qwen_image_2.1_int8_convrot" in w for w in r2["warnings"])


def test_check_api_catches_missing_required_input_and_bad_link(oi):
    api = {"1": {"class_type": "KSampler", "inputs": {"model": ["9", 0], "seed": 1}}}
    msgs = " | ".join(check_api(api, oi))
    assert "positive" in msgs and "nó 9" in msgs


def test_combo_value_without_subfolder_is_fixed(oi):
    """LoRA salvo como 'x.safetensors' mas instalado em 'Pasta/x.safetensors' deve ser corrigido."""
    oi2 = copy.deepcopy(oi)
    lora = oi2["LoraLoader"]["input"]["required"]["lora_name"]
    lora[0] = ["Pasta\\bfs_head.safetensors", "outra.safetensors"]
    wf = {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": "bfs_head.safetensors", "strength_model": 1, "strength_clip": 1}},
          "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}}}
    c = Converter(oi2)
    c._finish(wf, set(), None, set())
    assert wf["1"]["inputs"]["lora_name"] == "Pasta\\bfs_head.safetensors"
    assert c.fixes and c.fixes[0][3] == "bfs_head.safetensors"


def test_missing_clefflow_node_points_to_installer(oi):
    oi2 = {k: v for k, v in oi.items() if k != "DrawBox"}
    with pytest.raises(ConversionError, match="install_nodes"):
        Converter(oi2).convert(load("Qwen2.1 Transfer Object (2 imagens).json"))


def test_default_workflows_ship_no_personal_image_names():
    for p in WF.glob("*.json"):
        for n in json.loads(p.read_text(encoding="utf-8"))["nodes"]:
            if n["type"] == "LoadImage":
                assert n["widgets_values"][0] == "example.png", (p.name, n["id"])


# ----------------------------------------------- imagens opcionais vazias não custam nada
def _api_with(store, oi, wid, filled_keys):
    from clefflow.converter import node_id
    m = store.manifest(wid)
    wf = store.workflow_json(wid)
    drop = {str(n) for n in m.get("drop", [])}
    keep, overrides = set(), {}
    for s in m["images"]:
        n = node_id(s["node"])
        if s["key"] in filled_keys:
            overrides[(n, "image")] = "up.png"
            keep.add(str(n))
        else:
            drop.add(str(n))
    return Converter(oi, randomize_seeds=False).convert(wf, overrides, drop, "up.png", keep)


def _optional_workflows(store):
    return [wid for wid, m in store.manifests().items() if any(not s.get("required", True) for s in m["images"])]


def test_empty_optional_images_leave_the_graph(project, oi):
    """Referência que o usuário não manda sai do grafo (carregador, escala e entrada do encoder):
    o ComfyUI não executa nada para ela, então o fluxo não fica mais lento."""
    store = Store(project)
    wids = _optional_workflows(store)
    assert {"edit_quality", "edit_turbo", "bodyswap_turbo", "multi_bodyswap"} <= set(wids)
    for wid in wids:
        keys = [s["key"] for s in store.manifest(wid)["images"]]
        required = [s["key"] for s in store.manifest(wid)["images"] if s.get("required", True)]
        sizes = []
        for k in range(len(required), len(keys) + 1):
            api = _api_with(store, oi, wid, set(keys[:k]))
            assert not check_api(api, oi), (wid, k, check_api(api, oi))
            assert sum(n["class_type"] == "LoadImage" for n in api.values()) == k, (wid, k)
            enc = [n for n in api.values() if n["class_type"] == "TextEncodeQwenImage21"][0]["inputs"]
            assert sorted(x for x in enc if x.startswith("images.image_")) == [f"images.image_{i}" for i in range(1, k + 1)]
            sizes.append(len(api))
        assert sizes == sorted(set(sizes)), (wid, sizes)      # cada imagem a menos tira nós do grafo


def test_every_loadimage_in_defaults_is_connected():
    for p in WF.glob("Qwen2.1 *.json"):
        wf = json.loads(p.read_text(encoding="utf-8"))
        used = {l[1] for l in wf["links"]}
        loose = [n["id"] for n in wf["nodes"] if n["type"] == "LoadImage" and n["id"] not in used]
        assert not loose, (p.name, loose)
