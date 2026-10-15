import asyncio

import pytest

from pickflow.clef import Answer, MockClef, normalize_answer
from pickflow.router import Ctx, ImageRef, Router, Source, Step
from pickflow.store import Store


def img(i):
    return ImageRef(f"i{i}", f"img{i}.png", b"x", 10, 10)


def run(coro):
    return asyncio.run(coro)


def call_with(st, qid):
    """A primeira chamada ao Clef que traz a pergunta `qid` (antes dela pode haver a análise das imagens)."""
    return next(c for c in st.calls if qid in c[1])


class Stub:
    """Clef falso que devolve respostas roteirizadas e guarda as perguntas recebidas."""
    mode = "stub"

    def __init__(self, picks=None):
        self.picks, self.calls = picks or {}, []

    async def decide(self, state, questions, images=None):
        self.calls.append((state, questions, images))
        out = {}
        for q, spec in questions.items():
            keys = list(spec["criteria"])
            pick = self.picks.get(q, keys[0])
            probs = {k: (0.8 if k == pick else 0.2 / max(1, len(keys) - 1)) for k in keys}
            out[q] = Answer(pick, probs[pick], probs)
        return out


def test_normalize_answer_variants():
    a = normalize_answer({"choice": "a", "confidence": 0.9, "probabilities": {"a": 0.9, "b": 0.1}})
    assert a.choice == "a" and a.confidence == 0.9 and a.top(1)[0][0] == "a"
    n = normalize_answer({"probability": 0.8})
    assert n.choice == "yes" and n.probs["no"] == pytest.approx(0.2)
    lst = normalize_answer({"probabilities": [0.1, 0.9]}, ["x", "y"])
    assert lst.choice == "y" and lst.confidence == 0.9


