"""LoRA de restauração: prompt padrão, força, conflito com a Consistency e sumiço quando a LoRA está escondida."""
import yaml

from clefflow import loras as L
from clefflow.clef import Answer
from clefflow.store import Store

from test_agent import run  # noqa: F401
from test_e2e import _chain, api, png, start, upload, wait_event  # noqa: F401

RESTORE = "qwen-image-2.1-restore.safetensors"


class Picks:
    """Clef falso: responde o que está em `picks`; nas LoRAs sugeridas diz que não; no resto, a primeira opção."""
    mode = "stub"

    def __init__(self, picks):
        self.picks = picks

    async def decide(self, state, questions, images=None):
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q) or ("none" if q.startswith("lora_") and "none" in keys else keys[0])
            out[q] = Answer(pick, 0.9, {k: (0.9 if k == pick else 0.1 / max(1, len(keys) - 1)) for k in keys})
        return out


def names(stack):
    return [i["id"] for i in stack]


def test_restore_lora_is_in_the_default_library_and_in_the_installer_catalog(project):
    from clefflow.setup import catalog
    s = Store(project)
    lo = s.loras()["restore"]
    assert lo["file"] == RESTORE and lo["strength"] == 1.0 and "consistency" in lo["conflicts"] and not lo["auto"]
    assert any(e["file"] == RESTORE for e in catalog(s))


def test_default_prompt_uses_the_lora_at_full_strength_without_consistency_flymy_and_natexp(project):
    s = Store(project)
    lib = s.loras()
    preset = next(p for p in s.prompts("edit")["presets"] if p["id"] == "restaurar")
    st = L.effective_loras(lib, s.manifest("edit")["lora_base"], preset["loras"], [], "turbo")
    assert names(st) == ["restore"] and st[0]["strength"] == 1.0                 # a de consistência cedeu; realismo desligado
    cfg = L.merged_settings(lib, st, preset.get("settings"))
    assert cfg["scene_mp"] == 2.0 and cfg["resolution"] == 0 and cfg["ref_mp"] == "off"
    kinds = {o["id"]: o["text"] for v in preset["vars"] for o in v["options"]}
    assert kinds["photo"].startswith("Restore the photo: remove blur, noise and compression artifacts")
    assert kinds["print"].startswith("Restore the old photo: remove scratches, dust, fading and grain")


def test_blurry_prompt_halves_the_strength(project):
    s = Store(project)
    preset = next(p for p in s.prompts("edit")["presets"] if p["id"] == "restaurar_borrada")
    st = L.effective_loras(s.loras(), s.manifest("edit")["lora_base"], preset["loras"], [], "turbo")
    assert [(i["id"], i["strength"]) for i in st] == [("restore", 0.5)]


def test_hiding_the_lora_hides_its_prompts_and_the_other_prompts_stay(project):
    s = Store(project)
    ids = lambda: {p["id"] for p in s.active_presets("edit")}          # noqa: E731
    assert {"restaurar", "restaurar_borrada", "livre"} <= ids()
    s.custom_dir.mkdir(exist_ok=True)
    (s.custom_dir / "loras.yaml").write_text(yaml.dump({"loras": [{"id": "restore", "disabled": True}]}), encoding="utf-8")
    assert not {"restaurar", "restaurar_borrada"} & ids() and "livre" in ids() and "trocar_pessoas" in ids()


def test_restore_request_runs_edit_with_the_lora_and_the_standard_prompt(api, monkeypatch):
    monkeypatch.setattr("clefflow.runner.make_clef", lambda s: Picks({"preset": "restaurar", "var_restore_kind": "print"}))
    a = upload(api, "velha.png", png((120, 110, 100), (160, 120)))
    _, rid = start(api, "restaure esta foto antiga", [a["id"]], route="edit")
    wait_event(api, rid, lambda e: e["type"] == "done", timeout=20)
    (prompt,) = api.fake.prompts.values()
    chain = _chain(prompt)
    assert [c for c in chain if "restore" in c[0]] == [(RESTORE, 1.0, 1.0)]
    assert not [c for c in chain if "consistency" in c[0]]
    assert prompt["6"]["inputs"]["value"].startswith("Restore the old photo")       # só o prompt padrão, sem o texto do pedido
    assert prompt["40"]["inputs"]["megapixels"] == 2.0                              # a imagem 1 vai a cerca de 2 MP
