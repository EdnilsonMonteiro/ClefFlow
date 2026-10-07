"""Sobe o app com ComfyUI de mentira e Clef em modo de teste, para ver a interface sem GPU.

    python tests/demo_server.py   ->  http://127.0.0.1:7861
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from clefflow.app import create_app  # noqa: E402
from clefflow.store import Store  # noqa: E402
from fake_comfy import FakeComfy, load_object_info  # noqa: E402

if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="clefflow_demo_"))
    for d in ("defaults", "web"):
        shutil.copytree(ROOT / d, tmp / d)
    (tmp / "data").mkdir()
    import os
    if os.environ.get("DEMO_SEED_CUSTOM"):        # um workflow "seu" ainda não registrado, para ver o importador
        (tmp / "custom" / "workflows").mkdir(parents=True)
        shutil.copy(ROOT / "defaults" / "workflows" / "Qwen2.1 Image Edit Quality.json", tmp / "custom" / "workflows" / "Meu edit.json")
    oi = load_object_info()
    settings = {"comfyui": {"url": ""}, "clef": {"mode": "mock"}}
    if os.environ.get("DEMO_SETUP"):               # ComfyUI "novo": sem os nós, LoRAs e modelos; downloads de mentira, lentos
        import threading
        import time as _t
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import yaml
        for cls in ("DrawBox", "MoveBoxes", "DrawMoveBoxes", "ViggleTurboLora", "ViggleTurboSigmas"):
            oi.pop(cls, None)
        for cls, key in (("LoraLoader", "lora_name"), ("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"), ("VAELoader", "vae_name")):
            oi[cls]["input"]["required"][key][0] = []
        root = tmp / "ComfyUI"
        (root / "custom_nodes").mkdir(parents=True)
        (root / "models").mkdir()
        settings["comfyui"]["path"] = str(root)

        class Slow(BaseHTTPRequestHandler):
            size = 40 * 1024 * 1024

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.send_response(200); self.send_header("Content-Length", str(self.size)); self.end_headers()

            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", str(self.size)); self.end_headers()
                for _ in range(self.size // (1 << 20)):
                    self.wfile.write(b"x" * (1 << 20)); _t.sleep(0.12)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        (tmp / "custom").mkdir(exist_ok=True)
        cat = yaml.safe_load((ROOT / "defaults" / "models.yaml").read_text(encoding="utf-8"))["files"]
        for e in cat:
            e["url"] = f"http://127.0.0.1:{srv.server_address[1]}/{e['file']}"
        (tmp / "custom" / "models.yaml").write_text(yaml.dump({"files": cat}), encoding="utf-8")
    fake = FakeComfy(oi, delay=float(os.environ.get("DEMO_DELAY", "0")))
    settings["comfyui"]["url"] = "http://127.0.0.1:9" if os.environ.get("DEMO_NO_COMFY") else fake.url   # porta fechada
    if os.environ.get("DEMO_NO_COMFY"):
        settings["comfyui"].pop("path", None)
    Store(tmp).save_settings(settings)
    print("demo em http://127.0.0.1:7861  (dados em", tmp, ")", flush=True)
    uvicorn.run(create_app(tmp), host="127.0.0.1", port=7861, log_level="warning")
