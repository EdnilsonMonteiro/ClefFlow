"""API web e arquivos estáticos do ClefFlow."""
from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path

from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__
from .comfy import ComfyClient, ComfyError
from . import importer
from .doctor import validate_all
from .runner import Pipeline, make_clef
from .setup import SetupService
from .router import required_images
from .storage import ConvStore, ImageStore
from .store import Store, StoreError

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


class ClefConfigIn(BaseModel):
    instructions: str | None = None
    recipes: list[dict] | None = None


def create_app(root: Path = ROOT) -> FastAPI:
    store = Store(root)
    store.ensure_custom_dirs()
    images = ImageStore(store.data_dir)
    convs = ConvStore(store.data_dir)
    pipe = Pipeline(store, images, convs)
    app = FastAPI(title="ClefFlow", version=__version__)
    setup = SetupService(store, pipe)
    app.state.store, app.state.pipe, app.state.setup = store, pipe, setup

    loopback = {"localhost", "127.0.0.1", "[::1]", "::1"}

    @app.middleware("http")
    async def _local_only(request: Request, call_next):
        """O ClefFlow roda na sua máquina: recusa páginas de outros sites que tentem usar o servidor
        (requisição com Origin de outro endereço) e nomes de host estranhos (DNS rebinding)."""
        if store.settings()["server"]["host"] in ("127.0.0.1", "localhost", "::1"):
            host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") \
                else (request.headers.get("host") or "").split("]")[0] + "]"
            if host not in loopback:
                return JSONResponse({"detail": "Acesse o ClefFlow por http://127.0.0.1:<porta>."}, status_code=403)
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

    @app.get("/api/settings")
    async def get_settings():
        return store.settings()

    @app.put("/api/settings")
    async def put_settings(patch: dict):
        new = store.save_settings(patch)
        pipe._comfy = None
        return new

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
        return {"id": wid, "title": m.get("title", wid), "group": m.get("group", ""), "file": m["file"],
                "note": m.get("note", ""), "images": m.get("images", []), "params": m.get("params", []),
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

    # ------------------------------------------------------------- static
    web = root / "web"

    @app.get("/")
    async def index():
        return FileResponse(web / "index.html")

    @app.get("/static/{path:path}")
    async def static(path: str):
        p = (web / path).resolve()
        if web.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    return app


app = create_app()
