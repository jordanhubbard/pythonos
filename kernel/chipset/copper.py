"""Copper display list — WAIT line / MOVE register. Not cycle-accurate."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Wait:
    line: int


@dataclass(frozen=True)
class Move:
    reg: str
    value: int


class Copper:
    """Instructions execute as playfield Y advances."""

    def __init__(self) -> None:
        self.instructions: list = []
        self.warnings: list[str] = []

    def reset_pc(self) -> None:
        self._pc = 0

    def apply_line(self, view, y: int) -> None:
        ins = self.instructions
        pc = getattr(self, "_pc", 0)
        n = len(ins)
        while pc < n:
            item = ins[pc]
            if isinstance(item, Wait):
                if y < item.line:
                    break
                pc += 1
                continue
            if isinstance(item, Move):
                _apply_move(view, item.reg, item.value, self.warnings)
                pc += 1
                continue
            pc += 1
        self._pc = pc


def _apply_move(view, reg: str, value: int, warnings: list) -> None:
    name = reg.upper()
    if name.startswith("COLOR") and name[5:].isdigit():
        idx = int(name[5:])
        if 0 <= idx < 32:
            view.palette[idx] = value & 0xFFFFFF
            return
    if name == "BPLCON":
        view.bplcon = value
        return
    if name == "KEY_COLOR":
        view.key_color = value
        return
    if name == "DIWSTART":
        view.diw_start = value
        return
    if name == "DIWSTOP":
        view.diw_stop = value
        return
    warnings.append(f"unknown copper MOVE {reg!r}")
    _log(f"chipset copper: unknown register {reg!r}")


def _log(msg: str) -> None:
    try:
        import _hal  # noqa: F401
        import kernel.log as log
        log.info(msg)
    except Exception:
        pass
