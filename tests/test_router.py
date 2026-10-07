import asyncio

import pytest

from clefflow.clef import Answer, MockClef, normalize_answer
from clefflow.router import Ctx, ImageRef, Router, Source, Step
from clefflow.store import Store


def img(i):
    return ImageRef(f"i{i}", f"img{i}.png", b"x", 10, 10)


def run(coro):
    return asyncio.run(coro)


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
    st = Stub({"route": "bodyswap_turbo", "then": "none"})
    r = Router(Store(project), st)
    plan = run(r.plan(Ctx("troque o corpo", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["bodyswap_turbo"]
    qs = st.calls[0][1]
    assert set(qs) == {"route", "then"} and all(2 <= len(q["criteria"]) <= 26 for q in qs.values())
    assert qs["route"]["criteria"]["bodyswap_turbo"].startswith("Troca a PESSOA")
    assert "recipe__trocar_pessoa_e_fundo" not in qs["route"]["criteria"]    # a receita fixa a imagem 3
    assert st.calls[0][0]["attached_images"][1]["name"] == "img2.png"


def test_clef_can_chain_a_second_workflow(project):
    st = Stub({"route": "bodyswap_turbo", "then": "edit_turbo"})
    plan = run(Router(Store(project), st).plan(Ctx("troque o corpo e depois mude a cor", [img(1), img(2)])))
    assert [s.workflow for s in plan.steps] == ["bodyswap_turbo", "edit_turbo"]


def test_max_steps_one_disables_then_question(project):
    s = Store(project)
    s.save_settings({"clef": {"max_steps": 1}})
    st = Stub({"route": "edit_turbo"})
    run(Router(s, st).plan(Ctx("x", [img(1)])))
    assert set(st.calls[0][1]) == {"route"}


def test_workflows_that_need_more_images_are_not_offered(project):
    st = Stub()
    run(Router(Store(project), st).plan(Ctx("x", [img(1)])))
    crit = st.calls[0][1]["route"]["criteria"]
    assert "edit_turbo" in crit and "bodyswap_turbo" not in crit and "transfer_object" not in crit


def test_without_images_only_generation_is_possible(project):
    store = Store(project)
    cfg = store.clef_config()
    cfg["workflows"]["t2i"]["enabled"] = False
    store.save_clef_config(cfg)
    st = Stub()
    plan = run(Router(Store(project), st).plan(Ctx("desenhe um gato", [])))
    assert [s.workflow for s in plan.steps] == ["t2i_turbo"] and not st.calls   # opção única, nem pergunta


def test_disabled_workflow_is_never_offered_and_recipe_hidden(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["bodyswap_turbo"]["enabled"] = False
    s.save_clef_config(cfg)
    st = Stub()
    run(Router(s, st).plan(Ctx("x", [img(1), img(2), img(3)])))
    crit = st.calls[0][1]["route"]["criteria"]
    assert "bodyswap_turbo" not in crit and not any(k.startswith("recipe__") for k in crit)


def test_recipe_is_a_route_and_expands_to_steps(project):
    st = Stub({"route": "recipe__trocar_pessoa_e_fundo"})
    plan = run(Router(Store(project), st).plan(Ctx("troque a pessoa e o fundo", [img(1), img(2), img(3)])))
    assert [s.workflow for s in plan.steps] == ["bodyswap_turbo", "transfer_object_turbo"]
    assert plan.steps[1].inputs == {"image_1": "user:3", "image_2": "previous"}


def test_forced_route_skips_clef(project):
    st = Stub()
    plan = run(Router(Store(project), st).plan(Ctx("x", [img(1), img(2)]), forced="headswap_turbo"))
    assert plan.steps[0].workflow == "headswap_turbo" and not st.calls and plan.decisions[0]["source"] == "user"


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
    st = Stub({"slot_image_1": "user_2", "slot_image_2": "user_1", "preset": "bfs"})
    ctx = Ctx("coloque a pessoa da imagem 1 na cena da imagem 2", [img(1), img(2)])
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(s, st).bind(ctx, Step("bodyswap_turbo"), src))
    assert b.slots == {"image_1": "user_2", "image_2": "user_1", "image_3": None} and b.preset_id == "bfs"


def test_bind_never_reuses_an_image_when_clef_repeats_itself(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "user_1"})
    src = [Source("user_1", "Imagem 1", b"a"), Source("user_2", "Imagem 2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("headswap_turbo", prompt="bfs_head"), src))
    assert sorted(b.slots.values()) == ["user_1", "user_2"]


def test_bind_respects_recipe_pins_and_skips_clef(project):
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b"), Source("previous", "prev", b"c")]
    step = Step("transfer_object_turbo", "objeto", {"image_1": "user:2", "image_2": "previous"})
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), step, src))
    assert b.slots == {"image_1": "user_2", "image_2": "previous"} and b.preset_id == "objeto" and not st.calls


def test_bind_optional_slot_can_be_left_empty(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "none", "slot_image_3": "none", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit_quality"), src))
    assert b.slots["image_2"] is None and b.slots["image_3"] is None


def test_bind_optional_slots_are_compacted_without_gaps(project):
    # Clef pôs a referência na entrada 3 e deixou a 2 vazia: o prompt do modelo não pode ver "imagem 3" sem "imagem 2".
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "none", "slot_image_3": "user_2", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit_quality"), src))
    assert b.slots == {"image_1": "user_1", "image_2": "user_2", "image_3": None}


def test_bind_optional_slot_never_repeats_an_image(project):
    st = Stub({"slot_image_1": "user_1", "slot_image_2": "user_2", "slot_image_3": "user_1", "preset": "livre"})
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(Store(project), st).bind(Ctx("x", [img(1), img(2)]), Step("edit_turbo"), src))
    assert b.slots == {"image_1": "user_1", "image_2": "user_2", "image_3": None}


def test_only_prompts_selected_in_clef_file_are_offered(project):
    s = Store(project)
    cfg = s.clef_config()
    cfg["workflows"]["bodyswap_turbo"]["prompts"] = ["bfs_extra"]
    s.save_clef_config(cfg)
    st = Stub()
    src = [Source("user_1", "i1", b"a"), Source("user_2", "i2", b"b")]
    b = run(Router(s, st).bind(Ctx("x", [img(1), img(2)]), Step("bodyswap_turbo"), src))
    assert b.preset_id == "bfs_extra" and "preset" not in st.calls[0][1]


def test_mock_clef_prefers_matching_description(project):
    r = Router(Store(project), MockClef())
    plan = run(r.plan(Ctx("quero remover o fundo da foto, só o sujeito", [img(1)])))
    assert plan.steps[0].workflow == "background_removal"


def test_clef_picks_move_or_swap_prompt_from_the_request(project):
    """O mesmo fluxo (caixas vermelha e verde) tem dois prompts; o Clef escolhe pelo texto do pedido."""
    r = Router(Store(project), MockClef())
    src = [Source("user_1", "i1", b"a")]
    for wid in ("object_move", "object_move_turbo"):
        swap = run(r.bind(Ctx("trocar a lâmpada e o vaso de lugar entre si", [img(1)]), Step(wid), src))
        move = run(r.bind(Ctx("mover o vaso para o destino vazio", [img(1)]), Step(wid), src))
        assert (swap.preset_id, move.preset_id) == ("trocar", "mover"), wid


def test_move_and_swap_route_to_the_red_green_workflow(project):
    r = Router(Store(project), MockClef())
    plan = run(r.plan(Ctx("trocar dois objetos de lugar entre si na imagem, marquei as caixas", [img(1)])))
    assert plan.steps[0].workflow in ("object_move", "object_move_turbo")


def test_clef_tells_add_from_replace_in_the_single_red_box_flow(project):
    """Um quadrado vermelho só: 'adicionar' (lugar vazio) e 'substituir' (já há algo) são prompts diferentes."""
    r = Router(Store(project), MockClef())
    src = [Source("user_1", "i1", b"a")]
    add = run(r.bind(Ctx("adicionar um vaso de flores no lugar vazio marcado, acrescentar um objeto novo", [img(1)]),
                     Step("replace_redbox"), src))
    sub = run(r.bind(Ctx("substituir o carro que está no quadrado por uma moto", [img(1)]), Step("replace_redbox"), src))
    assert (add.preset_id, sub.preset_id) == ("adicionar", "substituir")


def test_adding_an_object_routes_to_replace_redbox_not_move_or_swap(project):
    r = Router(Store(project), MockClef())
    for text in ("coloque um gato no quadrado vermelho, um objeto novo que não existe na foto",
                 "adicione um abajur no lugar vazio que marquei com o quadrado vermelho"):
        plan = run(r.plan(Ctx(text, [img(1)])))
        assert plan.steps[0].workflow == "replace_redbox", text


def test_add_prompt_keeps_scene_and_only_places_inside_the_box(project):
    p = {x["id"]: x for x in Store(project).active_presets("replace_redbox")}
    assert {"substituir", "adicionar"} <= set(p)
    txt = p["adicionar"]["text"]
    assert "{request}" in txt and "red square" in txt and "do not remove" in txt
    assert "green" not in txt          # só UMA caixa
