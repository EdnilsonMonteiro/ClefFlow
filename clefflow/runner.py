"""Executa um turno do chat: planeja com o Clef, liga as imagens, roda no ComfyUI e encadeia."""
from __future__ import annotations

import asyncio
import io
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from . import writer
from .clef import ClefClient, ClefError, MockClef
from .comfy import ComfyClient, ComfyError
from .memory import Memory, add_clef, from_stats, should_free_comfy, should_unload
from .converter import Converter, ConversionError, node_id
from .router import Binding, Ctx, ImageRef, Router, RouterError, Source, Step, needs_drawing
from .storage import ConvStore, ImageStore, mask_to_alpha_png, to_png
from .store import Store, StoreError


INPUT_TIMEOUT_S = 900


class RunCancelled(Exception):
    pass


@dataclass
class Run:
    id: str
    conv_id: str
    events: list[dict] = field(default_factory=list)
    finished: bool = False
    cancelled: bool = False
    inputs: dict[str, asyncio.Future] = field(default_factory=dict)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, ev: dict):
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    async def finish(self):
        async with self.cond:
            self.finished = True
            self.cond.notify_all()

    async def stream(self):
        i = 0
        while True:
            async with self.cond:
                await self.cond.wait_for(lambda: i < len(self.events) or self.finished)
                batch = self.events[i:]
                i = len(self.events)
                done = self.finished
            for ev in batch:
                yield ev
            if done and i >= len(self.events):
                return


def make_clef(settings: dict):
    c = settings["clef"]
    if c.get("mode") == "mock":
        return MockClef()
    return ClefClient(c["url"], c.get("path", "/v1/systemone"), c.get("model", "clef"),
                      float(c.get("timeout_s", 120)), int(c.get("max_images", 4)), c.get("api_key", "") or "")


def compose_prompt(text: str, request: str) -> str:
    out = text.replace("{request}", request.strip())
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([.,;:])", r"\1", out) if "{request}" in text and not request.strip() else out
    return out.strip()


