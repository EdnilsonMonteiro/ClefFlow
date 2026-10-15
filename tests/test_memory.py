from pickflow.memory import GB, Memory, add_clef, from_stats, should_free_comfy, should_unload


def mem(total=24, clef=19.0, ram_free=20, loaded=True, device="cuda", ram_gb=0):
    m = from_stats({"system": {"ram_total": 32 * GB, "ram_free": ram_free * GB},
                    "devices": [{"vram_total": total * GB, "vram_free": 3 * GB}]})
    return add_clef(m, {"loaded": loaded, "device": device, "ram_gb": ram_gb, "vram": {"allocated_gb": clef}})


def test_auto_unloads_when_clef_leaves_too_little_vram():
    go, why = should_unload("auto", mem(24, 19), 16, 10, 2)
    assert go and "VRAM" in why and "19.0" in why


def test_auto_keeps_clef_when_there_is_room():
    go, why = should_unload("auto", mem(48, 19), 16, 10, 2)
    assert not go and "continua carregado" in why


def test_nothing_to_unload_when_clef_is_not_loaded():
    assert should_unload("auto", mem(24, 0, loaded=False), 16, 10, 2) == (False, "")
    assert should_unload("always", mem(24, 0, loaded=False), 16, 10, 2) == (False, "")


def test_policies():
    assert should_unload("never", mem(24, 19), 16, 10, 2) == (False, "")
    assert should_unload("always", mem(80, 5), 16, 10, 2)[0]


def test_ram_criterion_only_when_clef_sits_in_ram():
    gpu = mem(80, 5, ram_free=6, ram_gb=3)               # Clef na GPU: RAM do processo não conta
    assert not should_unload("auto", gpu, 16, 10, 2)[0]
    cpu = mem(80, 0, ram_free=6, device="cpu", ram_gb=17)
    go, why = should_unload("auto", cpu, 16, 10, 2)
    assert go and "RAM" in why


def test_unknown_memory_unloads_for_safety():
    m = Memory()
    assert should_unload("auto", m, 16, 10, 2)[0]


def test_free_comfy_before_clef():
    m = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 2 * GB}]})
    m = add_clef(m, {"loaded": False})
    assert should_free_comfy(m, 20, 2)[0]
    m2 = from_stats({"devices": [{"vram_total": 24 * GB, "vram_free": 23 * GB}]})
    assert not should_free_comfy(add_clef(m2, {"loaded": False}), 20, 2)[0]
    assert not should_free_comfy(add_clef(m, {"loaded": True, "vram": {"allocated_gb": 19}}), 20, 2)[0]
