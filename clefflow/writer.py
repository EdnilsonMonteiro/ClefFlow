"""Reescrita opcional do pedido para um prompt curto em inglês (qualquer servidor compatível
com a API de chat da OpenAI: llama-server, Ollama, LM Studio). Desligado por padrão."""
from __future__ import annotations

import httpx


async def rewrite(request: str, cfg: dict) -> str:
    body = {"model": cfg.get("model") or "default", "temperature": 0.3, "max_tokens": 300, "stream": False,
            "messages": [{"role": "system", "content": cfg["system"]}, {"role": "user", "content": request}]}
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout_s", 120))) as c:
            r = await c.post(cfg["url"].rstrip("/") + "/chat/completions", json=body)
            r.raise_for_status()
            text = (r.json()["choices"][0]["message"]["content"] or "").strip().strip('"')
            return text or request
    except Exception:  # noqa: BLE001
        return request          # se o redator falhar, segue com o pedido original