class Pipeline:
    def __init__(self, store: Store, images: ImageStore, convs: ConvStore):
        self.store, self.images, self.convs = store, images, convs
        self.runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()
        self._comfy: ComfyClient | None = None

    # ------------------------------------------------------------------ comfy
    async def comfy(self) -> ComfyClient:
        s = self.store.settings()["comfyui"]
        if self._comfy is None or self._comfy.base_url != s["url"].rstrip("/"):
            self._comfy = ComfyClient(s["url"], self.store.data_dir, s.get("upload_subfolder", "clefflow"),
                                      float(s.get("timeout_s", 1800)))
        return self._comfy

    # -------------------------------------------------------------- run mgmt
    def start(self, conv_id: str, text: str, image_ids: list[str], route: str | None) -> Run:
        run = Run(uuid.uuid4().hex[:12], conv_id)
        self.runs[run.id] = run
        task = asyncio.get_running_loop().create_task(self._guarded(run, text, image_ids, route))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def active_run(self, conv_id: str) -> str | None:
        for r in self.runs.values():
            if r.conv_id == conv_id and not r.finished:
                return r.id
        return None

    def cancel(self, run_id: str):
        run = self.runs.get(run_id)
        if run:
            run.cancelled = True
            for f in run.inputs.values():
                if not f.done():
                    f.cancel()

    def answer(self, run_id: str, input_id: str, value: Any):
        run = self.runs.get(run_id)
        fut = run.inputs.get(input_id) if run else None
        if not fut or fut.done():
            raise KeyError(input_id)
        fut.set_result(value)

    async def _guarded(self, run: Run, text: str, image_ids: list[str], route: str | None):
        try:
            if self.lock.locked():
                await run.emit({"type": "status", "text": "Na fila: espero a execução anterior terminar…"})
            async with self.lock:
                await self._turn(run, text, image_ids, route)
        except (asyncio.CancelledError, RunCancelled):
            await run.emit({"type": "error", "message": "Execução cancelada."})
        except (RouterError, ClefError, ComfyError, ConversionError, StoreError, ValueError) as e:
            await run.emit({"type": "error", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            await run.emit({"type": "error", "message": f"Erro inesperado: {e.__class__.__name__}: {e}"})
        finally:
            self._persist(run)
            await run.finish()

    def _persist(self, run: Run):
        try:
            conv = self.convs.get(run.conv_id)
        except KeyError:
            return
        evs = [e for e in run.events if e["type"] not in ("progress",)]
        imgs = [i for e in evs if e["type"] == "done" for i in e.get("images", [])]
        conv["messages"].append({"role": "assistant", "events": evs, "images": imgs})
        self.convs.save(conv)

    async def ask_user(self, run: Run, payload: dict) -> Any:
        iid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        run.inputs[iid] = fut
        await run.emit({"type": "needs_input", "input_id": iid, **payload})
        try:
            return await asyncio.wait_for(fut, INPUT_TIMEOUT_S)
        except asyncio.CancelledError:
            raise RunCancelled() from None
        except asyncio.TimeoutError:
            raise RouterError("Ninguém respondeu à pergunta a tempo; a execução foi encerrada.") from None

    # ---------------------------------------------------------------- memória
    async def _measure(self, comfy, clef) -> Memory:
        try:
            mem = from_stats(await comfy.stats())
        except Exception:  # noqa: BLE001
            mem = Memory()
        try:
            return add_clef(mem, await clef.health())
        except Exception:  # noqa: BLE001
            return mem

    async def _make_room_for_clef(self, run: Run, comfy, clef, settings: dict):
        """Antes de decidir: se o Clef não está carregado e o ComfyUI ocupa a VRAM, libera o ComfyUI."""
        c = settings["clef"]
        if not c.get("make_room_for_clef", True) or getattr(clef, "mode", "") != "systemone":
            return
        mem = await self._measure(comfy, clef)
        free, why = should_free_comfy(mem, float(c.get("clef_vram_gb", 20)), float(c.get("margin_gb", 2)))
        if free:
            await comfy.free()
            await run.emit({"type": "memory", "text": why})

    async def _clef_memory_before_generation(self, run: Run, comfy, clef, settings: dict, manifest: dict):
        """Depois de decidir (e antes de desenhar/gerar): descarrega o Clef se a memória não comporta os dois."""
        c = settings["clef"]
        policy = c.get("unload_policy", "auto")
        if policy == "never" or getattr(clef, "mode", "") != "systemone":
            return
        mem = await self._measure(comfy, clef)
        need = manifest.get("mem") or {}
        go, why = should_unload(policy, mem, float(need.get("vram_gb", c.get("need_vram_gb", 16))),
                                float(need.get("ram_gb", c.get("need_ram_gb", 10))), float(c.get("margin_gb", 2)))
        if go:
            await clef.unload()
        if why:
            await run.emit({"type": "memory", "text": why})

    # ------------------------------------------------------------------ turn
    async def _turn(self, run: Run, text: str, image_ids: list[str], route: str | None):
        settings = self.store.settings()
        conv = self.convs.get(run.conv_id)
        users = []
        for iid in image_ids:
            info = self.images.info(iid)
            users.append(ImageRef(info["id"], info["name"], self.images.load(iid), info["width"], info["height"]))
        history = self._history(conv)
        last = self._last_result(conv)
        if not users and last:
            users = [ImageRef(last["id"], last["name"], self.images.load(last["id"]), last["width"], last["height"])]
            await run.emit({"type": "status", "text": "Sem imagem anexada: uso o último resultado da conversa."})
        ctx = Ctx(text, users, history)
        clef = make_clef(settings)
        router = Router(self.store, clef)
        comfy = await self.comfy()
        if settings["comfyui"].get("free_vram_before_clef"):
            await comfy.free()
        await self._make_room_for_clef(run, comfy, clef, settings)
        await run.emit({"type": "status", "text": "Clef está escolhendo o workflow…"})
        plan = await router.plan(ctx, route if route and route != "auto" else None)
        for d in plan.decisions:
            await run.emit({"type": "decision", **d})
        steps = plan.steps
        if plan.ambiguous:
            pick = await self.ask_user(run, {"kind": "choose", "title": "O Clef ficou em dúvida. Qual workflow usar?",
                                             "options": plan.ambiguous})
            if pick and pick != "__auto__":
                steps = router.steps_from_route(pick)
        await run.emit({"type": "plan", "steps": [
            {"workflow": s.workflow, "title": self.store.manifest(s.workflow).get("title", s.workflow)} for s in steps]})

        previous: dict | None = None
        final: list[dict] = []
        for idx, step in enumerate(steps):
            if run.cancelled:
                raise RunCancelled()
            sources = [Source(f"user_{i + 1}", f"Imagem {i + 1} enviada pelo usuário ({im.name})", im.data,
                              f"/files/{self.images.path(im.id).name}") for i, im in enumerate(users)]
            if previous:
                sources.append(Source("previous", "Resultado do passo anterior", self.images.load(previous["id"]),
                                      previous["url"]))
            m = self.store.manifest(step.workflow)
            if len(sources) < sum(1 for s in m.get("images", []) if s.get("required", True)):
                raise RouterError(f"O workflow '{m.get('title')}' precisa de mais imagens do que as disponíveis.")
            binding = await router.bind(ctx, step, sources)
            for d in binding.decisions:
                await run.emit({"type": "decision", "step": idx, **d})
            await self._clef_memory_before_generation(run, comfy, clef, settings, m)
            result = await self._run_step(run, idx, step, m, binding, sources, ctx, settings)
            previous = result[0]
            final = result
        if settings["comfyui"].get("free_vram_after_run"):
            await comfy.free()
        await run.emit({"type": "done", "images": final})

    # ------------------------------------------------------------------ step
    async def _run_step(self, run: Run, idx: int, step: Step, m: dict, binding: Binding,
                        sources: list[Source], ctx: Ctx, settings: dict) -> list[dict]:
        by_key = {s.key: s for s in sources}
        lib = {p["id"]: p for p in self.store.prompts(step.workflow)["presets"]}
        preset = lib[binding.preset_id]
        request = ctx.request
        if settings["writer"].get("enabled") and preset.get("rewrite"):
            request = await writer.rewrite(request, settings["writer"])
        prompt_text = compose_prompt(preset["text"], request)

        drawings = await self._collect_drawings(run, m, binding, by_key)

        comfy = await self.comfy()
        oi = await comfy.object_info()
        wf = self.store.workflow_json(step.workflow)
        overrides: dict[tuple[Any, str], Any] = {}
        if m.get("prompt"):
            overrides[(node_id(m["prompt"]["node"]), m["prompt"].get("widget", "value"))] = prompt_text
        for c in m.get("set", []):
            v = c["value"]
            if v == "random":                       # sorteia a semente (útil em subgraphs com semente fixa)
                v = random.randrange(0, 2 ** 50)
            overrides[(node_id(c["node"]), c["widget"])] = v
        drop = {str(n) for n in m.get("drop", [])}
        keep: set = set()
        fallback: str | None = None
        for slot in m.get("images", []):
            src_key = binding.slots.get(slot["key"])
            node = node_id(slot["node"])
            if src_key is None:
                drop.add(str(node))
                continue
            data = to_png(by_key[src_key].data)
            if slot.get("mask"):
                mask = drawings.get("mask_" + slot["key"])
                if mask is None:
                    raise RouterError("Faltou a máscara para o workflow de inpaint.")
                data = mask_to_alpha_png(data, mask)
            uploaded = await comfy.upload(data, f"cf_{run.id}_{idx}_{slot['key']}.png")
            overrides[(node, "image")] = uploaded
            keep.add(str(node))
            fallback = fallback or uploaded
        for p in m.get("params", []):
            if p.get("type") == "box" and p["key"] in drawings:
                x, y, w, h = drawings[p["key"]]
                for name, v in zip(p["widgets"], (x, y, w, h)):
                    overrides[(node_id(p["node"]), name)] = v

        await run.emit({"type": "step_start", "index": idx, "workflow": step.workflow, "title": m.get("title"),
                        "preset": binding.preset_id, "prompt": prompt_text,
                        "inputs": {k: (by_key[v].label if v else None) for k, v in binding.slots.items()}})
        conv = Converter(oi)
        try:
            api = conv.convert(wf, overrides, drop, fallback, keep)
        except ConversionError:
            oi = await comfy.object_info(refresh=True)
            api = Converter(oi).convert(wf, overrides, drop, fallback, keep)
        out_nodes = [str(n) for n in m.get("outputs", [])]
        missing = [n for n in out_nodes if n not in api]
        if missing:
            raise ConversionError(f"O nó de saída {', '.join(missing)} ficou fora do grafo (entrada obrigatória removida).")
        pid = await comfy.submit(api)

        async def tick(elapsed: float):
            if run.cancelled:
                return
            await run.emit({"type": "progress", "index": idx, "elapsed": round(elapsed, 1)})

        outputs = await comfy.wait(pid, tick, lambda: run.cancelled)
        results: list[dict] = []
        for n in out_nodes:
            for im in (outputs.get(n) or {}).get("images", []):
                data = await comfy.view(im["filename"], im.get("subfolder", ""), im.get("type", "output"))
                results.append(self.images.save(data, im["filename"]))
        if not results:
            raise ComfyError("O workflow terminou, mas não gerou nenhuma imagem no nó de saída.")
        extras = []
        for n, o in outputs.items():
            if n in out_nodes:
                continue
            for im in o.get("images", []):
                extras.append(self.images.save(await comfy.view(im["filename"], im.get("subfolder", ""),
                                                                im.get("type", "temp")), im["filename"]))
        await run.emit({"type": "step_done", "index": idx, "title": m.get("title"), "images": results,
                        "extras": extras})
        return results

    # -------------------------------------------------------------- drawings
    async def _collect_drawings(self, run: Run, m: dict, binding: Binding, by_key: dict[str, Source]) -> dict:
        tasks: list[dict] = []
        for p in m.get("params", []):
            if p.get("type") == "box":
                key = binding.slots.get(p["image"])
                if key:
                    tasks.append({"key": p["key"], "kind": "box", "label": p["label"], "color": p.get("color", "red"),
                                  "image_url": by_key[key].url})
        for s in m.get("images", []):
            if s.get("mask") and binding.slots.get(s["key"]):
                tasks.append({"key": "mask_" + s["key"], "kind": "mask", "label": s.get("draw_label", "Pinte a área a refazer"),
                              "color": "white", "image_url": by_key[binding.slots[s["key"]]].url})
        if not tasks:
            return {}
        value = await self.ask_user(run, {"kind": "draw", "title": m.get("title"), "tasks": tasks})
        out: dict[str, Any] = {}
        for t in tasks:
            v = (value or {}).get(t["key"])
            if v is None:
                raise RouterError(f"Faltou o desenho: {t['label']}")
            if t["kind"] == "box":
                x, y, w, h = [min(1.0, max(0.0, float(n))) for n in v]
                if w <= 0.002 or h <= 0.002:
                    raise RouterError(f"A caixa '{t['label']}' está vazia.")
                out[t["key"]] = [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]
            else:
                import base64
                b64 = v.split(",", 1)[-1]
                out[t["key"]] = base64.b64decode(b64)
        return out

    # --------------------------------------------------------------- history
    @staticmethod
    def _history(conv: dict) -> list[dict]:
        out = []
        for m in conv["messages"][-8:]:
            if m["role"] == "user":
                out.append({"role": "user", "text": m["text"][:300]})
            else:
                plan = next((e for e in m.get("events", []) if e["type"] == "plan"), None)
                out.append({"role": "assistant", "text": ("Executou: " + " -> ".join(s["title"] for s in plan["steps"]))
                            if plan else "Sem execução."})
        return out

    @staticmethod
    def _last_result(conv: dict) -> dict | None:
        for m in reversed(conv["messages"]):
            if m["role"] == "assistant" and m.get("images"):
                return m["images"][0]
        return None
