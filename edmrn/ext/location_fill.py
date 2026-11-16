"""Location button for the route plotter's fields.

Implements docs/superpowers/specs/2026-11-17-location-fill-design.md, requirements L1-L6 (and L11
for the layout): a `📍` button beside a system field fills it with the commander's current system
from the journal; optionally Spansh is asked off the Tk thread whether it knows the system.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk

from edmrn.ext.location import LocationState
from edmrn.ext.start_check import StartCheck, check_plot_system
from edmrn.logger import get_logger

logger = get_logger('LocationFill')

UNKNOWN_LOCATION = "Current system unknown - no location in the journal yet."
NO_COORDINATES = "Current coordinates unknown - the journal has no position for '{system}' yet."
CHECKING = "Checking '{system}' at Spansh..."
SYSTEM_TOOLTIP = "Fill in your current system from the journal"
COORDS_TOOLTIP = "Fill in your current coordinates from the journal"
BUTTON_TEXT = "📍"

CHECKING_COLOR = "gray60"
KNOWN_COLOR = "#4CAF50"
WARNING_COLOR = "#FFA500"

TOOLTIP_DELAY_MS = 500


def current_location(app) -> Optional[LocationState]:
    """The tracker's state when it knows a system, else None. Never raises."""
    try:
        state = app.location_tracker.state
        return state if state is not None and state.system else None
    except Exception:
        return None


def format_coord(value: float) -> str:
    text = f"{value:.5f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def thread_runner(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="edmrn-location-check", daemon=True).start()


class Tooltip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(TOOLTIP_DELAY_MS, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._window is not None:
            return
        try:
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.wm_attributes("-topmost", True)
            tk.Label(win, text=self.text, background="#2b2b2b", foreground="#ffffff",
                     relief="solid", borderwidth=1, padx=6, pady=2).pack()
            win.wm_geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 4}")
            self._window = win
        except tk.TclError:
            self._window = None

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class SystemFieldFill:
    def __init__(self, app, field, *, check_spansh: bool,
                 after_fill: Optional[Callable[[str], None]] = None,
                 check=check_plot_system, runner=thread_runner):
        self.app = app
        self.field = field
        self.check_spansh = check_spansh
        self.after_fill = after_fill
        self._check = check
        self._runner = runner
        self._generation = 0
        self._subject: Optional[str] = None   # the field text the hint speaks about

        field.entry.pack_forget()
        field.columnconfigure(0, weight=1)
        field.entry.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self.button = ctk.CTkButton(field, text=BUTTON_TEXT, width=32, command=self.fill)
        self.button.grid(row=0, column=1, padx=(2, 2), pady=2)
        Tooltip(self.button, SYSTEM_TOOLTIP)
        self.hint = ctk.CTkLabel(field, text="", wraplength=300, justify="left", anchor="w")
        self.hint.grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self.hint.grid_remove()
        field.entry_var.trace_add("write", self._on_write)

    def _on_write(self, *_):
        if self._subject is not None and self.field.get() != self._subject:
            self._hide_hint()

    def _hide_hint(self):
        self._subject = None
        try:
            self.hint.grid_remove()
        except tk.TclError:
            pass

    def _show_hint(self, text: str, color: str, subject: str):
        self._subject = subject
        self.hint.configure(text=text, text_color=color)
        self.hint.grid()

    def fill(self):
        state = current_location(self.app)
        if state is None:
            self._show_hint(UNKNOWN_LOCATION, WARNING_COLOR, self.field.get())
            return
        system, coords = state.system, state.coords
        self.field.set(system)
        if not self.check_spansh:
            self._hide_hint()
            if self.after_fill:
                self.after_fill(system)
            return
        self._generation += 1
        generation = self._generation
        self._show_hint(CHECKING.format(system=system), CHECKING_COLOR, system)

        def job():
            try:
                result = self._check(system, coords)
            except Exception:
                logger.exception("Spansh check for %r failed", system)
                return
            self.app.root.after(0, self._apply, generation, system, result)

        self._runner(job)

    def _apply(self, generation: int, system: str, result: StartCheck):
        try:
            if generation != self._generation or self.field.get().strip() != system:
                return
            if result.name != system:
                self._subject = result.name   # before the write, so the write keeps the hint
                self.field.set(result.name)
            self._show_hint(result.hint, KNOWN_COLOR if result.known else WARNING_COLOR, result.name)
        except tk.TclError:
            pass


def _default_warn(app) -> Callable[[str], None]:
    def warn(message: str) -> None:
        from edmrn.gui import WarningDialog   # lazy: importing the GUI module is heavy
        WarningDialog(app, "Current Location", message)
    return warn


def _set_entry(entry, text: str) -> None:
    entry.delete(0, "end")
    entry.insert(0, text)


def fill_coordinates(app, entries: tuple, name_entry=None, *,
                     warn: Optional[Callable[[str], None]] = None) -> bool:
    """Write the current X, Y, Z (and system name) into the entries; False with a warning if unknown."""
    warn = warn or _default_warn(app)
    state = current_location(app)
    if state is None:
        warn(UNKNOWN_LOCATION)
        return False
    if state.coords is None:
        warn(NO_COORDINATES.format(system=state.system))
        return False
    for entry, value in zip(entries, state.coords):
        _set_entry(entry, format_coord(value))
    if name_entry is not None:
        _set_entry(name_entry, state.system)
    return True


def add_coords_button(app, master, entries: tuple, *, name_entry=None,
                      warn: Optional[Callable[[str], None]] = None, **grid) -> ctk.CTkButton:
    button = ctk.CTkButton(
        master, text=BUTTON_TEXT, width=32,
        command=lambda: fill_coordinates(app, entries, name_entry, warn=warn))
    button.grid(**grid)
    Tooltip(button, COORDS_TOOLTIP)
    return button


def add_neutron_tab_buttons(app) -> None:
    try:
        SystemFieldFill(app, app.neutron_from_autocomplete, check_spansh=True)
        SystemFieldFill(app, app.neutron_to_autocomplete, check_spansh=True)
        add_coords_button(
            app, app.nearest_find_btn.master,
            (app.nearest_x_entry, app.nearest_y_entry, app.nearest_z_entry),
            row=0, column=8, padx=(0, 8), pady=6)
    except Exception:
        logger.exception("Could not add the location buttons to the Neutron tab")


def add_galaxy_tab_buttons(app) -> None:
    try:
        SystemFieldFill(app, app.galaxy_source_autocomplete, check_spansh=True)
        SystemFieldFill(app, app.galaxy_dest_autocomplete, check_spansh=True)
    except Exception:
        logger.exception("Could not add the location buttons to the Galaxy tab")


def add_custom_route_buttons(tab) -> None:
    try:
        for field in (tab.starting_entry, tab.ending_entry, tab.system_entry):
            SystemFieldFill(tab.app, field, check_spansh=False)
    except Exception:
        logger.exception("Could not add the location buttons to the Custom Route tab")


def add_coords_dialog_button(app, frame, name_entry, entries: tuple) -> None:
    try:
        add_coords_button(app, frame, entries, name_entry=name_entry,
                          row=0, column=2, padx=(4, 0), pady=4)
    except Exception:
        logger.exception("Could not add the location button to the coordinates dialog")


def add_system_info_button(app, section) -> None:
    try:
        if app is None:
            return
        SystemFieldFill(app, section.system_info_entry, check_spansh=False,
                        after_fill=lambda _name: section.fetch_callback())
    except Exception:
        logger.exception("Could not add the location button to System Info")
