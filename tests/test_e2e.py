import base64
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clefflow.app import create_app


def png(color=(10, 120, 200), size=(80, 60), alpha=False):
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", size, color + ((255,) if alpha else ())).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def api(project, comfy):
    from clefflow.store import Store
    Store(project).save_settings({"comfyui": {"url": comfy.url}, "clef": {"mode": "mock"}})
    app = create_app(project)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        c.fake, c.pipe = comfy, app.state.pipe
        yield c


def upload(c, name="a.png", data=None):
    r = c.post("/api/upload", files={"file": (name, data or png(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()


def start(c, text, ids, route=None):
    cid = c.post("/api/conversations").json()["id"]
    rid = c.post(f"/api/conversations/{cid}/messages", json={"text": text, "image_ids": ids, "route": route}).json()["run_id"]
    return cid, rid


def wait_event(c, rid, pred, timeout=10):
    run = c.pipe.runs[rid]
    t0 = time.time()
    while time.time() - t0 < timeout:
        for e in list(run.events):
            if pred(e):
                return e
        if run.finished and not any(pred(e) for e in run.events):
            break
        time.sleep(0.05)
    raise AssertionError("evento não apareceu: " + json.dumps(run.events[-3:], ensure_ascii=False)[:600])


def types(c, rid):
    return [e["type"] for e in c.pipe.runs[rid].events]


def test_status_and_workflow_listing(api):
    s = api.get("/api/status").json()
    assert s["comfy"]["ok"] and s["clef"]["mode"] == "mock" and s["workflows"] == 15
    wfs = {w["id"]: w for w in api.get("/api/workflows").json()}
    assert wfs["bodyswap_turbo"]["required_images"] == 2 and len(wfs["bodyswap_turbo"]["presets"]) == 3
    assert api.get("/").status_code == 200


def test_simple_edit_end_to_end(api):
    a = upload(api)
    cid, rid = start(api, "troque a cor da camisa para vermelho", [a["id"]], route="edit_turbo")
    done = wait_event(api, rid, lambda e: e["type"] == "done")
    assert done["images"][0]["url"].startswith("/files/")
    assert api.get(done["images"][0]["url"]).status_code == 200
    # o que foi para o ComfyUI
    (prompt,) = api.fake.prompts.values()
    assert prompt["6"]["inputs"]["value"] == "troque a cor da camisa para vermelho"
    assert prompt["26"]["inputs"]["image"].startswith("clefflow/up_")
    assert "27" not in prompt and "33" not in prompt and "25" in prompt
    assert prompt["20"]["inputs"]["noise_seed"] != 1055939264985049        # seed randomizada
    conv = api.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] and conv["title"].startswith("troque a cor")
    assert conv["messages"][1]["images"] and "progress" not in [e["type"] for e in conv["messages"][1]["events"]]


def test_sse_replays_finished_run(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    time.sleep(0.1)
    with api.stream("GET", f"/api/runs/{rid}/events") as r:
        body = "".join(r.iter_text())
    assert '"type": "plan"' in body and '"type": "done"' in body and "event: end" in body


def test_text_to_image_when_no_images(api):
    cid, rid = start(api, "desenhe uma praia ao pôr do sol", [], route="t2i_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    assert prompt["30"]["inputs"]["value"] is True and "26" not in prompt
    assert prompt["6"]["inputs"]["value"] == "desenhe uma praia ao pôr do sol"


def test_follow_up_without_attachment_edits_last_result(api):
    a = upload(api)
    cid, rid = start(api, "primeiro", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    time.sleep(0.1)
    r = api.post(f"/api/conversations/{cid}/messages", json={"text": "agora deixe mais escuro", "image_ids": [], "route": "edit_turbo"})
    rid2 = r.json()["run_id"]
    ev = wait_event(api, rid2, lambda e: e["type"] in ("done", "error"))
    assert ev["type"] == "done"
    assert any(e["type"] == "status" and "último resultado" in e["text"] for e in api.pipe.runs[rid2].events)
    assert len(api.fake.prompts) == 2


def test_two_image_workflow_uploads_both_and_keeps_order(api):
    a, b = upload(api, "cena.png", png((1, 1, 1))), upload(api, "pessoa.png", png((9, 9, 9)))
    _, rid = start(api, "troque o corpo", [a["id"], b["id"]], route="bodyswap_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    names = {k: prompt[k]["inputs"]["image"] for k in ("26", "27")}
    assert names["26"] != names["27"]
    assert "42" not in prompt                              # LoadImage não conectado foi descartado
    assert "body_swap: start with <image1>" in prompt["6"]["inputs"]["value"]


def test_box_workflow_pauses_for_drawing_and_applies_boxes(api):
    a = upload(api)
    _, rid = start(api, "mova o objeto", [a["id"]], route="object_move_turbo")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "draw" and [t["key"] for t in need["tasks"]] == ["red", "green"]
    assert need["tasks"][0]["image_url"].startswith("/files/")
    assert not api.fake.prompts                                # nada foi para o ComfyUI ainda
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"],
                                             "value": {"red": [0.1, 0.2, 0.3, 0.2], "green": [0.6, 0.5, 0.2, 0.2]}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    (prompt,) = api.fake.prompts.values()
    mb = prompt["6"]["inputs"]
    assert (mb["src_x"], mb["src_y"], mb["src_w"], mb["src_h"]) == (0.1, 0.2, 0.3, 0.2)
    assert (mb["tgt_x"], mb["tgt_y"], mb["tgt_w"], mb["tgt_h"]) == (0.6, 0.5, 0.2, 0.2)
    assert "red box" in prompt["8"]["inputs"]["prompt"] or "red box" in prompt["8"]["inputs"]["prompt"].lower()


def test_empty_box_is_rejected(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="replace_redbox")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": {"red": [0.1, 0.1, 0.0, 0.0]}})
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "vazia" in err["message"]


def test_mask_workflow_becomes_alpha_cutout(api, project):
    """Workflow seu (em custom/) com entrada de máscara: o desenho vira o canal alfa da imagem enviada."""
    import yaml
    from clefflow.store import Store
    store = Store(project)
    m = yaml.safe_load((project / "defaults" / "registry" / "edit_turbo.yaml").read_text(encoding="utf-8"))
    m.update(id="com_mascara", title="Com máscara")
    m["images"][0]["mask"] = True
    (project / "custom" / "registry" / "com_mascara.yaml").write_text(yaml.dump(m), encoding="utf-8")
    store.save_prompts("com_mascara", {"presets": [{"id": "livre", "name": "Livre", "text": "{request}"}]})
    a = upload(api, data=png((50, 50, 50), (40, 40)))
    _, rid = start(api, "um chapéu azul", [a["id"]], route="com_mascara")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["tasks"][0]["kind"] == "mask"
    m = Image.new("L", (40, 40), 0)
    for x in range(10, 20):
        for y in range(10, 20):
            m.putpixel((x, y), 255)
    buf = io.BytesIO()
    m.save(buf, format="PNG")
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": {
        "mask_image_1": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    up = Image.open(io.BytesIO(next(v for k, v in api.fake.uploads.items())))
    assert up.mode == "RGBA" and up.getpixel((15, 15))[3] == 0 and up.getpixel((30, 30))[3] == 255


def test_subgraph_workflows_run_end_to_end(api):
    """t2i (texto -> imagem) e remoção de fundo: workflows oficiais com subgraph, achatados na hora de rodar."""
    _, rid = start(api, "um gato astronauta", [], route="t2i")
    wait_event(api, rid, lambda e: e["type"] == "done")
    p1 = list(api.fake.prompts.values())[0]
    assert p1["459:474"]["inputs"]["on_false"] == "um gato astronauta" and "459:471" not in p1
    seed1 = p1["459:458"]["inputs"]["seed"]
    a = upload(api)
    _, rid = start(api, "tire o fundo", [a["id"]], route="background_removal")
    wait_event(api, rid, lambda e: e["type"] == "done")
    p2 = list(api.fake.prompts.values())[1]
    assert p2["459:474"]["inputs"]["prompt"] == "Remove the background, and output a PNG image"
    assert p2["470"]["inputs"]["image"].startswith("clefflow/up_") and "472" not in p2
    assert p2["459:458"]["inputs"]["seed"] != 992618384254740 and seed1 != 0           # semente sorteada (registro: set random)


def test_recipe_chains_two_workflows_and_feeds_previous_result(api):
    ids = [upload(api, f"i{i}.png", png((i * 30, 0, 0))) ["id"] for i in (1, 2, 3)]
    _, rid = start(api, "troque a pessoa e o fundo", ids, route="recipe__trocar_pessoa_e_fundo")
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")        # passo 2 pede as caixas
    assert [t["key"] for t in need["tasks"]] == ["green", "red"]
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": {
        "green": [0.4, 0.4, 0.3, 0.5], "red": [0.2, 0.1, 0.5, 0.8]}})
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert [e["index"] for e in api.pipe.runs[rid].events if e["type"] == "step_done"] == [0, 1]
    assert len(api.fake.prompts) == 2 and len(api.fake.uploads) == 4
    second = list(api.fake.prompts.values())[1]
    assert second["5"]["inputs"]["image"] != second["6"]["inputs"]["image"]
    assert second["7"]["inputs"]["box_x"] == 0.4 and second["8"]["inputs"]["box_w"] == 0.5


def test_ambiguous_route_asks_user(api, project):
    from clefflow.store import Store
    Store(project).save_settings({"clef": {"ask_below": 0.999}})
    a, b = upload(api), upload(api)
    _, rid = start(api, "algo vago", [a["id"], b["id"]])
    need = wait_event(api, rid, lambda e: e["type"] == "needs_input")
    assert need["kind"] == "choose" and len(need["options"]) == 3
    api.post(f"/api/runs/{rid}/input", json={"input_id": need["input_id"], "value": "headswap_turbo"})
    wait_event(api, rid, lambda e: e["type"] == "plan")
    plan = next(e for e in api.pipe.runs[rid].events if e["type"] == "plan")
    assert plan["steps"][0]["workflow"] == "headswap_turbo"


def test_comfy_execution_error_is_reported(api):
    api.fake.fail_node = "34"
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_quality")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "CUDA out of memory" in err["message"] and "nó 34" in err["message"]


def test_comfy_validation_error_is_readable(api):
    a = upload(api)
    # força um grafo inválido: remove o nó de saída da lista de outputs do manifesto
    import yaml
    store = api.pipe.store
    m = yaml.safe_load((store.defaults_dir / "registry" / "edit_turbo.yaml").read_text(encoding="utf-8"))
    m["outputs"] = [999]
    (store.custom_dir / "registry" / "edit_turbo.yaml").write_text(yaml.dump(m), encoding="utf-8")   # o seu vale mais
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "999" in err["message"]


def test_clef_down_gives_actionable_error(api, project):
    from clefflow.store import Store
    Store(project).save_settings({"clef": {"mode": "systemone", "url": "http://127.0.0.1:9"}})
    a, b = upload(api), upload(api)
    _, rid = start(api, "x", [a["id"], b["id"]])
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "Clef" in err["message"] and "127.0.0.1:9" in err["message"]


def test_too_few_images_for_forced_workflow(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="headswap_turbo")
    err = wait_event(api, rid, lambda e: e["type"] == "error")
    assert "mais imagens" in err["message"]


def test_cancel_unblocks_waiting_run(api):
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="replace_redbox")
    wait_event(api, rid, lambda e: e["type"] == "needs_input")
    api.post(f"/api/runs/{rid}/cancel")
    wait_event(api, rid, lambda e: e["type"] == "error")
    t0 = time.time()
    while not api.pipe.runs[rid].finished and time.time() - t0 < 5:
        time.sleep(0.05)
    assert api.pipe.runs[rid].finished
    # o lock global foi liberado: uma nova execução termina normalmente
    _, rid2 = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid2, lambda e: e["type"] == "done")


def test_free_vram_hooks(api, project):
    from clefflow.store import Store
    Store(project).save_settings({"comfyui": {"free_vram_before_clef": True, "free_vram_after_run": True}})
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert api.fake.freed == 2


def test_editing_prompts_goes_to_custom_and_never_touches_defaults(api):
    store = api.pipe.store
    before = {p: p.read_bytes() for p in store.defaults_dir.rglob("*") if p.is_file()}
    w = next(x for x in api.get("/api/workflows").json() if x["id"] == "bodyswap_turbo")
    n0 = len(w["presets"])
    assert w["source"] == "default" and w["customized"] is False
    w["presets"][0]["text"] = "<orbit> novo texto {request}"
    w["presets"].append({"id": "custom", "name": "Meu prompt", "when": "sempre", "text": "meu {request}", "rewrite": False})
    r = api.put("/api/workflows/bodyswap_turbo", json={"enabled": True, "description": "nova descrição",
                                                       "active": ["custom"], "default": "custom", "presets": w["presets"]})
    assert r.status_code == 200, r.text
    again = next(x for x in api.get("/api/workflows").json() if x["id"] == "bodyswap_turbo")
    assert again["description"] == "nova descrição" and again["active"] == ["custom"] and len(again["presets"]) == n0 + 1
    assert again["customized"] is True and again["source"] == "default"
    clef_text = (store.custom_dir / "clef_prompt.yaml").read_text(encoding="utf-8")
    assert "nova descrição" in clef_text and "background_removal" not in clef_text          # só o que mudou
    assert "meu {request}" in (store.custom_dir / "prompts" / "bodyswap_turbo.yaml").read_text(encoding="utf-8")
    assert {p: p.read_bytes() for p in store.defaults_dir.rglob("*") if p.is_file()} == before
    bad = api.put("/api/workflows/bodyswap_turbo", json={"enabled": True, "description": "", "presets": [{"id": "Inválido!", "text": "x"}]})
    assert bad.status_code == 422
    # restaurar o padrão apaga só as suas edições
    assert api.delete("/api/workflows/bodyswap_turbo").json() == {"result": "reset"}
    back = next(x for x in api.get("/api/workflows").json() if x["id"] == "bodyswap_turbo")
    assert back["customized"] is False and back["description"] != "nova descrição" and len(back["presets"]) == n0
    assert not (store.custom_dir / "prompts" / "bodyswap_turbo.yaml").exists()
    assert not (store.custom_dir / "clef_prompt.yaml").exists()


def test_recipes_roundtrip_and_validation(api):
    cfg = api.get("/api/clef-config").json()
    cfg["recipes"].append({"id": "so_cabeca", "name": "Só cabeça", "description": "x", "enabled": True,
                           "steps": [{"workflow": "headswap_turbo", "inputs": {"image_1": "user:1"}}]})
    assert api.put("/api/clef-config", json={"recipes": cfg["recipes"], "instructions": "novas instruções"}).status_code == 200
    assert len(api.get("/api/clef-config").json()["recipes"]) == 2
    bad = api.put("/api/clef-config", json={"recipes": [{"id": "x", "steps": [{"workflow": "nao_existe"}]}]})
    assert bad.status_code == 422


def test_doctor_endpoint_reports_all_ok(api):
    r = api.post("/api/doctor").json()
    assert len(r) == 15 and all(x["ok"] for x in r)


def test_upload_rejects_non_image(api):
    r = api.post("/api/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


GB = 2 ** 30


def _spy_clef(monkeypatch, health):
    from clefflow import runner
    from clefflow.clef import MockClef
    calls = []

    class Spy(MockClef):
        mode = "systemone"

        async def health(self):
            return health

        async def decide(self, *a, **k):
            calls.append("decide")
            return await super().decide(*a, **k)

        async def unload(self):
            calls.append("unload")
            return True

    monkeypatch.setattr(runner, "make_clef", lambda settings: Spy())
    return calls


def _stats(vram_total=24, vram_free=24):
    return {"system": {"ram_total": 32 * GB, "ram_free": 20 * GB},
            "devices": [{"vram_total": vram_total * GB, "vram_free": vram_free * GB}]}


def test_clef_unloaded_when_it_does_not_fit(api, monkeypatch):
    calls = _spy_clef(monkeypatch, {"loaded": True, "device": "cuda", "vram": {"allocated_gb": 19.0}})
    api.fake.stats = _stats(24, 3)
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert calls.count("unload") == 1 and calls.index("unload") > max(i for i, c in enumerate(calls) if c == "decide")
    mem = [e for e in api.pipe.runs[rid].events if e["type"] == "memory"]
    assert mem and "Descarreguei o Clef" in mem[0]["text"]


def test_clef_kept_when_memory_is_enough(api, monkeypatch):
    calls = _spy_clef(monkeypatch, {"loaded": True, "device": "cuda", "vram": {"allocated_gb": 8.0}})
    api.fake.stats = _stats(48, 30)       # GPU grande: sobram 38 GB para o ComfyUI
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert "unload" not in calls
    assert any("continua carregado" in e["text"] for e in api.pipe.runs[rid].events if e["type"] == "memory")


def test_policy_never_and_always(api, project, monkeypatch):
    from clefflow.store import Store
    health = {"loaded": True, "device": "cuda", "vram": {"allocated_gb": 19.0}}
    api.fake.stats = _stats(24, 3)
    Store(project).save_settings({"clef": {"unload_policy": "never"}})
    calls = _spy_clef(monkeypatch, health)
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert "unload" not in calls
    Store(project).save_settings({"clef": {"unload_policy": "always"}})
    api.fake.stats = _stats(80, 70)
    calls = _spy_clef(monkeypatch, health)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert calls.count("unload") == 1


def test_comfy_freed_when_clef_cannot_load(api, monkeypatch):
    _spy_clef(monkeypatch, {"loaded": False, "device": "cuda"})
    api.fake.stats = _stats(24, 2)        # ComfyUI ocupando a GPU
    a = upload(api)
    _, rid = start(api, "x", [a["id"]], route="edit_turbo")
    wait_event(api, rid, lambda e: e["type"] == "done")
    assert api.fake.freed == 1
    assert any("Liberei os modelos do ComfyUI" in e["text"] for e in api.pipe.runs[rid].events if e["type"] == "memory")
