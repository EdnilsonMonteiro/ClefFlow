"""API web e arquivos estáticos do PickFlow."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from pathlib import Path

from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__
from .addons import HOOKS, AddonError, AddonManager, new_addon
from .clef import ClefError
from .comfy import ComfyClient, ComfyError
from . import envfile, hf, importer
from . import llm as llmcfg
from .doctor import validate_all
from .runner import Pipeline, make_clef
from .setup import SetupService
from .router import required_images
from .storage import ConvStore, ImageStore
from .splats import view as splat_view
from .store import Store, StoreError
from .recipe_merge import apply_merge, merge_recipes, score_pairs

ROOT = Path(__file__).resolve().parent.parent


class MessageIn(BaseModel):
    text: str = ""
    image_ids: list[str] = []
    route: str | None = None


class AnswerIn(BaseModel):
    input_id: str
    value: object | None = None


class WorkflowIn(BaseModel):
    enabled: bool = True
    description: str = ""
    active: list[str] | None = None
    default: str | None = None
    presets: list[dict]


class SetupInstallIn(BaseModel):
    ids: list[str] = []
    nodes: bool = False
    overwrite_nodes: bool = False


class SimilarityIn(BaseModel):
    ids: list[str] | None = None


class MergeIn(BaseModel):
    ids: list[str]


class MergeApplyIn(BaseModel):
    recipe: dict
    replaces: list[str]
    mode: str = "disable"


class ClefConfigIn(BaseModel):
    instructions: str | None = None
    recipes: list[dict] | None = None


def create_app(root: Path = ROOT) -> FastAPI:
    hf.load_env(root)                         # .env da pasta do PickFlow (HF_TOKEN etc.)
    store = Store(root)
    store.ensure_custom_dirs()
    images = ImageStore(store.data_dir)
    convs = ConvStore(store.data_dir)
    app = FastAPI(title="PickFlow", version=__version__)
    addons = AddonManager(store, app)
    pipe = Pipeline(store, images, convs, addons)
    setup = SetupService(store, pipe)
    app.state.store, app.state.pipe, app.state.setup, app.state.addons = store, pipe, setup, addons
    addons.load_all()                         # addons/<nome>/addon.py: rotas, ganchos e ações próprios

    loopback = {"localhost", "127.0.0.1", "[::1]", "::1"}

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        """O PickFlow roda na sua máquina: recusa páginas de outros sites que tentem usar o servidor
        (requisição com Origin de outro endereço) e nomes de host estranhos (DNS rebinding)."""
        if store.settings()["server"]["host"] in ("127.0.0.1", "localhost", "::1"):
            host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
                else (request.headers.get("host") or "").split("]")[0] + "]"
            if host not in loopback:
                return JSONResponse({"detail": "Acesse o PickFlow por http://127.0.0.1:<porta>."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(StoreError)
    async def _store_err(_req, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # ------------------------------------------------------------- status
    @app.get("/api/status")
    async def status():
        s = store.settings()
        comfy = await (await pipe.comfy()).ping()
        detected = None
        if not comfy["ok"]:
            detected = await ComfyClient.detect(s["comfyui"]["url"])
        clef = await make_clef(s).ping()
        try:
            setup_summary = await setup.summary() if comfy["ok"] else None
        except Exception:  # noqa: BLE001  (o resumo é um enfeite: nunca derruba o status)
            setup_summary = None
        return {"version": __version__, "comfy": comfy, "comfy_detected_url": detected, "clef": clef,
                "workflows": len(store.manifests()), "enabled": len(store.enabled_workflows()),
                "custom_dir": str(store.custom_dir), "setup": setup_summary}

    @app.post("/api/comfy/test")
    async def comfy_test(body: dict | None = None):
        """Teste rápido do endereço do ComfyUI (só isto: não consulta o Clef nem os arquivos instalados)."""
        url = str((body or {}).get("url") or store.settings()["comfyui"]["url"]).strip()
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
            return {"ok": False, "url": url, "error": "O endereço precisa começar com http:// ou https:// (ex.: http://127.0.0.1:8000)."}
        res = await ComfyClient(url, store.data_dir).ping()
        if res["ok"]:
            st = res.pop("stats", {}) or {}
            dev = (st.get("devices") or [{}])[0]
            res["version"] = (st.get("system") or {}).get("comfyui_version")
            res["device"] = dev.get("name")
            return res
        res.pop("stats", None)
        res["detected_url"] = await ComfyClient.detect(url)
        return res

    @app.get("/api/settings")
    async def get_settings():
        return store.settings()

    @app.put("/api/settings")
    async def put_settings(patch: dict):
        new = store.save_settings(patch)
        pipe._comfy = None
        return new

    # ----------------------------------------------- LLM: provedores e chaves
    @app.get("/api/llm")
    async def llm_info():
        """Provedores configurados, se cada chave existe no .env (a chave nunca sai daqui) e modelos prontos para acrescentar."""
        w = store.settings()["writer"]
        provs = [{**p, "key_set": envfile.is_set(p["key_env"])} for p in llmcfg.providers(w)]
        cur = llmcfg.resolve(w)
        return {"providers": provs, "active": {"provider": cur["provider"], "model": cur["model"]} if cur.get("provider") else {},
                "presets": llmcfg.PRESETS, "env_file": str(root / ".env"), "hf_token_set": envfile.is_set("HF_TOKEN")}

    @app.put("/api/env")
    async def env_put(body: dict):
        """Grava (ou, com valor vazio, apaga) uma chave de API no .env. Só HF_TOKEN e variáveis LLM_*."""
        try:
            envfile.set_var(root, str(body.get("name") or ""), str(body.get("value") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True, "set": envfile.is_set(str(body["name"]))}

    @app.post("/api/llm/test")
    async def llm_test(body: dict):
        """Testa um provedor e modelo (do formulário, ainda sem salvar) com uma pergunta de uma palavra."""
        w = dict(store.settings()["writer"])
        p = body.get("provider") if isinstance(body.get("provider"), dict) else None
        if p:
            w["providers"] = [p]
        res = llmcfg.resolve(w, None, str(body.get("model") or "") if body.get("model") is not None else None)
        return await llmcfg.test(res)

    # -------------------------------------------------- primeiros passos
    @app.get("/api/setup")
    async def setup_get(refresh: bool = False):
        return await setup.overview(refresh)

    @app.get("/api/setup/probe")
    async def setup_probe():
        return await setup.probe()

    @app.put("/api/setup/path")
    async def setup_path(body: dict):
        return setup.set_path(str(body.get("path") or ""))

    @app.post("/api/setup/dismiss")
    async def setup_dismiss(body: dict):
        setup.dismiss(bool(body.get("value", True)))
        return {"ok": True}

    @app.post("/api/setup/install")
    async def setup_install(body: SetupInstallIn):
        return await setup.install(body.ids, body.nodes, body.overwrite_nodes)

    @app.get("/api/setup/jobs")
    async def setup_jobs():
        return setup.jobs()

    @app.post("/api/setup/cancel")
    async def setup_cancel():
        setup.cancel()
        return {"ok": True}

    # ---------------------------------------------------------- workflows
    def workflow_view(wid: str, m: dict, cfg: dict) -> dict:
        lib = store.prompts(wid)
        c = cfg["workflows"].get(wid) or {}
        return {"id": wid, "title": m.get("title", wid), "group": m.get("group", ""), "file": m.get("file", ""), "kind": m.get("kind", "workflow"), "action": m.get("action", ""),
                "note": m.get("note", ""), "internal": bool(m.get("internal")), "images": m.get("images", []),
                "params": m.get("params", []),
                "modes": [{"id": k, "title": v.get("title", k), "when": v.get("when", "")}
                          for k, v in (m.get("modes") or {}).items()],
                "knobs": sorted(set((m.get("knobs") or {}).keys()) | {k for v in (m.get("modes") or {}).values()
                                                                      for k in (v.get("knobs") or {})}),
                "lora_chain": bool(m.get("lora_chain")),
                "required_images": required_images(m), "enabled": c.get("enabled", True),
                "description": c.get("description", ""), "active": c.get("prompts"),
                "default": lib.get("default"), "presets": lib["presets"],
                "source": m.get("source", "default"),
                "customized": store.prompts_customized(wid) or store.clef_customized(wid)}

    @app.get("/api/workflows")
    async def workflows():
        cfg = store.clef_config()
        return [workflow_view(wid, m, cfg) for wid, m in store.manifests().items()]

    @app.put("/api/workflows/{wid}")
    async def put_workflow(wid: str, body: WorkflowIn):
        store.manifest(wid)
        lib = store.save_prompts(wid, {"default": body.default, "presets": body.presets})
        cfg = store.clef_config()
        entry = {"enabled": body.enabled, "description": body.description}
        if body.active is not None:
            entry["prompts"] = [p for p in body.active if p in {x["id"] for x in lib["presets"]}]
        cfg["workflows"][wid] = entry
        store.save_clef_config(cfg)
        return workflow_view(wid, store.manifest(wid), store.clef_config())

    @app.delete("/api/workflows/{wid}")
    async def reset_workflow(wid: str):
        """Padrão: volta ao que vem no repositório. Seu: tira da lista (o .json fica na pasta)."""
        return {"result": store.reset_workflow(wid)}

    # --------------------------------------------------------------- LoRAs
    @app.get("/api/loras")
    async def loras_list():
        """Biblioteca de LoRAs + arquivos de LoRA que o ComfyUI tem e ainda não têm descrição."""
        lib = store.loras()
        files: list[str] | None = None
        try:
            oi = await (await pipe.comfy()).object_info()
            from .converter import widget_list
            for w in widget_list(oi.get("LoraLoader", {})):
                if w.name == "lora_name":
                    files = [o for o in w.options if isinstance(o, str)]
        except Exception:  # noqa: BLE001
            files = None
        base = lambda n: str(n).replace("\\", "/").rsplit("/", 1)[-1].lower()      # noqa: E731
        have = {base(f) for f in files or []}
        known = {base(lo["file"]) for lo in lib.values()}
        items = [{**lo, "installed": (base(lo["file"]) in have) if files is not None else None} for lo in lib.values()]
        unlisted = [f for f in (files or []) if base(f) not in known]
        stack_ids = {i["id"] for m in store.manifests().values() for i in (m.get("lora_base") or [])
                     if isinstance(i, dict)}
        return {"loras": items, "unlisted": unlisted, "comfy_ok": files is not None,
                "workflows": [wid for wid, m in store.manifests().items() if m.get("lora_chain")],
                "base": sorted(stack_ids), "hidden": store.hidden_loras()}

    @app.put("/api/loras/{lid}")
    async def loras_put(lid: str, body: dict):
        return store.save_lora({**body, "id": lid})

    @app.post("/api/loras/hf/inspect")
    async def loras_hf_inspect(body: dict):
        """Link do Hugging Face -> arquivos .safetensors que dá para baixar."""
        try:
            ref = hf.parse(str(body.get("url") or ""))
            if ref["path"]:
                if not ref["path"].lower().endswith(".safetensors"):
                    raise hf.HFError("O link aponta para um arquivo que não é .safetensors.")
                files = [{"path": ref["path"], "name": ref["path"].rsplit("/", 1)[-1], "size": None,
                          "url": hf.file_url(ref["repo"], ref["rev"], ref["path"])}]
            else:
                files = await hf.list_safetensors(ref["repo"], ref["rev"], ref.get("dir") or "")
                if not files:
                    raise hf.HFError("Não achei nenhum arquivo .safetensors nesse repositório.")
        except hf.HFError as e:
            raise HTTPException(400, str(e)) from e
        return {"repo": ref["repo"], "page": f"https://huggingface.co/{ref['repo']}", "files": files,
                "has_token": bool(hf.token())}

    @app.post("/api/loras/hf/download")
    async def loras_hf_download(body: dict):
        """Baixa o arquivo para o ComfyUI e já cadastra a LoRA na biblioteca (a descrição você escreve depois)."""
        url = str(body.get("url") or "")
        try:
            ref = hf.parse(url)
            if not ref["path"] or not ref["path"].lower().endswith(".safetensors"):
                raise hf.HFError("Escolha um arquivo .safetensors da lista.")
        except hf.HFError as e:
            raise HTTPException(400, str(e)) from e
        name = str(body.get("file") or ref["path"].rsplit("/", 1)[-1]).strip()
        lid = re.sub(r"[^a-z0-9]+", "_", str(body.get("id") or name.rsplit(".", 1)[0]).lower()).strip("_")[:40] or "lora"
        title = str(body.get("title") or name.rsplit(".", 1)[0])
        if lid in store.loras() and not body.get("overwrite"):
            raise HTTPException(400, f"Já existe uma LoRA com o id '{lid}'. Escolha outro nome ou edite a existente.")
        res = await setup.download_lora({"id": lid, "title": title, "file": name, "url": hf.file_url(ref["repo"], ref["rev"], ref["path"]),
                                          "page": f"https://huggingface.co/{ref['repo']}"})
        lora = store.save_lora({"id": lid, "title": title, "file": name, "strength": 1.0, "auto": False,
                                "description": str(body.get("description") or "")})
        return {**res, "lora": lora}

    @app.post("/api/loras/{lid}/restore")
    async def loras_restore(lid: str):
        return {"result": store.restore_lora(lid)}

    @app.delete("/api/loras/{lid}")
    async def loras_delete(lid: str):
        return {"result": store.delete_lora(lid)}

    @app.get("/api/importable")
    async def importable():
        return importer.list_importable(store)

    @app.get("/api/importable/draft")
    async def import_draft(file: str):
        return importer.draft(store, file)

    @app.post("/api/import")
    async def import_workflow(body: dict):
        m = importer.register(store, body)
        return workflow_view(m["id"], m, store.clef_config())

    @app.get("/api/clef-config")
    async def get_clef_config():
        c = store.clef_config()
        return {"instructions": c["instructions"], "recipes": c["recipes"]}

    @app.put("/api/clef-config")
    async def put_clef_config(body: ClefConfigIn):
        c = store.clef_config()
        if body.instructions is not None:
            c["instructions"] = body.instructions
        if body.recipes is not None:
            c["recipes"] = body.recipes
        new = store.save_clef_config(c)
        return {"instructions": new["instructions"], "recipes": new["recipes"]}

    # ------------------------------------------- casos de uso: agente e repetição
    @app.post("/api/recipes/similarity")
    async def recipes_similarity(body: SimilarityIn):
        """Nota de repetição de cada par de casos de uso (Clef + estrutura) e os grupos que valem uma fusão."""
        try:
            return await score_pairs(store, make_clef(store.settings()), body.ids)
        except ClefError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/recipes/merge")
    async def recipes_merge(body: MergeIn):
        """O LLM propõe UM caso de uso que cobre os escolhidos. Nada é gravado: você revisa e aprova em /merge/apply."""
        try:
            return await merge_recipes(store, pipe.llm, body.ids)
        except StoreError as e:
            raise HTTPException(400, str(e)) from e
        except AddonError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/recipes/merge/apply")
    async def recipes_merge_apply(body: MergeApplyIn):
        if body.mode not in ("disable", "delete"):
            raise HTTPException(400, "mode deve ser 'disable' ou 'delete'.")
        try:
            new = apply_merge(store, body.recipe, body.replaces, body.mode)
        except StoreError as e:
            raise HTTPException(400, str(e)) from e
        c = store.clef_config()
        return {"recipe": new, "instructions": c["instructions"], "recipes": c["recipes"]}

    @app.post("/api/recipes/{rid}/keep")
    async def recipe_keep(rid: str):
        """Tira o caso de uso criado pelo agente do regime de teste: ele deixa de ser trocado por um mais novo."""
        try:
            store.keep_generated(rid)
        except StoreError as e:
            raise HTTPException(404, str(e)) from e
        c = store.clef_config()
        return {"instructions": c["instructions"], "recipes": c["recipes"]}

    @app.post("/api/doctor")
    async def doctor():
        comfy = await pipe.comfy()
        try:
            oi = await comfy.object_info(refresh=True)
        except ComfyError as e:
            raise HTTPException(502, str(e)) from e
        return validate_all(store, oi)

    # ------------------------------------------------------------- files
    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        try:
            return images.save(data, file.filename)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/files/{name}")
    async def files(name: str):
        try:
            p = images.path(name)
        except FileNotFoundError:
            raise HTTPException(404, "Imagem não encontrada") from None
        return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "image/png")

    # ------------------------------------------------------------ 3D (splats)
    @app.get("/splats/{name}")
    async def splat_file(name: str):
        try:
            p = pipe.splats.file(Path(name).stem)
        except KeyError:
            raise HTTPException(404, "Modelo 3D não encontrado") from None
        return FileResponse(p, media_type="application/octet-stream", filename=p.name)

    @app.get("/api/splats")
    async def splats_list(all: bool = False):
        """Biblioteca 3D: os que você guardou (com `all=true`, também os recentes não guardados)."""
        return [splat_view(m) for m in pipe.splats.all() if all or m.get("saved")]

    @app.put("/api/splats/{sid}")
    async def splats_put(sid: str, body: dict):
        try:
            meta = pipe.splats.update(sid, body.get("name") if "name" in body else None,
                                      body.get("saved") if "saved" in body else None)
        except KeyError:
            raise HTTPException(404, "Modelo 3D não encontrado") from None
        return splat_view(meta)

    @app.delete("/api/splats/{sid}")
    async def splats_delete(sid: str):
        try:
            pipe.splats.delete(sid)
        except KeyError:
            raise HTTPException(404, "Modelo 3D não encontrado") from None
        return {"ok": True}

    # ------------------------------------------------------ conversations
    @app.get("/api/conversations")
    async def conv_list():
        return convs.list()

    @app.post("/api/conversations")
    async def conv_new():
        return convs.create()

    @app.get("/api/conversations/{cid}")
    async def conv_get(cid: str):
        try:
            conv = convs.get(cid)
        except KeyError:
            raise HTTPException(404, "Conversa não encontrada") from None
        conv["active_run"] = pipe.active_run(cid)
        return conv

    @app.delete("/api/conversations/{cid}")
    async def conv_del(cid: str):
        convs.delete(cid)
        return {"ok": True}

    @app.post("/api/conversations/{cid}/messages")
    async def send(cid: str, body: MessageIn):
        try:
            conv = convs.get(cid)
        except KeyError:
            raise HTTPException(404, "Conversa não encontrada") from None
        text = body.text.strip()
        if not text and not body.image_ids:
            raise HTTPException(400, "Escreva um pedido ou anexe uma imagem.")
        try:
            imgs = [images.info(i) for i in body.image_ids]
        except FileNotFoundError:
            raise HTTPException(400, "Uma das imagens anexadas não existe mais.") from None
        conv["messages"].append({"role": "user", "text": text, "images": imgs, "route": body.route})
        if conv["title"] == "Nova conversa" and text:
            conv["title"] = text[:48]
        convs.save(conv)
        run = pipe.start(cid, text, body.image_ids, body.route)
        return {"run_id": run.id}

    @app.get("/api/runs/{rid}/events")
    async def events(rid: str):
        run = pipe.runs.get(rid)
        if not run:
            raise HTTPException(404, "Execução não encontrada")

        async def gen():
            async for ev in run.stream():
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{rid}/input")
    async def run_input(rid: str, body: AnswerIn):
        try:
            pipe.answer(rid, body.input_id, body.value)
        except KeyError:
            raise HTTPException(404, "Nenhuma pergunta pendente com esse id") from None
        return {"ok": True}

    @app.post("/api/runs/{rid}/cancel")
    async def run_cancel(rid: str):
        pipe.cancel(rid)
        return {"ok": True}

    # ------------------------------------------------------------- addons
    @app.get("/api/addons")
    async def addons_list():
        llm_url = llmcfg.resolve(store.settings().get("writer") or {}).get("url", "")
        return {"dir": str(store.addons_dir), "addons": addons.status(), "hooks": HOOKS,
                "llm_url": llm_url}

    @app.put("/api/addons/{name}")
    async def addons_set(name: str, body: dict):
        """Liga/desliga um addon e guarda a configuração dele. Código e interface valem após reiniciar; os
        recursos (workflows, prompts, LoRAs) valem na hora."""
        if name not in {a.name for a in addons.discover_all()}:
            raise HTTPException(404, "Addon não encontrado")
        cur = store.settings().get("addons", {})
        off = [n for n in (cur.get("disabled") or []) if n != name]
        if body.get("enabled") is False:
            off.append(name)
        cfg = dict(cur.get("config") or {})
        if isinstance(body.get("config"), dict):
            cfg[name] = body["config"]
        store.save_settings({"addons": {"disabled": sorted(set(off)), "config": cfg}})
        return {"ok": True, "restart": True}

    @app.post("/api/addons")
    async def addons_new(body: dict):
        try:
            dest = new_addon(store.addons_dir, str(body.get("name") or "").strip(), root / "defaults" / "addon_template")
        except AddonError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True, "path": str(dest), "restart": True}

    @app.get("/addons/{name}/web/{path:path}")
    async def addon_web(name: str, path: str):
        p = addons.web_file(name, path)
        if p is None:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------- static
    web = root / "web"

    # no-cache: o navegador revalida (ETag, 304 sem baixar de novo); sem isso, depois de atualizar o PickFlow a página
    # podia continuar com o app.js antigo até um Ctrl+F5
    @app.get("/")
    async def index():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/static/{path:path}")
    async def static(path: str):
        p = (web / path).resolve()
        if web.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