def test_plan_uses_choice_questions_within_clef_limits(project):
    st = Stub({"route": "edit", "then": "none"})
    r = Router(Store(project), st)
    plan = run(r.plan(Ctx("troque o corpo", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit"]
    assert set(st.calls[0][1]) == {"img_1", "img_2"}                          # 1ª análise: o que há em cada imagem
    qs = call_with(st, "route")[1]
    assert set(qs) == {"route", "then"} and all(2 <= len(q["criteria"]) <= 26 for q in qs.values())
    assert qs["route"]["criteria"]["edit"].startswith("General image editor")
    assert "recipe__trocar_pessoa_e_fundo" not in qs["route"]["criteria"]    # a receita fixa a imagem 3
    assert call_with(st, "route")[0]["attached_images"][1]["name"] == "img2.png"


def test_clef_can_chain_a_second_workflow(project):
    st = Stub({"route": "edit", "then": "background_removal"})
    plan = run(Router(Store(project), st).plan(Ctx("troque o corpo e depois tire o fundo", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit", "background_removal"]


def test_then_step_needs_the_images_and_some_confidence(project):
    # "Make both woman from the <image1> kissing each other": o Clef real escolhia editar e depois 'transferir objeto
    # entre imagens', que precisa de 2 fotos e só tinha 1. A etapa seguinte recebe o resultado NO LUGAR de uma foto.
    st = Stub({"route": "edit", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("make both women kiss", [img(1)])))
    then = call_with(st, "route")[1]["then"]["criteria"]
    assert "edit" in then and "background_removal" in then
    assert "transfer_object" not in then and "transfer_multi" not in then

    class Unsure(Stub):                                  # etapa seguinte escolhida com pouca certeza: fica de fora
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "then" in out:
                out["then"] = Answer("background_removal", 0.3, {"background_removal": 0.3, "none": 0.29})
            return out
    plan = run(Router(Store(project), Unsure({"route": "edit"})).plan(Ctx("x", [img(1)])))
    assert [s.workflow for s in plan.steps] == ["edit"]


def test_none_weighs_more_in_the_next_step_question(project):
    # 'none' vale 1,5x (clef.then_none_weight) e a etapa precisa de 50% (clef.then_min) depois do peso
    class Lean(Stub):
        def __init__(self, p):
            super().__init__({"route": "edit"})
            self.p = p

        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "then" in out:
                out["then"] = Answer("background_removal", self.p, {"background_removal": self.p, "none": 1 - self.p})
            return out
    steps = lambda p: [s.workflow for s in run(Router(Store(project), Lean(p)).plan(Ctx("x", [img(1)]))).steps]  # noqa: E731
    assert steps(0.55) == ["edit"]                           # 0,55 contra 0,45 x 1,5: 'none' ganha
    assert steps(0.7) == ["edit", "background_removal"]      # 0,7 contra 0,3 x 1,5 = 0,61 depois do peso


def test_max_steps_one_disables_then_question(project):
    s = Store(project)
    s.save_settings({"clef": {"max_steps": 1}})
    st = Stub({"route": "edit"})
    run(Router(s, st).plan(Ctx("x", [img(1)])))
    assert set(st.calls[0][1]) == {"route"}


def test_workflows_that_need_more_images_are_not_offered(project):
    st = Stub()
    run(Router(Store(project), st).plan(Ctx("x", [img(1)])))
    crit = st.calls[0][1]["route"]["criteria"]
    assert "edit" in crit and "transfer_object" not in crit        # transferir precisa de 2 imagens


def test_without_images_only_generation_is_possible(project):
    st = Stub()
    plan = run(Router(Store(project), st).plan(Ctx("desenhe um gato", [])))
    assert [s.workflow for s in plan.steps] == ["t2i"] and not st.calls   # opção única, nem pergunta


def test_disabled_workflow_is_never_offered_and_recipe_hidden(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["edit"]["enabled"] = False
    s.save_clef_config(cfg)
    st = Stub()
    run(Router(s, st).plan(Ctx("x", [img(1), img(2), img(3)])))
    crit = call_with(st, "route")[1]["route"]["criteria"]
    assert "edit" not in crit and "recipe__trocar_pessoa_e_fundo" not in crit


def test_recipe_is_a_route_and_expands_to_steps(project):
    st = Stub({"route": "recipe__trocar_pessoa_e_fundo", "role_scene": "user_2", "role_person": "user_3",
               "role_background": "user_1"})
    plan = run(Router(Store(project), st).plan(Ctx("troque a pessoa e o fundo", [img(1), img(2), img(3)])))
    assert [s.workflow for s in plan.steps] == ["edit", "transfer_object"]
    assert (plan.steps[0].prompt, plan.steps[0].mode) == ("trocar_pessoas", "turbo") and plan.steps[1].mode == "turbo"
    # os papéis, não a ordem de envio, decidem quem é quem: cena = imagem 2, pessoa = imagem 3, ambiente = imagem 1
    assert plan.steps[0].inputs == {"image_1": "user:2", "image_2": "user:3"}
    assert plan.steps[1].inputs == {"image_1": "user:1", "image_2": "previous"}
    assert {d["question"] for d in plan.decisions} >= {"role_scene", "role_person", "role_background"}


def test_forced_route_skips_clef(project):
    st = Stub()
    plan = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)]), forced="edit"))
    assert plan.steps[0].workflow == "edit" and not st.calls and plan.decisions[0]["source"] == "user"


def test_low_confidence_marks_plan_ambiguous(project):
    s = Store(project)
    s.save_settings({"clef": {"ask_below": 0.95}})
    plan = run(Router(s, Stub()).plan(Ctx("x", [img(1), img(2)])))
    assert plan.ambiguous and len(plan.ambiguous) == 3


def test_more_than_26_options_are_split_in_two_stages(project):
    st = Stub()
    r = Router(project and Store(project), st)
    opts = {f"w{i}": (f"descricao {i}", f"g{i % 5}") for i in range(40)}
    ans = run(r._ask(Ctx("x", [img(1)]), "t", [("route", "qual?", opts)]))
    assert len(st.calls) == 2 and all(len(q["criteria"]) <= 26 for c in st.calls for q in c[1].values())
    assert ans["route"].choice in opts


def test_bind_assigns_each_slot_a_different_image_following_clef(project):
    s = Store(project)
    st = Stub({"slot_image_1": "user_2", "slot_image_2": "user_1", "preset": "livre"})
    ctx = Ctx("coloque a pessoa da imagem 1 na cena da imagem 2", [img(1), img(2)])
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(s, st).bind(ctx, Step("edit"), src))
    assert b.slots == slots8(image_1="user_2", image_2="user_1")
    assert b.preset_id == "livre"


def test_bind_never_reuses_an_image_when_clef_repeats_itself(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "user_1"})
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit", prompt="estilo"), src))
    assert sorted(v for v in b.slots.values() if v) == ["user_1", "user_2"]


