"""Decide, com o Clef, qual workflow (ou sequência) atende ao pedido.

O Clef só devolve probabilidades. Por isso o roteador transforma cada decisão em
perguntas `choice`:
  plan:  `route` (workflow ou caso de uso) e `then` (precisa de mais um workflow depois?)
  bind:  `preset` (qual prompt do workflow) e `slot_<imagem>` (qual imagem vai em cada entrada)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .clef import MAX_OPTIONS, Answer, ClefError, choice_question
from .store import Store

NONE = "none"


@dataclass
class ImageRef:
    id: str
    name: str
    data: bytes
    width: int = 0
    height: int = 0


@dataclass
class Source:
    key: str            # user_1, user_2, previous
    label: str
    data: bytes
    url: str = ""


@dataclass
class Step:
    workflow: str
    prompt: str | None = None            # id do prompt fixado (caso de uso) ou None para o Clef escolher
    inputs: dict[str, str] = field(default_factory=dict)   # slot -> user:1 | previous | none


@dataclass
class Ctx:
    request: str
    images: list[ImageRef]
    history: list[dict] = field(default_factory=list)


@dataclass
class PlanResult:
    steps: list[Step]
    decisions: list[dict] = field(default_factory=list)
    ambiguous: list[dict] | None = None         # candidatos quando o Clef ficou em dúvida


@dataclass
class Binding:
    preset_id: str
    slots: dict[str, str | None]                # slot -> chave da fonte (ou None)
    decisions: list[dict] = field(default_factory=list)


class RouterError(Exception):
    pass


def required_images(m: dict) -> int:
    return sum(1 for s in m.get("images", []) if s.get("required", True))


def needs_drawing(m: dict) -> bool:
    return any(p.get("type") == "box" for p in m.get("params", [])) or any(
        s.get("mask") for s in m.get("images", []))


def decision_view(qid: str, label: str, ans: Answer, names: dict[str, str], source: str) -> dict:
    return {"question": qid, "label": label, "source": source, "chosen": ans.choice,
            "chosen_name": names.get(ans.choice or "", ans.choice), "confidence": round(ans.confidence, 3),
            "options": [{"id": k, "name": names.get(k, k), "p": round(p, 3)} for k, p in ans.top(5)]}


class Router:
    def __init__(self, store: Store, clef):
        self.store = store
        self.clef = clef

    # ---------------------------------------------------------------- state
    def _state(self, ctx: Ctx, task: str, extra: dict | None = None) -> dict:
        st: dict[str, Any] = {
            "task": task,
            "instructions": self.store.clef_config().get("instructions", ""),
            "request": ctx.request,
            "attached_images": [{"index": i + 1, "name": im.name, "size": f"{im.width}x{im.height}"}
                                for i, im in enumerate(ctx.images)],
            "conversation": ctx.history[-6:],
        }
        st.update(extra or {})
        return st

    def _images(self, ctx: Ctx, extra: list[bytes] | None = None) -> list[bytes]:
        return [im.data for im in ctx.images] + list(extra or [])

    # ------------------------------------------------------------ describe
    def workflow_blurb(self, wid: str) -> str:
        m = self.store.manifest(wid)
        desc = self.store.describe(wid)
        slots = ", ".join(f"imagem {i + 1} = {s.get('label', s['key'])}" for i, s in enumerate(m.get("images", [])))
        extra = []
        if slots:
            extra.append("entradas: " + slots)
        if needs_drawing(m):
            extra.append("o usuário desenha caixas/máscara na imagem")
        return desc + (f" [{'; '.join(extra)}]" if extra else "")

    # ----------------------------------------------------------- choices
    async def _ask(self, ctx: Ctx, task: str, specs: list[tuple[str, str, dict[str, tuple[str, str]]]],
                   extra_state: dict | None = None, extra_images: list[bytes] | None = None) -> dict[str, Answer]:
        """specs: (qid, instruções, {opção: (descrição, grupo)}). Divide em 2 etapas se passar de 26 opções."""
        state = self._state(ctx, task, extra_state)
        imgs = self._images(ctx, extra_images)
        small = {q: (ins, opts) for q, ins, opts in specs if len(opts) <= MAX_OPTIONS}
        out: dict[str, Answer] = {}
        if small:
            qs = {q: choice_question(ins, {k: d for k, (d, _g) in opts.items()}) for q, (ins, opts) in small.items()}
            out.update(await self.clef.decide(state, qs, imgs))
        for q, ins, opts in specs:
            if q in small:
                continue
            out[q] = await self._two_stage(state, imgs, q, ins, opts)
        return out

    async def _two_stage(self, state, imgs, qid, ins, opts) -> Answer:
        groups: dict[str, list[str]] = {}
        for k, (_d, g) in opts.items():
            groups.setdefault(g or "outros", []).append(k)
        gcrit = {g: "; ".join(opts[k][0].split(".")[0] for k in ks[:4]) for g, ks in groups.items()}
        gans = (await self.clef.decide(state, {"group": choice_question(ins + " Primeiro escolha a família.", gcrit)},
                                       imgs))["group"]
        members = groups[gans.choice]
        if len(members) == 1:
            return Answer(members[0], gans.confidence, {members[0]: gans.confidence})
        members = members[:MAX_OPTIONS]
        ians = (await self.clef.decide(state, {"item": choice_question(
            ins, {k: opts[k][0] for k in members})}, imgs))["item"]
        probs = {k: p * gans.confidence for k, p in ians.probs.items()}
        return Answer(ians.choice, probs.get(ians.choice, 0.0), probs)

    # ----------------------------------------------------------------- plan
    def steps_from_route(self, route: str) -> list[Step]:
        if route.startswith("recipe__"):
            rid = route[len("recipe__"):]
            for r in self.store.clef_config()["recipes"]:
                if r["id"] == rid:
                    return [Step(s["workflow"], s.get("prompt"), dict(s.get("inputs") or {})) for s in r["steps"]]
            raise RouterError(f"Caso de uso desconhecido: {rid}")
        self.store.manifest(route)
        return [Step(route)]

    async def plan(self, ctx: Ctx, forced: str | None = None) -> PlanResult:
        settings = self.store.settings()["clef"]
        max_steps = int(settings.get("max_steps", 3))
        if forced:
            steps = self.steps_from_route(forced)
            name = self._route_name(forced)
            return PlanResult(steps, [{"question": "route", "label": "Rota", "source": "user", "chosen": forced,
                                       "chosen_name": name, "confidence": 1.0,
                                       "options": [{"id": forced, "name": name, "p": 1.0}]}])
        n_imgs = len(ctx.images)
        enabled = self.store.enabled_workflows()
        names: dict[str, str] = {}
        route_opts: dict[str, tuple[str, str]] = {}
        for wid, m in enabled.items():
            if required_images(m) <= n_imgs:
                route_opts[wid] = (self.workflow_blurb(wid), m.get("group", ""))
                names[wid] = m.get("title", wid)
        for r in self.store.enabled_recipes():
            first = self.store.manifests().get(r["steps"][0]["workflow"])
            pinned = [int(v.split(":")[1]) for st in r["steps"] for v in (st.get("inputs") or {}).values()
                      if isinstance(v, str) and v.startswith("user:")]
            if first and required_images(first) <= n_imgs and max(pinned, default=0) <= n_imgs and all(
                    s["workflow"] in enabled for s in r["steps"]):
                seq = " -> ".join(self.store.manifest(s["workflow"]).get("title", s["workflow"]) for s in r["steps"])
                rid = "recipe__" + r["id"]
                route_opts[rid] = (f"{r.get('description') or r['name']} [sequência: {seq}]", "casos de uso")
                names[rid] = f"{r['name']} (sequência)"
        if not route_opts:
            raise RouterError("Nenhum workflow ativo serve para este pedido com "
                              f"{n_imgs} imagem(ns). Anexe a(s) imagem(ns) necessária(s) ou ative mais workflows.")
        decisions: list[dict] = []
        if len(route_opts) == 1:
            (route,) = route_opts
            decisions.append({"question": "route", "label": "Rota", "source": "único", "chosen": route,
                              "chosen_name": names[route], "confidence": 1.0,
                              "options": [{"id": route, "name": names[route], "p": 1.0}]})
            steps = self.steps_from_route(route)
            return PlanResult(steps, decisions)

        specs = [("route", "Qual fluxo de edição de imagem atende melhor ao pedido do usuário?", route_opts)]
        then_opts: dict[str, tuple[str, str]] = {}
        if max_steps > 1:
            then_opts[NONE] = ("Nenhuma etapa adicional: o primeiro fluxo já resolve o pedido.", "")
            for wid, m in enabled.items():
                if required_images(m) <= n_imgs + 1:
                    then_opts[wid] = (self.workflow_blurb(wid), m.get("group", ""))
                    names.setdefault(wid, m.get("title", wid))
            if len(then_opts) > 1:
                specs.append(("then", "Depois da primeira etapa, o resultado ainda precisa passar por OUTRO fluxo "
                                      "para cumprir tudo o que o usuário pediu? Escolha 'none' se não.", then_opts))
        names[NONE] = "nenhuma"
        try:
            ans = await self._ask(ctx, "route_image_request", specs)
        except ClefError as e:
            raise RouterError(str(e)) from e
        route_ans = ans["route"]
        decisions.append(decision_view("route", "Rota", route_ans, names, self.clef.mode))
        steps = self.steps_from_route(route_ans.choice)
        ambiguous = None
        if route_ans.confidence < float(settings.get("ask_below", 0.45)):
            ambiguous = [{"id": k, "name": names.get(k, k), "p": round(p, 3)} for k, p in route_ans.top(3)]
        then = ans.get("then")
        if then is not None:
            decisions.append(decision_view("then", "Etapa seguinte", then, names, self.clef.mode))
            if (not route_ans.choice.startswith("recipe__") and then.choice not in (None, NONE)
                    and then.choice != route_ans.choice and len(steps) < max_steps):
                steps.append(Step(then.choice))
        return PlanResult(steps[:max_steps] if not route_ans.choice.startswith("recipe__") else steps,
                          decisions, ambiguous)

    def _route_name(self, route: str) -> str:
        if route.startswith("recipe__"):
            rid = route[len("recipe__"):]
            return next((r["name"] for r in self.store.clef_config()["recipes"] if r["id"] == rid), route)
        return self.store.manifest(route).get("title", route)

    # ----------------------------------------------------------------- bind
    async def bind(self, ctx: Ctx, step: Step, sources: list[Source]) -> Binding:
        m = self.store.manifest(step.workflow)
        slots = m.get("images", [])
        presets = self.store.active_presets(step.workflow)
        decisions: list[dict] = []
        by_key = {s.key: s for s in sources}

        # 1) o que o caso de uso já fixou
        slot_map: dict[str, str | None] = {}
        for k, v in (step.inputs or {}).items():
            if v == NONE:
                slot_map[k] = None
            else:
                key = "previous" if v == "previous" else v.replace(":", "_")
                if key in by_key:
                    slot_map[k] = key
        preset_id = step.prompt if step.prompt and any(p["id"] == step.prompt for p in presets) else None
        if preset_id is None and len(presets) == 1:
            preset_id = presets[0]["id"]

        # 2) o que falta, o Clef decide (se houver ambiguidade real)
        open_slots = [s for s in slots if s["key"] not in slot_map]
        questions: dict[str, dict] = {}
        names: dict[str, str] = {}
        if preset_id is None:
            crit = {p["id"]: (p.get("when") or p["name"]) for p in presets}
            questions["preset"] = choice_question(
                f"Qual prompt do fluxo '{m.get('title')}' é o mais adequado a este pedido?", crit)
            names.update({p["id"]: p["name"] for p in presets})
        ask_slots = len(sources) >= 2 and len(open_slots) >= 1
        if ask_slots:
            for s in open_slots:
                crit = {src.key: src.label for src in sources}
                if not s.get("required", True):
                    crit[NONE] = "Não usar esta entrada (opcional)."
                if len(crit) >= 2:
                    questions[f"slot_{s['key']}"] = choice_question(
                        f"Qual imagem deve entrar na entrada '{s.get('label', s['key'])}' do fluxo "
                        f"'{m.get('title')}'? {s.get('hint', '')}".strip(), crit)
        answers: dict[str, Answer] = {}
        if questions:
            try:
                answers = await self.clef.decide(
                    self._state(ctx, "bind_workflow", {"workflow": m.get("title"), "sources": [
                        {"key": s.key, "label": s.label} for s in sources]}),
                    questions, [s.data for s in sources])
            except ClefError as e:
                raise RouterError(str(e)) from e
        if "preset" in answers:
            preset_id = answers["preset"].choice
            decisions.append(decision_view("preset", "Prompt", answers["preset"], names, self.clef.mode))
        preset_id = preset_id or presets[0]["id"]
        probs = {s["key"]: answers[f"slot_{s['key']}"].probs for s in open_slots if f"slot_{s['key']}" in answers}
        slot_map.update(self._assign(open_slots, probs, sources, slot_map))
        self._compact_optional(slots, open_slots, slot_map)
        for s in open_slots:
            a = answers.get(f"slot_{s['key']}")
            if a:
                nm = {src.key: src.label for src in sources}
                nm[NONE] = "não usar"
                decisions.append(decision_view(f"slot_{s['key']}", f"Entrada: {s.get('label', s['key'])}", a, nm,
                                               self.clef.mode))
        return Binding(preset_id, slot_map, decisions)

    @staticmethod
    def _compact_optional(slots: list[dict], open_slots: list[dict], slot_map: dict[str, str | None]):
        """Entradas opcionais preenchidas ficam juntas, sem buracos (imagem 2 vazia e imagem 3 cheia viraria
        'imagem 2' no prompt do modelo). As vazias saem do grafo e não custam tempo."""
        open_keys = {s["key"] for s in open_slots}
        opt = [s["key"] for s in slots if not s.get("required", True) and s["key"] in open_keys
               and not s.get("mask")]
        vals = [slot_map[k] for k in opt if slot_map.get(k)]
        for k, v in zip(opt, vals + [None] * (len(opt) - len(vals))):
            slot_map[k] = v

    @staticmethod
    def _assign(open_slots: list[dict], probs: dict[str, dict[str, float]], sources: list[Source],
                taken: dict[str, str | None]) -> dict[str, str | None]:
        """Atribui fontes às entradas: sem repetir imagem quando houver imagens suficientes."""
        keys = [s.key for s in sources]
        used = {v for v in taken.values() if v}
        out: dict[str, str | None] = {}

        def best(slot):
            return max(probs[slot["key"]].values()) if slot["key"] in probs and probs[slot["key"]] else 0.0

        for s in sorted(open_slots, key=lambda s: (not s.get("required", True), -best(s))):
            ranked = sorted(probs.get(s["key"], {}).items(), key=lambda kv: -kv[1])
            chosen: str | None = "__unset__"
            for k, _p in ranked:
                if k == NONE:
                    if not s.get("required", True):
                        chosen = None
                        break
                    continue
                if k in used and (any(x not in used for x in keys) or not s.get("required", True)):
                    continue            # entrada opcional nunca repete imagem: fica vazia e sai do grafo
                chosen = k
                break
            if chosen == "__unset__":
                free = [k for k in keys if k not in used]
                if s.get("required", True) or free:
                    chosen = (free or keys)[0] if (free or keys) else None
                else:
                    chosen = None
            out[s["key"]] = chosen
            if chosen:
                used.add(chosen)
        return out
