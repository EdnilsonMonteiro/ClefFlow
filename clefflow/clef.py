"""Cliente do Clef (API System One / Jev) e um modo `mock` para trabalhar sem o modelo.

O Clef não escreve texto: ele recebe um `state`, imagens e `questions` e devolve
probabilidades por pergunta (`choice`, `score`, `noul`). Este módulo só fala esse
formato; quem decide o que perguntar é o `router`.
"""
from __future__ import annotations

import base64
import io
import math
import re
import unicodedata
from dataclasses import dataclass, field

import httpx
from PIL import Image

MAX_OPTIONS = 26


class ClefError(Exception):
    pass


@dataclass
class Answer:
    choice: str | None
    confidence: float
    probs: dict[str, float] = field(default_factory=dict)

    def top(self, n: int = 3) -> list[tuple[str, float]]:
        return sorted(self.probs.items(), key=lambda kv: -kv[1])[:n]


def choice_question(instructions: str, criteria: dict[str, str]) -> dict:
    if not 2 <= len(criteria) <= MAX_OPTIONS:
        raise ClefError(f"Uma pergunta 'choice' aceita de 2 a {MAX_OPTIONS} opções (recebeu {len(criteria)}).")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def normalize_answer(raw: dict, options: list[str] | None = None) -> Answer:
    """Aceita as variações de formato da resposta de uma pergunta e devolve `Answer`."""
    probs = raw.get("probabilities") or raw.get("probs") or {}
    if isinstance(probs, list):
        keys = options or [str(i) for i in range(len(probs))]
        probs = dict(zip(keys, probs))
    probs = {str(k): float(v) for k, v in probs.items()}
    if not probs and "probability" in raw:
        p = float(raw["probability"])
        probs = {"yes": p, "no": 1 - p}
    choice = raw.get("choice")
    if choice is None and probs:
        choice = max(probs, key=probs.get)
    conf = raw.get("confidence")
    if conf is None:
        conf = probs.get(choice, 0.0) if choice is not None else 0.0
    return Answer(None if choice is None else str(choice), float(conf), probs)


def encode_image(data: bytes, max_side: int = 768) -> str:
    """Reduz a imagem (o Clef só precisa entender a cena) e devolve uma data URL PNG."""
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class ClefClient:
    """Fala com um servidor `POST {url}{path}` no formato System One."""

    mode = "systemone"

    def __init__(self, url: str, path: str = "/v1/systemone", model: str = "clef",
                 timeout_s: float = 120, max_images: int = 4, api_key: str = ""):
        self.url, self.path, self.model = url.rstrip("/"), path, model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout_s, self.max_images = timeout_s, max_images

    async def ping(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.url + "/health")
                return {"ok": r.status_code < 500, "mode": self.mode, "url": self.url}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "mode": self.mode, "url": self.url, "error": str(e) or e.__class__.__name__}

    async def health(self) -> dict:
        """JSON de GET /health do servidor (vazio se não responder)."""
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.url + "/health", headers=self.headers)
                return r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            return {}

    async def unload(self) -> bool:
        """Pede ao servidor local para descarregar o modelo (libera RAM/VRAM). Falhar não é erro."""
        for path in ("/v1/unload", "/unload"):
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.post(self.url + path, headers=self.headers)
                if r.status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                return False
        return False

    async def decide(self, state: dict | str, questions: dict[str, dict],
                     images: list[bytes] | None = None) -> dict[str, Answer]:
        body: dict = {"model": self.model, "state": state, "questions": questions}
        if images:
            body["images"] = [encode_image(b) for b in images[: self.max_images]]
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(self.url + self.path, json=body, headers=self.headers)
        except Exception as e:  # noqa: BLE001
            raise ClefError(f"Não consegui falar com o Clef em {self.url}: {e}") from e
        if r.status_code != 200:
            raise ClefError(f"Clef respondeu {r.status_code}: {r.text[:300]}")
        answers = (r.json() or {}).get("answers") or {}
        out = {}
        for qid, q in questions.items():
            if qid not in answers:
                raise ClefError(f"O Clef não respondeu a pergunta '{qid}'.")
            opts = list((q.get("criteria") or {}).keys()) if isinstance(q.get("criteria"), dict) else None
            out[qid] = normalize_answer(answers[qid], opts)
        return out


# ----------------------------------------------------------------- mock
def _tokens(text: str) -> set[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r"[a-z0-9]{3,}", t)}


class MockClef:
    """Substituto sem modelo: pontua cada opção por palavras em comum com o pedido.

    Serve para testar a interface e o encadeamento sem o Clef rodando. As palavras
    vêm de `state["request"]` e das `criteria`/`instructions` de cada pergunta.
    """

    mode = "mock"

    async def ping(self) -> dict:
        return {"ok": True, "mode": self.mode}

    async def health(self) -> dict:
        return {"loaded": True, "mock": True}

    async def unload(self) -> bool:
        return False

    async def decide(self, state, questions, images=None):
        req = state.get("request", "") if isinstance(state, dict) else str(state)
        rt = _tokens(req)
        out = {}
        for qid, q in questions.items():
            crit = q.get("criteria")
            if isinstance(crit, dict):
                scores = {}
                for key, desc in crit.items():
                    kw = _tokens(desc + " " + key.replace("_", " "))
                    scores[key] = len(rt & kw) + (1.5 if key in ("none", "nenhum") else 0.0)
                out[qid] = self._softmax_answer(scores)
            else:  # noul
                p = 0.5
                out[qid] = Answer("yes" if p >= 0.5 else "no", p, {"yes": p, "no": 1 - p})
        return out

    @staticmethod
    def _softmax_answer(scores: dict[str, float]) -> Answer:
        m = max(scores.values())
        exps = {k: math.exp(3.0 * (v - m)) for k, v in scores.items()}
        z = sum(exps.values())
        probs = {k: v / z for k, v in exps.items()}
        best = max(probs, key=probs.get)
        return Answer(best, probs[best], probs)