def test_bind_respects_recipe_pins_and_skips_clef(project):
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b"), Source("previous", "prev", b"c")]
    step = Step("transfer_object", "objeto", {"image_1": "user:2", "image_2": "previous"}, "turbo", None, {"relacao": "adicionar"})
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), step, src))
    assert b.slots == {"image_1": "user_2", "image_2": "previous"} and b.preset_id == "objeto" and not st.calls


def test_bind_optional_slot_can_be_left_empty(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "none", "slot_image_3": "none", "slot_image_4": "none", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src))
    assert b.slots["image_2"] is None and b.slots["image_3"] is None


def test_bind_optional_slot_left_empty_by_clef_stays_empty_and_there_are_no_gaps(project):
    # o Clef só decide as primeiras entradas (tantas quanto imagens); as outras seis ficam vazias e saem do grafo
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "none", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src))
    assert b.slots == slots8(image_1="user_1") and set(b.slots) == {f"image_{i}" for i in range(1, 9)}
    asked = {q for c in st.calls for q in c[1]}
    assert "slot_image_3" not in asked and "slot_image_2" in asked


def test_bind_optional_slot_never_repeats_an_image(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "user_2", "slot_image_3": "user_1", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src))
    assert b.slots == slots8(image_1="user_1", image_2="user_2")


def test_only_prompts_selected_in_clef_file_are_offered(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["edit"]["prompts"] = ["estilo"]
    s.save_clef_config(cfg)
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(s, st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), src))
    assert b.preset_id == "estilo" and "preset" not in st.calls[0][1]


def test_mock_clef_prefers_matching_description(project):
    r = Router(Store(project), MockClef())
    plan = run(r.plan(Ctx("remove the background of the photo, keep only the subject", [img(1)])))
    assert plan.steps[0].workflow == "background_removal"


def test_clef_picks_move_or_swap_prompt_from_the_request(project):
    """O mesmo fluxo (caixas vermelha e verde) tem dois prompts; o Clef escolhe pelo texto do pedido."""
    r = Router(Store(project), MockClef())
    src = [Source("user_1", "i1", b"a")]
    for wid in ("object_move",):
        swap = run(r.bind(Ctx("make the lamp and the vase swap places with each other", [img(1)]), Step(wid), src))
        move = run(r.bind(Ctx("move the vase to the empty destination", [img(1)]), Step(wid), src))
        assert (swap.preset_id, move.preset_id) == ("trocar", "mover"), wid


def test_move_and_swap_route_to_the_red_green_workflow(project):
    r = Router(Store(project), MockClef())
    plan = run(r.plan(Ctx("make two objects swap places inside the image, I marked the red and green boxes", [img(1)])))
    assert plan.steps[0].workflow == "object_move"


def test_clef_tells_add_from_replace_in_the_single_red_box_flow(project):
    """Um quadrado vermelho só: 'adicionar' (lugar vazio) e 'substituir' (já há algo) são prompts diferentes."""
    r = Router(Store(project), MockClef())
    src = [Source("user_1", "i1", b"a")]
    add = run(r.bind(Ctx("add a vase of flowers at the marked empty spot, insert a new object", [img(1)]),
                     Step("replace_redbox"), src))
    sub = run(r.bind(Ctx("replace the car that is already inside the square with a motorcycle", [img(1)]), Step("replace_redbox"), src))
    assert (add.preset_id, sub.preset_id) == ("adicionar", "substituir")


