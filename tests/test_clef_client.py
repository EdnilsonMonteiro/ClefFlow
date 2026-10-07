"""Contrato HTTP do ClefClient contra um servidor System One falso."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clefflow.clef import ClefClient, ClefError, choice_question
from PIL import Image
import io


def png(color=(200, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", (900, 600), color).save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def server():
    seen = {}
    mode = {"status": 200, "answers": None}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok": true, "loaded": true}')

        def do_POST(self):
            if self.path == "/v1/unload":
                seen["unloaded"] = seen.get("unloaded", 0) + 1
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}"); return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["path"], seen["body"] = self.path, body
            if mode["status"] != 200:
                self.send_response(mode["status"]); self.end_headers(); self.wfile.write(b"boom"); return
            answers = mode["answers"] if mode["answers"] is not None else {
                q: {"choice": list(v["criteria"])[1], "confidence": 0.8,
                    "probabilities": {k: (0.8 if i == 1 else 0.2 / (len(v["criteria"]) - 1))
                                      for i, k in enumerate(v["criteria"])}}
                for q, v in body["questions"].items()}
            out = json.dumps({"answers": answers}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen, mode
    srv.shutdown()


def test_request_shape_and_answer_parsing(server):
    url, seen, _ = server
    c = ClefClient(url, max_images=2)
    qs = {"route": choice_question("qual?", {"none": "nada", "edit": "editar", "orbit": "girar"})}
    ans = asyncio.run(c.decide({"request": "gire"}, qs, [png(), png(), png()]))
    assert seen["path"] == "/v1/systemone"
    assert seen["body"]["model"] == "clef"
    assert seen["body"]["state"] == {"request": "gire"}
    assert seen["body"]["questions"]["route"]["type"] == "choice"
    assert len(seen["body"]["images"]) == 2  # respeita max_images
    assert seen["body"]["images"][0].startswith("data:image/png;base64,")
    a = ans["route"]
    assert a.choice == "edit" and a.confidence == pytest.approx(0.8)
    assert a.top(1)[0][0] == "edit"


def test_errors_are_explained(server):
    url, _, mode = server
    qs = {"q": choice_question("x", {"a": "a", "b": "b"})}
    mode["status"] = 500
    with pytest.raises(ClefError, match="500"):
        asyncio.run(ClefClient(url).decide({}, qs))
    mode["status"], mode["answers"] = 200, {}
    with pytest.raises(ClefError, match="não respondeu"):
        asyncio.run(ClefClient(url).decide({}, qs))
    with pytest.raises(ClefError, match="Não consegui falar"):
        asyncio.run(ClefClient("http://127.0.0.1:1", timeout_s=2).decide({}, qs))


def test_choice_limits():
    with pytest.raises(ClefError):
        choice_question("x", {"a": "a"})
    with pytest.raises(ClefError):
        choice_question("x", {chr(97 + i) + str(i): "d" for i in range(27)})


def test_unload_hits_server_and_tolerates_absence(server):
    url, seen, _ = server
    assert asyncio.run(ClefClient(url).unload()) is True and seen["unloaded"] == 1
    assert asyncio.run(ClefClient("http://127.0.0.1:1").unload()) is False  # sem servidor: não é erro


def test_health_returns_json_and_unload_falls_back_to_short_path(server):
    url, seen, _ = server
    assert asyncio.run(ClefClient(url).health())["loaded"] is True
    assert asyncio.run(ClefClient("http://127.0.0.1:1").health()) == {}
