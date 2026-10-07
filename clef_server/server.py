"""Servidor local do Clef (POST /v1/systemone, GET /health).

Carrega os pesos da Cloudflare (Cloudflare/clef-flash ou Cloudflare/clef) com o
`joint_schema_model.py` que vem junto do repositório e responde no mesmo formato da
API System One. O campo `images` aceita data URLs ou base64 puro.

Para não disputar VRAM com o ComfyUI, o modelo é carregado no primeiro pedido e
descarregado depois de `--idle-unload` segundos sem uso.

    python clef_server/server.py --model Cloudflare/clef-flash --port 8321

Observação: escrito a partir da documentação pública do repositório; não foi
executado com os pesos reais no ambiente onde o projeto foi criado.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import io
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from PIL import Image


class Holder:
    def __init__(self, model: str, device: str, idle_unload: float, quant: str = "none"):
        self.model_id, self.device, self.idle, self.quant = model, device, idle_unload, quant
        self.model = self.processor = self.systemone = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    def load(self):
        if self.model is not None:
            return
        from huggingface_hub import snapshot_download

        path = self.model_id
        if not __import__("os").path.isdir(path):
            path = snapshot_download(self.model_id)
        if path not in sys.path:
            sys.path.insert(0, path)  # o joint_schema_model.py vem no repositório
        from joint_schema_model import load_release_model, systemone

        print(f"[clef] carregando {self.model_id} em {self.device} ({self.quant})…", flush=True)
        t0 = time.time()
        kwargs = {}
        if self.quant in ("int8", "int4"):  # load_release_model repassa kwargs ao from_pretrained
            import torch
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = (
                BitsAndBytesConfig(load_in_8bit=True) if self.quant == "int8"
                else BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                                        bnb_4bit_quant_type="nf4"))
        self.model, self.processor = load_release_model(path, device=self.device, **kwargs)
        self.systemone = systemone
        print(f"[clef] pronto em {time.time() - t0:.0f}s", flush=True)

    def unload(self):
        with self.lock:
            if self.model is None:
                return
            self.model = self.processor = self.systemone = None
            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            print("[clef] descarregado por inatividade", flush=True)

    def run(self, request: dict) -> dict:
        with self.lock:
            self.load()
            out = self.systemone(self.model, self.processor, request)
            self.last_used = time.time()
            return out


def decode_image(item) -> Image.Image:
    if isinstance(item, dict):  # {"url": "..."} no estilo OpenAI
        item = item.get("url") or item.get("image") or ""
    if not isinstance(item, str) or not item:
        raise ValueError("imagem inválida")
    if item.startswith("data:"):
        item = item.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(item))).convert("RGB")


def jsonable(x):
    if hasattr(x, "tolist"):
        return x.tolist()
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def create_app(holder: Holder) -> FastAPI:
    app = FastAPI(title="Clef (System One)")

    @app.on_event("startup")
    async def _janitor():
        async def loop():
            while True:
                await asyncio.sleep(15)
                if holder.idle and holder.model is not None and time.time() - holder.last_used > holder.idle:
                    await asyncio.to_thread(holder.unload)

        asyncio.create_task(loop())

    @app.get("/health")
    async def health():
        """O ClefFlow lê `loaded` e `vram.allocated_gb` para decidir se precisa descarregar o Clef antes de gerar."""
        out = {"ok": True, "model": holder.model_id, "loaded": holder.model is not None, "device": holder.device}
        try:
            import torch
            if holder.model is not None and str(holder.device).startswith("cuda") and torch.cuda.is_available():
                out["vram"] = {"allocated_gb": round(torch.cuda.memory_allocated() / 2**30, 2),
                               "reserved_gb": round(torch.cuda.memory_reserved() / 2**30, 2)}
        except Exception:  # noqa: BLE001 - health nunca deve falhar
            pass
        return out

    @app.post("/v1/unload")
    @app.post("/unload")
    async def unload():
        await asyncio.to_thread(holder.unload)
        return {"ok": True, "loaded": holder.model is not None}

    @app.post("/v1/systemone")
    async def systemone_endpoint(req: Request):
        body = await req.json()
        if "questions" not in body:
            raise HTTPException(422, "faltou 'questions'")
        try:
            imgs = [decode_image(i) for i in body.get("images") or []]
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"imagem inválida: {e}") from e
        request = {"model": body.get("model", holder.model_id), "state": body.get("state", ""),
                   "questions": body["questions"]}
        if imgs:
            request["images"] = imgs
        try:
            out = await asyncio.to_thread(holder.run, request)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{e.__class__.__name__}: {e}") from e
        return jsonable(out)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Cloudflare/clef-flash", help="id no Hugging Face ou pasta local")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--idle-unload", type=float, default=120, help="segundos sem uso até descarregar (0 = nunca)")
    ap.add_argument("--quant", choices=["none", "int8", "int4"], default="none",
                    help="quantização do backbone via bitsandbytes (precisa do pacote bitsandbytes)")
    ap.add_argument("--preload", action="store_true", help="carrega o modelo já na partida")
    a = ap.parse_args()
    holder = Holder(a.model, a.device, a.idle_unload, a.quant)
    if a.preload:
        holder.load()
        holder.last_used = time.time()
    import uvicorn

    uvicorn.run(create_app(holder), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
