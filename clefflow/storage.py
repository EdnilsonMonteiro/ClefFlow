"""Imagens e conversas gravadas em data/ (nada disso toca nas pastas do ComfyUI)."""
from __future__ import annotations

import re

import io
import json
import time
import uuid
from pathlib import Path

from PIL import Image

from .store import atomic_write

EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp"}


class ImageStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "images"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, name: str | None = None) -> dict:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Arquivo não é uma imagem válida: {name or ''}") from e
        iid = uuid.uuid4().hex[:16]
        ext = EXT.get(im.format or "PNG", "png")
        (self.dir / f"{iid}.{ext}").write_bytes(data)
        return {"id": iid, "name": name or f"{iid}.{ext}", "url": f"/files/{iid}.{ext}",
                "width": im.width, "height": im.height}

    def save_pil(self, im: Image.Image, name: str) -> dict:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return self.save(buf.getvalue(), name)

    def path(self, iid: str) -> Path:
        iid = Path(str(iid)).stem
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", iid):          # nada de curingas nem de sair da pasta
            raise FileNotFoundError(iid)
        for p in self.dir.glob(f"{iid}.*"):
            return p
        raise FileNotFoundError(iid)

    def load(self, iid: str) -> bytes:
        return self.path(iid).read_bytes()

    def info(self, iid: str) -> dict:
        p = self.path(iid)
        with Image.open(p) as im:
            return {"id": p.stem, "name": p.name, "url": f"/files/{p.name}", "width": im.width, "height": im.height}


def to_png(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def mask_to_alpha_png(source: bytes, mask_png: bytes) -> bytes:
    """Convenção do LoadImage do ComfyUI: a máscara é o INVERSO do alfa.
    Recebe a fonte e uma máscara (branco = região a refazer) e devolve a fonte com a região recortada."""
    src = Image.open(io.BytesIO(source)).convert("RGBA")
    m = Image.open(io.BytesIO(mask_png)).convert("L").resize(src.size)
    alpha = m.point(lambda v: 0 if v > 127 else 255)
    src.putalpha(alpha)
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    return buf.getvalue()


class ConvStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "conversations"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, cid: str) -> Path:
        return self.dir / f"{Path(cid).stem}.json"

    def create(self, title: str = "Nova conversa") -> dict:
        c = {"id": uuid.uuid4().hex[:12], "title": title, "created": time.time(), "updated": time.time(),
             "messages": []}
        self.save(c)
        return c

    def save(self, c: dict):
        c["updated"] = time.time()
        atomic_write(self._p(c["id"]), json.dumps(c, ensure_ascii=False))

    def get(self, cid: str) -> dict:
        p = self._p(cid)
        if not p.exists():
            raise KeyError(cid)
        return json.loads(p.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": c["id"], "title": c["title"], "updated": c["updated"],
                            "count": len(c["messages"])})
            except Exception:  # noqa: BLE001
                continue
        return sorted(out, key=lambda c: -c["updated"])

    def delete(self, cid: str):
        self._p(cid).unlink(missing_ok=True)
