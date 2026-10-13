"""ComfyUI de mentira para testes: serve o `object_info` real reduzido (tests/object_info.json) e um servidor HTTP mínimo."""
from __future__ import annotations

import copy
import io
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image


def load_object_info() -> dict:
    """`object_info` real (reduzido e sem nomes de arquivos da máquina de quem gerou), em tests/object_info.json."""
    return json.loads((Path(__file__).parent / "object_info.json").read_text(encoding="utf-8"))


class FakeComfy:
    """Servidor HTTP mínimo: /object_info, /upload/image, /prompt, /history, /view, /free."""

    def __init__(self, object_info: dict, fail_node: str | None = None, delay: float = 0.0):
        self.oi = copy.deepcopy(object_info)          # cada teste muda o seu: o object_info da sessão é compartilhado
        self.delay, self.started = delay, {}
        self.prompts: dict[str, dict] = {}
        self.uploads: dict[str, bytes] = {}
        self.fail_node = fail_node
        self.freed = 0
        self.view_rgba = False            # True: /view devolve um recorte com borda transparente (como o RMBG)
        self.views: dict[str, bytes] = {}  # nome do arquivo de saída -> PNG a devolver no /view
        self.stats = {"system": {}}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencioso
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                p = self.path.split("?")[0]
                if p == "/system_stats":
                    return self._json(outer.stats)
                if p == "/object_info":
                    return self._json(outer.oi)
                if p.startswith("/history/"):
                    pid = p.rsplit("/", 1)[1]
                    return self._json(outer._history(pid))
                if p == "/view":
                    from urllib.parse import parse_qs, urlparse
                    name = (parse_qs(urlparse(self.path).query).get("filename") or [""])[0]
                    buf = io.BytesIO()
                    if name in outer.views:                       # imagem específica para um nó de saída
                        buf.write(outer.views[name])
                    elif name.endswith(".spz"):                   # arquivo 3D do SaveGLB (conteúdo de mentira)
                        buf.write(b"SPZ-FAKE:" + name.encode())
                    elif outer.view_rgba:
                        im = Image.new("RGBA", (64, 48), (0, 0, 0, 0))
                        im.paste(Image.new("RGBA", (48, 32), (200, 80, 40, 255)), (8, 8))
                        im.save(buf, format="PNG")
                    else:
                        Image.new("RGB", (64, 48), (200, 80, 40)).save(buf, format="PNG")
                    b = buf.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                    return
                self._json({}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                p = self.path
                if p == "/upload/image":
                    name = f"up_{uuid.uuid4().hex[:6]}.png"
                    a = body.find(b"\x89PNG\r\n\x1a\n")
                    outer.uploads[name] = body[a:body.rfind(b"IEND") + 8] if a >= 0 else body
                    return self._json({"name": name, "subfolder": "clefflow", "type": "input"})
                if p == "/prompt":
                    prompt = json.loads(body)["prompt"]
                    errs = outer._validate(prompt)
                    if errs:
                        return self._json({"error": {"message": "Prompt outputs failed validation", "details": ""},
                                           "node_errors": errs}, 400)
                    pid = uuid.uuid4().hex
                    outer.prompts[pid] = prompt
                    return self._json({"prompt_id": pid})
                if p == "/free":
                    outer.freed += 1
                    return self._json({})
                self._json({})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()

    def _validate(self, prompt: dict) -> dict:
        """Mesma checagem do botão "Verificar workflows" (nós, entradas obrigatórias, ligações, limites)."""
        from clefflow.doctor import check_api
        errs = {}
        for msg in check_api(prompt, self.oi):
            nid = msg.split("#", 1)[1].split(":", 1)[0].split()[0] if "#" in msg else "?"
            errs.setdefault(nid, {"class_type": "?", "errors": []})["errors"].append({"message": msg})
        return errs

    def _history(self, pid: str) -> dict:
        prompt = self.prompts.get(pid)
        if prompt is None:
            return {}
        import time as _t
        if self.delay and _t.time() - self.started.setdefault(pid, _t.time()) < self.delay:
            return {}  # ainda executando
        if self.fail_node:
            return {pid: {"status": {"status_str": "error", "completed": False, "messages": [
                ["execution_error", {"node_id": self.fail_node, "node_type": "KSampler",
                                     "exception_message": "CUDA out of memory"}]]}, "outputs": {}}}
        outs = {}
        for nid, node in prompt.items():
            if node["class_type"] in ("SaveImage", "PreviewImage", "SaveImageAdvanced"):
                outs[nid] = {"images": [{"filename": f"out_{nid}.png", "subfolder": "", "type": "output" if node["class_type"] == "SaveImage" else "temp"}]}
            elif node["class_type"] == "SaveGLB":
                outs[nid] = {"3d": [{"filename": f"splat_{nid}.spz", "subfolder": "clefflow", "type": "output"}]}
        return {pid: {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outs}}
