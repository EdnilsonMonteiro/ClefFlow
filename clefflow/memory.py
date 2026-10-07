"""Decide se vale descarregar o Clef antes de gerar a imagem (ou liberar o ComfyUI antes do Clef).

Só decisão: quem lê a memória (ComfyUI `/system_stats`, Clef `/health`) e quem age
(`/v1/unload`, `/free`) é o `runner`. Assim a regra é testável sem GPU.
"""
from __future__ import annotations

from dataclasses import dataclass

GB = 2 ** 30


@dataclass
class Memory:
    vram_total_gb: float | None = None   # GPU inteira (ComfyUI, `vram_total`)
    vram_free_gb: float | None = None    # livre agora, com tudo que está carregado
    ram_total_gb: float | None = None
    ram_free_gb: float | None = None
    clef_loaded: bool = False
    clef_vram_gb: float = 0.0            # o que o Clef ocupa na GPU
    clef_ram_gb: float = 0.0             # RAM do processo do Clef (0 se não informado)
    clef_known: bool = False             # o servidor respondeu /health com `loaded`


def from_stats(stats: dict | None) -> Memory:
    """Lê a resposta de `/system_stats` do ComfyUI (bytes)."""
    m = Memory()
    if not stats:
        return m
    sysd = stats.get("system") or {}
    if sysd.get("ram_total"):
        m.ram_total_gb = sysd["ram_total"] / GB
    if sysd.get("ram_free") is not None and sysd.get("ram_total"):
        m.ram_free_gb = sysd["ram_free"] / GB
    devs = [d for d in (stats.get("devices") or []) if d.get("vram_total")]
    if devs:
        d = max(devs, key=lambda x: x["vram_total"])
        m.vram_total_gb = d["vram_total"] / GB
        if d.get("vram_free") is not None:
            m.vram_free_gb = d["vram_free"] / GB
    return m


def add_clef(m: Memory, health: dict | None) -> Memory:
    """Junta o que o servidor do Clef informa em `/health`."""
    if not health or "loaded" not in health:
        return m
    m.clef_known = True
    m.clef_loaded = bool(health.get("loaded")) and not health.get("mock")
    v = health.get("vram") or {}
    m.clef_vram_gb = float(v.get("allocated_gb") or 0.0)
    m.clef_ram_gb = float(health.get("ram_gb") or 0.0) if str(health.get("device", "")).startswith("cpu") else 0.0
    if m.vram_total_gb is None and v.get("gpu_total_gb"):
        m.vram_total_gb = float(v["gpu_total_gb"])
    return m


def should_unload(policy: str, m: Memory, need_vram_gb: float, need_ram_gb: float,
                  margin_gb: float) -> tuple[bool, str]:
    """(descarregar?, motivo em português). `policy`: auto | always | never."""
    if policy == "never":
        return False, ""
    if m.clef_known and not m.clef_loaded:
        return False, ""                                   # nada a descarregar
    if policy == "always":
        return True, "Clef descarregado antes de gerar (configuração: sempre)."
    # auto
    if m.vram_total_gb is not None:
        room = m.vram_total_gb - m.clef_vram_gb - margin_gb
        if m.clef_vram_gb > 0 and room < need_vram_gb:
            return True, (f"Descarreguei o Clef: ele ocupa {m.clef_vram_gb:.1f} GB dos {m.vram_total_gb:.0f} GB de VRAM e "
                          f"sobrariam {max(room, 0):.1f} GB; o workflow precisa de ~{need_vram_gb:.0f} GB.")
    if m.ram_free_gb is not None and m.clef_ram_gb >= 2 and m.ram_free_gb < need_ram_gb + margin_gb:
        return True, (f"Descarreguei o Clef: só {m.ram_free_gb:.1f} GB de RAM livres e o workflow precisa de "
                      f"~{need_ram_gb:.0f} GB; o Clef usa {m.clef_ram_gb:.1f} GB.")
    if not m.clef_known and m.vram_total_gb is None:
        return True, "Não consegui medir a memória; descarreguei o Clef por segurança."
    return False, ("Memória suficiente: o Clef continua carregado."
                   if m.vram_total_gb is not None else "")


def should_free_comfy(m: Memory, need_clef_vram_gb: float, margin_gb: float) -> tuple[bool, str]:
    """Antes de o Clef decidir: se ele não está carregado e falta VRAM para carregá-lo, libera o ComfyUI."""
    if m.clef_known and m.clef_loaded:
        return False, ""
    if m.vram_free_gb is None:
        return False, ""
    if m.vram_free_gb < need_clef_vram_gb + margin_gb:
        return True, (f"Liberei os modelos do ComfyUI: só {m.vram_free_gb:.1f} GB de VRAM livres e o Clef precisa "
                      f"de ~{need_clef_vram_gb:.0f} GB para carregar.")
    return False, ""