def test_adding_an_object_routes_to_replace_redbox_not_move_or_swap(project):
    r = Router(Store(project), MockClef())
    for text in ("add a new cat in the marked spot, nothing to remove",
                 "add a lamp at the empty spot I marked with the red square"):
        plan = run(r.plan(Ctx(text, [img(1)])))
        assert plan.steps[0].workflow == "replace_redbox", text


def test_add_prompt_keeps_scene_and_only_places_inside_the_box(project):
    p = {x["id"]: x for x in Store(project).active_presets("replace_redbox")}
    assert {"substituir", "adicionar"} <= set(p)
    txt = p["adicionar"]["text"]
    assert "{request}" in txt and "red square" in txt and "do not remove" in txt
    assert "green" not in txt          # só UMA caixa


def test_internal_steps_are_never_offered_alone_but_the_recipes_that_use_them_are(project):
    st = Stub()
    run(Router(Store(project), st).plan(Ctx("cole a pessoa neste cenário", [img(1), img(2)])))
    crit = call_with(st, "route")[1]["route"]["criteria"]
    assert "colar_recortar" not in crit and "colar_posicionar" not in crit
    assert {"recipe__colar_e_fundir", "recipe__colar_sem_fundir", "colar_fundir"} <= set(crit)
    assert "colar_recortar" not in call_with(st, "route")[1].get("then", {}).get("criteria", {})


def test_paste_recipes_route_by_the_request_and_pin_the_right_images(project):
    r = Router(Store(project), Stub({"role_background": "user_1", "role_subject": "user_2"}))
    plan = run(r.plan(Ctx("colar a pessoa da segunda imagem no cenário da primeira", [img(1), img(2)]), "recipe__colar_e_fundir"))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar", "colar_fundir"]
    assert plan.steps[0].inputs == {"image_1": "user:2"} and plan.steps[1].inputs == {"image_1": "user:1", "image_2": "previous"}
    assert plan.steps[2].inputs == {"image_1": "previous"}
    swapped = run(Router(Store(project), Stub({"role_background": "user_2", "role_subject": "user_1"})).plan(
        Ctx("cole a cadeira da imagem 1 no quarto da imagem 2", [img(1), img(2)]), "recipe__colar_e_fundir"))
    assert swapped.steps[0].inputs == {"image_1": "user:1"} and swapped.steps[1].inputs["image_1"] == "user:2"   # papéis, não ordem
    with_one = run(Router(Store(project), MockClef()).plan(Ctx("colar a pessoa no cenário", [img(1)])))
    assert [s.workflow for s in with_one.steps] != ["colar_recortar", "colar_posicionar", "colar_fundir"]   # uma imagem só: não há o que colar


def test_blend_prompt_depends_on_person_or_object(project):
    r = Router(Store(project), MockClef())
    src = [Source("user_1", "i1", b"a")]
    obj = run(r.bind(Ctx("blend the pasted object, it is furniture, without a person", [img(1)]), Step("colar_fundir"), src))
    per = run(r.bind(Ctx("blend the pasted person into the place", [img(1)]), Step("colar_fundir"), src))
    assert (per.preset_id, obj.preset_id) == ("pessoa", "objeto")


# ------------------------------------------------- modos e LoRAs escolhidas pelo Clef
def slots8(**kw):
    """As oito entradas de imagem do grafo; as não citadas ficam vazias."""
    return {f"image_{i}": kw.get(f"image_{i}") for i in range(1, 9)}


def _src(n):
    return [Source(f"user_{i}", f"Imagem {i}", b"x") for i in range(1, n + 1)]


