"""The range fields follow the laden jump range from the ship tracker; an Auto switch hands them back.

Spec: docs/superpowers/specs/2026-11-08-ship-range-and-neutron-start-design.md (J2, J3, J4, J7,
UI: range panel). Everything here runs on the Tk thread and never raises: a broken update is
logged and the next one tries again.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from edmrn.ext.fsd import supercharge_multiplier
from edmrn.ext.neutron_resume import BOOST_LABELS
from edmrn.ext.ship import ShipState
from edmrn.logger import get_logger

logger = get_logger('ShipPanel')

WAITING = "Waiting for ship data from the journal"


def _floor(value: float) -> float:
    """Floor to 0.1 LY; the epsilon keeps 58.7 from becoming 58.6 through float noise."""
    return math.floor(value * 10 + 1e-9) / 10


def _fmt(value: float) -> str:
    return f"{_floor(value):.1f}"


def range_text(state: ShipState) -> str:
    laden = state.laden
    if state.drive is None or laden is None:
        if state.max_jump_range is not None:
            return (f"Max {_fmt(state.max_jump_range)} LY "
                    "(drive not recognised, laden range unknown)")
        return WAITING
    parts = [f"Laden {_fmt(laden)} LY"]
    current = state.current
    if current is not None:
        parts.append(f"now {_fmt(current)}")
    parts.append(f"max {_fmt(state.drive.max_jump_range)}")
    factor = supercharge_multiplier(state.drive, "N")
    parts.append(f"neutron ×{factor:g} {_fmt(factor * laden)}")
    return " · ".join(parts)


def _field_value(state: ShipState) -> Optional[str]:
    laden = state.laden
    if laden is not None:
        return _fmt(laden)
    if state.max_jump_range is not None:
        return _fmt(state.max_jump_range)
    return None


class RangeBinding:
    """Connects the tracker state to the range fields, the labels and the Auto switch."""

    def __init__(self, app) -> None:
        self.app = app
        self.auto = True
        self.state = ShipState()
        self._written: Optional[str] = None
        self._last_drive: Any = None
        self._panels: list[tuple[Any, Any]] = []

    def register_panel(self, label, checkbox_var) -> None:
        self._panels.append((label, checkbox_var))
        try:
            label.configure(text=range_text(self.state))
            checkbox_var.set(self.auto)
        except Exception as e:
            logger.debug(f"Range panel not initialised: {e}")

    def on_state(self, state: ShipState) -> None:
        try:
            self.state = state
            text = range_text(state)
            for label, _var in list(self._panels):
                try:
                    label.configure(text=text)
                except Exception as e:
                    logger.debug(f"Range label not updated: {e}")
            if self.auto:
                self._apply_auto(state)
        except Exception as e:
            logger.error(f"Range update failed: {e}")

    def _apply_auto(self, state: ShipState) -> None:
        value = _field_value(state)
        if value is not None:
            var = self.app.jump_range
            if var.get() != value:
                var.set(value)
            self._written = value
        drive = state.drive
        if drive is not None and drive != self._last_drive:
            self._last_drive = drive
            boost = getattr(self.app, 'neutron_boost_var', None)
            if boost is not None:
                boost.set(BOOST_LABELS['x6' if drive.mk2 else 'x4'])

    def set_auto(self, value: bool) -> None:
        try:
            self.auto = bool(value)
            for _label, var in list(self._panels):
                try:
                    var.set(self.auto)
                except Exception as e:
                    logger.debug(f"Auto checkbox not updated: {e}")
            if self.auto:
                self._last_drive = None
                self._apply_auto(self.state)
        except Exception as e:
            logger.error(f"Auto switch failed: {e}")

    def on_key(self, var=None) -> None:
        """A key in a range entry: text that differs from what we wrote means the user typed."""
        try:
            var = var if var is not None else self.app.jump_range
            if self.auto and self._written is not None and var.get() != self._written:
                self.set_auto(False)
        except Exception as e:
            logger.error(f"Range key handler failed: {e}")


def binding_for(app) -> RangeBinding:
    binding = getattr(app, 'ship_range', None)
    if not isinstance(binding, RangeBinding):
        binding = RangeBinding(app)
        app.ship_range = binding
    return binding


def _inside(widget, parent) -> bool:
    return widget is not None and (getattr(widget, 'master', None) is parent
                                   or str(widget).startswith(str(parent) + '.'))


def add_range_panel(app, parent) -> None:
    """Add the range label and the Auto checkbox under the range entry in ``parent``."""
    try:
        import customtkinter as ctk

        binding = binding_for(app)
        label = ctk.CTkLabel(parent, text=WAITING, anchor="w", justify="left")
        label.pack(anchor="w", pady=(4, 0))
        var = ctk.BooleanVar(value=binding.auto)
        check = ctk.CTkCheckBox(parent, text="Auto", variable=var,
                                command=lambda: binding.set_auto(var.get()))
        check.pack(anchor="w", pady=(4, 0))
        binding.register_panel(label, var)
        for name, var_name in (('range_entry', 'jump_range'),
                               ('neutron_range_entry', 'neutron_range_var')):
            entry = getattr(app, name, None)
            if _inside(entry, parent):
                entry.bind('<KeyRelease>',
                           lambda _e, n=var_name: binding.on_key(getattr(app, n, None)),
                           add='+')
    except Exception as e:
        logger.error(f"Range panel not added: {e}")
