"""Leitura e escrita da configuração em disco (YAML), sempre com escrita atômica.

Duas camadas, de propósito:

  defaults/   o que vem no repositório (rastreado pelo git)  — nunca é escrito pelo app
  custom/     o que é seu (ignorado pelo git)                 — tudo que você edita na interface vai para cá

Dentro de cada camada há as mesmas coisas:
  workflows/          arquivos .json do ComfyUI
  registry/<id>.yaml  ligação técnica do workflow (nós, imagens, saída); raramente muda
  prompts/<id>.yaml   biblioteca de prompts daquele workflow
  clef_prompt.yaml    o "prompt do Clef": instruções, descrição de cada workflow, quais prompts
                      ele pode usar e os casos de uso (receitas)

O que está em custom/ vale mais do que o que está em defaults/ (mesmo id = o seu substitui o padrão).
Ao salvar pela interface só vai para custom/ o que difere do padrão; assim atualizar o repositório
continua trazendo as novidades dos padrões que você não mexeu, e "restaurar padrão" é só apagar o seu.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,60}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 7860},
    "comfyui": {"url": "http://127.0.0.1:8188", "upload_subfolder": "clefflow", "timeout_s": 1800,
                "free_vram_before_clef": False, "free_vram_after_run": False, "path": ""},
    "setup": {"dismissed": False},
    "clef": {"mode": "systemone", "url": "http://127.0.0.1:8321", "path": "/v1/systemone",
             "model": "clef", "timeout_s": 120, "max_images": 4, "max_steps": 3,
             "ask_below": 0.45, "api_key": "",
             "unload_policy": "auto", "need_vram_gb": 16, "need_ram_gb": 10, "margin_gb": 2,
             "make_room_for_clef": True, "clef_vram_gb": 20},
    "writer": {"enabled": False, "url": "http://127.0.0.1:8123/v1", "model": "", "timeout_s": 120,
               "system": ("You rewrite a user's request into one short, precise English instruction for the "
                          "Qwen-Image-2.1 editing model. Keep names, numbers and positions. Output only the "
                          "instruction, no quotes.")},
}


class _Dumper(yaml.SafeDumper):
    pass


def _str_repr(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_repr)


def dump_yaml(data: Any, header: str = "") -> str:
    body = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=110)
    return (header.rstrip() + "\n\n" if header else "") + body


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class StoreError(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.defaults_dir = self.root / "defaults"
        self.custom_dir = self.root / "custom"
        self.data_dir = self.root / "data"

    # --------------------------------------------------------------- layout
    def layers(self, kind: str) -> list[Path]:
        """Pastas de um tipo (workflows, registry, prompts), da mais forte (custom) para a mais fraca."""
        return [self.custom_dir / kind, self.defaults_dir / kind]

    def ensure_custom_dirs(self):
        for kind in ("workflows", "registry", "prompts"):
            (self.custom_dir / kind).mkdir(parents=True, exist_ok=True)

    def _find(self, kind: str, name: str) -> Path | None:
        for d in self.layers(kind):
            p = d / name
            if p.is_file():
                try:
                    p.resolve().relative_to(d.resolve())
                except ValueError:
                    continue        # nome tentando sair da pasta (..)
                return p
        return None

    @staticmethod
    def _read(p: Path | None) -> dict:
        if not p or not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    # ------------------------------------------------------------ settings
    @property
    def settings_path(self) -> Path:
        return self.custom_dir / "settings.yaml"

    def settings(self) -> dict:
        return deep_merge(DEFAULT_SETTINGS, self._read(self.settings_path))

    def save_settings(self, patch: dict) -> dict:
        new = deep_merge(self.settings(), patch)
        atomic_write(self.settings_path,
                     dump_yaml(new, "# Configurações do ClefFlow (também editáveis na interface)."))
        return new

    # ----------------------------------------------------------- manifests
    def manifests(self) -> dict[str, dict]:
        files = [(layer, p) for layer, d in (("default", self.defaults_dir / "registry"),
                                             ("custom", self.custom_dir / "registry"))
                 for p in sorted(d.glob("*.yaml"))]
        sig = tuple((layer, p.name, p.stat().st_mtime_ns) for layer, p in files)
        if getattr(self, "_mf_sig", None) != sig:
            out = {}
            for layer, p in files:                # custom vem depois e substitui o padrão do mesmo id
                m = self._read(p)
                if m.get("id"):
                    m["source"] = layer
                    out[m["id"]] = m
            self._mf_sig, self._mf = sig, out
        return copy.deepcopy(self._mf)

    def manifest(self, wid: str) -> dict:
        m = self.manifests().get(wid)
        if not m:
            raise StoreError(f"Workflow desconhecido: {wid}")
        return m

    def workflow_path(self, wid: str) -> Path:
        m = self.manifest(wid)
        path = self._find("registry", f"{wid}.api.json") or self._find("workflows", str(m.get("file", "")))
        if not path:
            raise StoreError(f"Arquivo do workflow não encontrado: {m.get('file')} "
                             f"(procurei em custom/workflows e defaults/workflows)")
        return path

    def workflow_json(self, wid: str) -> dict:
        return json.loads(self.workflow_path(wid).read_text(encoding="utf-8"))

    # ------------------------------------------------------------- prompts
    def _prompts_raw(self, wid: str) -> dict:
        data = self._read(self._find("prompts", f"{wid}.yaml"))
        data.setdefault("presets", [])
        if not data.get("default") and data["presets"]:
            data["default"] = data["presets"][0]["id"]
        return data

    def prompts(self, wid: str) -> dict:
        return self._prompts_raw(wid)

    def prompts_customized(self, wid: str) -> bool:
        return (self.custom_dir / "prompts" / f"{wid}.yaml").exists()

    def save_prompts(self, wid: str, data: dict) -> dict:
        self.manifest(wid)
        presets = []
        seen = set()
        for pr in data.get("presets", []):
            pid = str(pr.get("id", "")).strip()
            if not ID_RE.match(pid):
                raise StoreError(f"Id de prompt inválido: '{pid}'. Use letras minúsculas, números e _.")
            if pid in seen:
                raise StoreError(f"Id de prompt repetido: {pid}")
            seen.add(pid)
            presets.append({"id": pid, "name": str(pr.get("name") or pid),
                            "when": str(pr.get("when") or ""), "text": str(pr.get("text") or ""),
                            "rewrite": bool(pr.get("rewrite", False))})
        if not presets:
            raise StoreError("Cada workflow precisa de pelo menos um prompt.")
        default = data.get("default") if data.get("default") in seen else presets[0]["id"]
        out = {"default": default, "presets": presets}
        target = self.custom_dir / "prompts" / f"{wid}.yaml"
        base = self._read(self.defaults_dir / "prompts" / f"{wid}.yaml")
        if base and self._norm_prompts(base) == out:
            target.unlink(missing_ok=True)          # igual ao padrão: não precisa de cópia sua
        else:
            atomic_write(target, dump_yaml(out, f"# Seus prompts do workflow '{wid}'. "
                                                f"Use {{request}} onde o pedido do usuário entra."))
        return out

    @staticmethod
    def _norm_prompts(d: dict) -> dict:
        presets = [{"id": str(p["id"]), "name": str(p.get("name") or p["id"]), "when": str(p.get("when") or ""),
                    "text": str(p.get("text") or ""), "rewrite": bool(p.get("rewrite", False))}
                   for p in d.get("presets", [])]
        default = d.get("default") if any(p["id"] == d.get("default") for p in presets) else (
            presets[0]["id"] if presets else None)
        return {"default": default, "presets": presets}

    # ---------------------------------------------------------- clef prompt
    @staticmethod
    def _norm_entry(cfg: dict) -> dict:
        e = {"enabled": bool(cfg.get("enabled", True)), "description": str(cfg.get("description") or "")}
        if cfg.get("prompts") is not None:
            e["prompts"] = list(cfg["prompts"])
        return e

    def _clef_layer(self, p: Path) -> dict:
        d = self._read(p)
        out: dict[str, Any] = {}
        if "instructions" in d:
            out["instructions"] = str(d.get("instructions") or "")
        if d.get("workflows"):
            out["workflows"] = {w: self._norm_entry(c or {}) for w, c in d["workflows"].items()}
        if "recipes" in d:
            out["recipes"] = d.get("recipes") or []
        return out

    def clef_config(self) -> dict:
        base = self._clef_layer(self.defaults_dir / "clef_prompt.yaml")
        mine = self._clef_layer(self.custom_dir / "clef_prompt.yaml")
        known = self.manifests()
        wfs = {**(base.get("workflows") or {}), **(mine.get("workflows") or {})}
        return {"instructions": mine.get("instructions", base.get("instructions", "")),
                "workflows": {w: c for w, c in wfs.items() if w in known},
                "recipes": copy.deepcopy(mine["recipes"] if "recipes" in mine else base.get("recipes", []))}

    def clef_customized(self, wid: str) -> bool:
        return wid in (self._clef_layer(self.custom_dir / "clef_prompt.yaml").get("workflows") or {})

    def _write_clef_custom(self, mine: dict):
        p = self.custom_dir / "clef_prompt.yaml"
        if not mine:
            p.unlink(missing_ok=True)
            return
        atomic_write(p, dump_yaml(mine, "# Suas mudanças no prompt do Clef (só o que difere de defaults/clef_prompt.yaml).\n"
                                        "# Editável aqui ou na interface."))

    def save_clef_config(self, data: dict) -> dict:
        known = self.manifests()
        wfs = {}
        for wid, cfg in (data.get("workflows") or {}).items():
            if wid not in known:
                raise StoreError(f"Workflow desconhecido: {wid}")
            entry = {"enabled": bool(cfg.get("enabled", True)),
                     "description": str(cfg.get("description") or "")}
            if cfg.get("prompts") is not None:
                valid = {p["id"] for p in self.prompts(wid)["presets"]}
                bad = [x for x in cfg["prompts"] if x not in valid]
                if bad:
                    raise StoreError(f"Prompts inexistentes em {wid}: {', '.join(bad)}")
                entry["prompts"] = list(cfg["prompts"])
            wfs[wid] = entry
        recipes = []
        rids = set()
        for r in data.get("recipes") or []:
            rid = str(r.get("id", "")).strip()
            if not ID_RE.match(rid):
                raise StoreError(f"Id de caso de uso inválido: '{rid}'.")
            if rid in rids:
                raise StoreError(f"Id de caso de uso repetido: {rid}")
            rids.add(rid)
            steps = []
            for s in r.get("steps") or []:
                if s.get("workflow") not in known:
                    raise StoreError(f"Caso de uso '{rid}' aponta para workflow inexistente: {s.get('workflow')}")
                st: dict[str, Any] = {"workflow": s["workflow"]}
                if s.get("prompt"):
                    st["prompt"] = s["prompt"]
                if s.get("inputs"):
                    st["inputs"] = dict(s["inputs"])
                steps.append(st)
            if not steps:
                raise StoreError(f"O caso de uso '{rid}' precisa de pelo menos um passo.")
            recipes.append({"id": rid, "name": str(r.get("name") or rid), "enabled": bool(r.get("enabled", True)),
                            "description": str(r.get("description") or ""), "steps": steps})
        out = {"instructions": str(data.get("instructions") or ""), "workflows": wfs, "recipes": recipes}

        base = self._clef_layer(self.defaults_dir / "clef_prompt.yaml")
        base_w = base.get("workflows") or {}
        mine: dict[str, Any] = {}
        if out["instructions"] != base.get("instructions", ""):
            mine["instructions"] = out["instructions"]
        diff = {w: e for w, e in wfs.items() if e != base_w.get(w)}
        if diff:
            mine["workflows"] = diff
        if recipes != (base.get("recipes") or []):
            mine["recipes"] = recipes
        # entradas suas de workflows que não foram enviadas agora continuam como estavam
        old = self._clef_layer(self.custom_dir / "clef_prompt.yaml").get("workflows") or {}
        for w, e in old.items():
            if w in known and w not in wfs:
                mine.setdefault("workflows", {})[w] = e
        self._write_clef_custom(mine)
        return out

    # ---------------------------------------------------- restaurar / remover
    def reset_workflow(self, wid: str) -> str:
        """Padrão: descarta as suas edições (prompts e descrição) e volta ao que vem no repositório.
        Workflow seu (sem padrão): tira da lista (os arquivos .json continuam na pasta)."""
        m = self.manifest(wid)
        (self.custom_dir / "prompts" / f"{wid}.yaml").unlink(missing_ok=True)
        mine = self._clef_layer(self.custom_dir / "clef_prompt.yaml")
        (mine.get("workflows") or {}).pop(wid, None)
        if not mine.get("workflows"):
            mine.pop("workflows", None)
        if m.get("source") == "custom" and not (self.defaults_dir / "registry" / f"{wid}.yaml").exists():
            (self.custom_dir / "registry" / f"{wid}.yaml").unlink(missing_ok=True)
            (self.custom_dir / "registry" / f"{wid}.api.json").unlink(missing_ok=True)
            self._write_clef_custom(mine)
            return "removed"
        self._write_clef_custom(mine)
        return "reset"

    # -------------------------------------------------------------- merged
    def active_presets(self, wid: str) -> list[dict]:
        lib = self.prompts(wid)
        sel = (self.clef_config()["workflows"].get(wid) or {}).get("prompts")
        presets = lib["presets"]
        if sel is None:
            return presets
        chosen = [p for p in presets if p["id"] in sel]
        return chosen or [p for p in presets if p["id"] == lib.get("default")] or presets[:1]

    def enabled_workflows(self) -> dict[str, dict]:
        cfg = self.clef_config()["workflows"]
        out = {}
        for wid, m in self.manifests().items():
            if (cfg.get(wid) or {}).get("enabled", True):
                out[wid] = m
        return out

    def describe(self, wid: str) -> str:
        c = (self.clef_config()["workflows"].get(wid) or {}).get("description")
        return c or self.manifest(wid).get("title", wid)

    def enabled_recipes(self) -> list[dict]:
        usable = self.enabled_workflows()
        return [r for r in self.clef_config()["recipes"]
                if r.get("enabled", True) and all(s.get("workflow") in usable for s in r.get("steps", []))]