def test_mode_is_a_separate_question_and_defaults_to_turbo(project):
    st = Stub()
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1)]), Step("object_move", "mover"), _src(1)))
    assert b.mode == "turbo" and set(st.calls[0][1]) == {"mode"}
    crit = st.calls[0][1]["mode"]["criteria"]
    assert list(crit) == ["turbo", "quality"] and "default" in crit["turbo"]
    q = run(Router(Store(project), Stub({"mode": "quality"})).bind(Ctx("x", [img(1)]), Step("object_move", "mover"), _src(1)))
    assert q.mode == "quality"
    pinned = Stub()
    p = run(Router(Store(project), pinned).bind(Ctx("x", [img(1)]), Step("object_move", "mover", mode="quality"), _src(1)))
    assert p.mode == "quality" and not pinned.calls              # o caso de uso fixou o modo: sem pergunta


def test_unsure_mode_falls_back_to_the_default(project):
    class Unsure(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            if "mode" in questions:
                out["mode"] = Answer("quality", 0.4, {"quality": 0.4, "turbo": 0.6})
            return out
    b = run(Router(Store(project), Unsure()).bind(Ctx("x", [img(1)]), Step("object_move", "mover"), _src(1)))
    assert b.mode == "turbo"


def test_clef_decides_loras_one_question_each(project):
    st = Stub({"lora_bfs_body": "usar", "preset": "livre"})
    b = run(Router(Store(project), st).bind(Ctx("troque o corpo", [img(1), img(2)]), Step("edit"), _src(2)))
    qs = st.calls[0][1]
    assert {"lora_bfs_body", "lora_bfs_head", "mode", "preset"} <= set(qs)
    assert list(qs["lora_bfs_body"]["criteria"]) == ["none", "usar"] and "BODY" in qs["lora_bfs_body"]["criteria"]["usar"]
    assert b.loras == ["bfs_body"] and any(d["question"] == "lora_bfs_body" for d in b.decisions)


def test_lora_already_in_the_prompt_is_not_added_twice(project):
    st = Stub({"lora_bfs_body": "usar", "lora_bfs_head": "usar", "preset": "trocar_pessoas"})
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert b.loras == []            # o prompt escolhido já traz uma LoRA de troca (bfs_body)


def test_loras_of_the_same_group_are_not_turned_on_together(project):
    class Both(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            out["lora_bfs_body"] = Answer("usar", 0.9, {"usar": 0.9, "none": 0.1})
            out["lora_bfs_head"] = Answer("usar", 0.95, {"usar": 0.95, "none": 0.05})
            return out
    b = run(Router(Store(project), Both({"preset": "livre"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert b.loras == ["bfs_head"]                       # as duas trocam pessoa: fica a mais segura
    c = run(Router(Store(project), Both({"preset": "trocar_pessoas"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert c.loras == []                                 # o prompt já trouxe uma LoRA de troca


def test_lora_cap_keeps_the_most_confident(project):
    s = Store(project)
    s.save_settings({"clef": {"max_loras": 1}})

    class Pick(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            out["lora_bfs_body"] = Answer("usar", 0.9, {"usar": 0.9, "none": 0.1})
            out["lora_bfs_head"] = Answer("usar", 0.7, {"usar": 0.7, "none": 0.3})
            return out
    b = run(Router(s, Pick({"preset": "livre"})).bind(Ctx("x", [img(1), img(2)]), Step("edit"), _src(2)))
    assert b.loras == ["bfs_body"]


def test_loras_that_need_two_images_are_not_asked_with_one_image(project):
    st = Stub()
    run(Router(Store(project), st).bind(Ctx("x", [img(1)]), Step("edit"), _src(1)))
    asked = {q for q in st.calls[0][1] if q.startswith("lora_")}
    assert "lora_bfs_body" not in asked and "lora_outfit_swap" not in asked      # as de troca precisam de 2 imagens


def test_recipe_can_pin_the_loras_and_skip_the_questions(project):
    st = Stub()
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit", "livre", mode="turbo", loras=["bfs_head"]), _src(2)))
    assert b.loras == ["bfs_head"] and not any(q.startswith("lora_") for c in st.calls for q in c[1])


def test_mock_clef_turns_on_the_lora_that_matches_the_request(project):
    r = Router(Store(project), MockClef())
    body = run(r.bind(Ctx("swap the person in the scene for the person in the reference", [img(1), img(2)]), Step("edit"), _src(2)))
    head = run(r.bind(Ctx("swap only the face and the head, a face swap", [img(1), img(2)]), Step("edit"), _src(2)))
    plain = run(r.bind(Ctx("make the shirt blue", [img(1), img(2)]), Step("edit"), _src(2)))
    assert body.preset_id == "trocar_pessoas" and body.vars["scope"] == "both"        # sem dizer o quê: corpo e cabeça
    assert head.preset_id == "trocar_pessoas" and head.vars["scope"] == "head"
    assert plain.loras == [] and not plain.preset_id.startswith("trocar")


def test_clef_picks_person_or_object_cutout_from_the_request(project):
    src = [Source("user_1", "i1", b"a")]
    bind = lambda picks, text: run(Router(Store(project), Stub(picks)).bind(Ctx(text, [img(1)]), Step("colar_recortar"), src))  # noqa: E731
    per = bind({"var_alvo": "person"}, "cole ela neste cenário")
    obj = bind({"var_alvo": "chair", "var_cor": "blue"}, "cole a cadeira azul neste cenário")
    alone = bind({"var_alvo": "alone"}, "cole este produto")
    assert (per.mode, obj.mode, alone.mode) == ("pessoa", "objeto", "fundo")       # a opção escolhida diz o modo
    assert obj.vars == {"alvo": "chair", "cor": "blue"}
    store = Store(project)
    from pickflow import pvars
    preset = store.prompts("colar_recortar")["presets"][0]
    assert pvars.render(preset, obj.vars)["text"] == "blue chair"                  # o que o SAM3 procura
    assert store.manifest("colar_recortar", "pessoa")["prompt"]["node"] == 4 and store.manifest("colar_recortar", "objeto")["prompt"]["node"] == 4
    assert store.manifest("colar_recortar", "objeto")["file"] == "PickFlow Colar - Recortar Pessoa.json"
    assert store.manifest("colar_recortar", "fundo")["file"] == "PickFlow Colar - Recortar.json" and not store.manifest("colar_recortar", "fundo").get("prompt")
    assert store.manifest("colar_recortar", "pessoa")["review"] == "cutout"


def test_internal_steps_are_not_in_the_forced_route_list(project):
    from pickflow.app import create_app  # noqa: F401  (a lista é feita pela interface a partir do campo internal)
    s = Store(project)
    assert s.manifest("colar_recortar")["internal"] and s.manifest("colar_posicionar")["internal"]


def test_recipes_only_appear_with_the_exact_number_of_images(project):
    for n, has_paste, has_swap in ((2, True, False), (3, False, True), (4, False, False)):
        st = Stub()
        run(Router(Store(project), st).plan(Ctx("x", [img(i) for i in range(1, n + 1)])))
        crit = call_with(st, "route")[1]["route"]["criteria"]
        assert ("recipe__colar_e_fundir" in crit) is has_paste and ("recipe__trocar_pessoa_e_fundo" in crit) is has_swap


def test_images_are_described_first_and_the_labels_reach_every_later_question(project):
    class Seer(Stub):
        async def decide(self, state, questions, images=None):
            out = await super().decide(state, questions, images)
            for q in questions:
                if q.startswith("img_"):
                    kind = {"img_1": "place", "img_2": "person", "img_3": "person"}[q]
                    out[q] = Answer(kind, 0.9, {kind: 0.9})
            return out
    st = Seer({"route": "recipe__trocar_pessoa_e_fundo"})
    ctx = Ctx("coloque a pessoa da imagem 3 na pose da imagem 2. O lugar é a imagem 1.", [img(1), img(2), img(3)])
    run(Router(Store(project), st).plan(ctx))
    assert ctx.contents == {1: "place/setting without people", 2: "person", 3: "person"}
    seen = call_with(st, "route")[0]["attached_images"]
    assert seen[0]["content"] == "place/setting without people" and seen[1]["content"] == "person"
    role = call_with(st, "role_background")[1]["role_background"]
    assert "Image 1 (place/setting without people)" in role["criteria"]["user_1"]


def test_roles_follow_the_clef_even_when_positions_disagree_and_never_repeat_an_image(project):
    r = Router(Store(project), Stub({"route": "recipe__trocar_pessoa_e_fundo", "role_scene": "user_2",
                                     "role_person": "user_2", "role_background": "user_1"}))
    plan = run(r.plan(Ctx("x", [img(1), img(2), img(3)])))
    used = [plan.steps[0].inputs["image_1"], plan.steps[0].inputs["image_2"], plan.steps[1].inputs["image_1"]]
    assert len(set(used)) == 3                                     # a mesma imagem não vira dois papéis


def test_roles_fall_back_to_the_order_of_upload_when_the_clef_has_no_opinion(project):
    class Blank(Stub):
        async def decide(self, state, questions, images=None):
            return {q: Answer(next(iter(sp["criteria"])), 0.34, {k: 1 / len(sp["criteria"]) for k in sp["criteria"]})
                    for q, sp in questions.items()}
    r = Router(Store(project), Blank())
    plan = run(r.plan(Ctx("x", [img(1), img(2), img(3)]), forced="recipe__trocar_pessoa_e_fundo"))
    assert plan.steps[0].inputs == {"image_1": "user:1", "image_2": "user:2"} and plan.steps[1].inputs["image_1"] == "user:3"


def test_a_doubtful_route_gets_a_second_look_among_the_top_candidates(project):
    class Doubt(Stub):
        async def decide(self, state, questions, images=None):
            self.calls.append((state, questions, images))
            out = {}
            for q, sp in questions.items():
                keys = list(sp["criteria"])
                if q == "route" and len(keys) > 3:                  # 1ª vez: muita dúvida
                    out[q] = Answer(keys[0], 0.3, {k: (0.3 if i == 0 else 0.7 / (len(keys) - 1)) for i, k in enumerate(keys)})
                elif q == "route":                                  # 2ª vez, só 3 opções: escolhe a última com firmeza
                    out[q] = Answer(keys[-1], 0.7, {k: (0.7 if k == keys[-1] else 0.15) for k in keys})
                else:
                    out[q] = Answer(keys[0], 0.8, {k: 0.8 if k == keys[0] else 0.1 for k in keys})
            return out
    st = Doubt()
    plan = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)])))
    routes = [c for c in st.calls if "route" in c[1]]
    assert len(routes) == 2 and len(routes[1][1]["route"]["criteria"]) == 3
    assert any(d["question"] == "route_2" for d in plan.decisions) and plan.ambiguous is None


def test_recipe_images_and_roles_survive_saving_the_clef_config(project):
    s = Store(project)
    cfg = s.clef_config()
    swap = next(r for r in cfg["recipes"] if r["id"] == "trocar_pessoa_e_fundo")
    assert swap["images"] == 3 and [x["key"] for x in swap["roles"]] == ["scene", "person", "background"]
    swap["roles"][2]["about"] = "meu texto"
    s.save_clef_config(cfg)
    again = next(r for r in Store(project).clef_config()["recipes"] if r["id"] == "trocar_pessoa_e_fundo")
    assert again["roles"][2]["about"] == "meu texto" and again["images"] == 3


def test_multi_transfer_puts_the_picked_destination_first_and_the_objects_after(project):
    st = Stub({"var_dest": "3", "var_objects": "2", "mode": "turbo"})
    ctx = Ctx("coloque os objetos das imagens 1 e 2 na imagem 3", [img(1), img(2), img(3)])
    b = run(Router(Store(project), st).bind(ctx, Step("transfer_multi"), _src(3)))
    assert b.preset_id == "varios"
    assert b.slots["image_1"] == "user_3"                       # o destino vira a entrada 1
    assert b.slots["image_2"] == "user_1" and b.slots["image_3"] == "user_2"
    assert b.vars["objects"] == "2" and b.vars["dest"] == "3"
    assert not b.slots["image_4"]


def test_multi_transfer_default_destination_is_the_first_image(project):
    b = run(Router(Store(project), Stub({"var_objects": "3"})).bind(Ctx("x", [img(1), img(2), img(3), img(4)]),
                                                                    Step("transfer_multi"), _src(4)))
    assert [b.slots[f"image_{i}"] for i in range(1, 5)] == ["user_1", "user_2", "user_3", "user_4"]


# ------------------------------------------------------- levar algo de uma imagem para a outra: intenção
def test_replace_intent_moves_a_paste_route_to_transfer_with_the_replace_variant(project):
    st = Stub({"route": "recipe__colar_e_fundir", "then": "none", "paste_intent": "substituir"})
    plan = run(Router(Store(project), st).plan(Ctx("coloque o coringa no lugar do cara de vermelho", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["transfer_object"] and plan.steps[0].vars == {"relacao": "substituir"}
    d = {x["question"]: x for x in plan.decisions}
    assert d["paste_intent"]["chosen"] == "substituir" and d["route_intent"]["chosen"] == "transfer_object"


def test_add_intent_moves_transfer_or_edit_to_paste_and_keeps_only_paste(project):
    st = Stub({"route": "transfer_object", "then": "none", "paste_intent": "acrescentar"})
    plan = run(Router(Store(project), st).plan(Ctx("coloque o batman sentado no banco", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar", "colar_fundir"]
    st = Stub({"route": "recipe__colar_sem_fundir", "then": "none", "paste_intent": "acrescentar"})
    plan = run(Router(Store(project), st).plan(Ctx("só cole o peão ao lado", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["colar_recortar", "colar_posicionar"]


def test_hold_intent_goes_to_transfer_and_other_edits_keep_their_route(project):
    st = Stub({"route": "recipe__colar_e_fundir", "then": "none", "paste_intent": "segurar"})
    plan = run(Router(Store(project), st).plan(Ctx("faça o batman segurar a tesoura", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["transfer_object"] and plan.steps[0].vars == {"relacao": "adicionar"}
    st = Stub({"route": "edit", "then": "none", "paste_intent": "outra"})
    plan = run(Router(Store(project), st).plan(Ctx("troque o rosto dele pelo da imagem 2", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit"]


def test_person_swap_intent_keeps_or_moves_the_route_to_the_editor_and_the_next_step_never_repeats_the_asked_route(project):
    # "replace the woman in image 1 with the man in image 2, face and body": o editor (prompt Trocar pessoas), não o transferir objeto
    st = Stub({"route": "edit", "then": "none", "paste_intent": "trocar"})
    plan = run(Router(Store(project), st).plan(Ctx("replace the woman in image 1 with the man in image 2, face and body", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit"]
    st = Stub({"route": "transfer_object", "then": "none", "paste_intent": "trocar"})
    plan = run(Router(Store(project), st).plan(Ctx("body swap the woman with the man", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["edit"]
    # o `then` é respondido antes de a rota mudar pela intenção: se ele pede a rota de que partiu, não vira um 2º passo igual
    st = Stub({"route": "edit", "then": "edit", "paste_intent": "substituir"})
    plan = run(Router(Store(project), st).plan(Ctx("put the man in place of the woman", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["transfer_object"]


def test_the_intent_question_is_only_asked_with_two_images_and_a_paste_like_route(project):
    st = Stub({"route": "object_move", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("mova a cadeira", [img(1), img(2)])))
    assert not any("paste_intent" in c[1] for c in st.calls)
    st = Stub({"route": "edit", "then": "none"})
    run(Router(Store(project), st).plan(Ctx("deixe mais quente", [img(1)])))
    assert not any("paste_intent" in c[1] for c in st.calls)
