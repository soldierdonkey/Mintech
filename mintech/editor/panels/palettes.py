"""Palettes tab: the named 5-tone palettes materials point at (palettes.json), the rock
palettes ore backgrounds use (ores.json), and the hue-shift ramp definitions (textures.json)
that turn a single base color into a 5-tone ramp for material.palette = {ramp, base}."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List

from .. import jsonfmt
from ..widgets import ColorField


class PaletteListEditor(ttk.Frame):
    """Edits a {name: ["#RRGGBB", ...]} dict in place -- used for both the named metal/gem
    palettes (palettes.json) and the rock palettes ore blocks sit in (ores.json)."""

    def __init__(self, parent, get_dict: Callable[[], Dict[str, List[str]]], on_change: Callable[[], None]):
        super().__init__(parent)
        self.get_dict = get_dict
        self.on_change = on_change
        self.current: str = ""

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        self.listbox = tk.Listbox(left, exportselection=False, width=16)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="+ New", command=self._new).pack(side="left")
        ttk.Button(btns, text="Rename", command=self._rename).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete", command=self._delete).pack(side="left")

        right = ttk.Frame(paned, padding=8)
        paned.add(right, weight=2)
        self.swatch_row = ttk.Frame(right)
        self.swatch_row.pack(anchor="w")
        tools = ttk.Frame(right)
        tools.pack(anchor="w", pady=(8, 0))
        ttk.Button(tools, text="+ tone", command=self._add_tone).pack(side="left")
        ttk.Button(tools, text="- tone", command=self._remove_tone).pack(side="left", padx=4)

        self.refresh()

    def refresh(self, select: str = "") -> None:
        self.listbox.delete(0, "end")
        names = sorted(self.get_dict().keys())
        for n in names:
            self.listbox.insert("end", n)
        target = select or self.current or (names[0] if names else "")
        if target in names:
            self.listbox.selection_set(names.index(target))
            self._load(target)
        else:
            self.current = ""
            self._render_swatches([])

    def _on_select(self, _evt=None) -> None:
        sel = self.listbox.curselection()
        if sel:
            self._load(self.listbox.get(sel[0]))

    def _load(self, name: str) -> None:
        self.current = name
        self._render_swatches(self.get_dict().get(name, []))

    def _render_swatches(self, tones: List[str]) -> None:
        for w in self.swatch_row.winfo_children():
            w.destroy()
        for i, hexv in enumerate(tones):
            field = ColorField(self.swatch_row, hexv, lambda h, idx=i: self._set_tone(idx, h))
            field.pack(side="left", padx=3)

    def _set_tone(self, index: int, hexv: str) -> None:
        if not self.current:
            return
        tones = self.get_dict()[self.current]
        tones[index] = hexv
        self.on_change()

    def _add_tone(self) -> None:
        if not self.current:
            return
        tones = self.get_dict()[self.current]
        tones.append(tones[-1] if tones else "#808080")
        self.on_change()
        self._render_swatches(tones)

    def _remove_tone(self) -> None:
        if not self.current:
            return
        tones = self.get_dict()[self.current]
        if tones:
            tones.pop()
            self.on_change()
            self._render_swatches(tones)

    def _new(self) -> None:
        d = self.get_dict()
        base, n = "new_palette", 1
        name = base
        while name in d:
            n += 1
            name = f"{base}_{n}"
        d[name] = ["#3B3B3B", "#5A5A5A", "#747474", "#8F8F8F", "#B4B4B4"]
        self.on_change()
        self.refresh(select=name)

    def _rename(self) -> None:
        if not self.current:
            return
        new_name = _ask_text(self, "Rename palette", "New name:", self.current)
        if not new_name or new_name == self.current:
            return
        d = self.get_dict()
        if new_name in d:
            messagebox.showerror("Rename palette", f"'{new_name}' already exists.")
            return
        d[new_name] = d.pop(self.current)
        self.on_change()
        self.refresh(select=new_name)

    def _delete(self) -> None:
        if not self.current:
            return
        if not messagebox.askyesno("Delete palette", f"Delete palette '{self.current}'?"):
            return
        self.get_dict().pop(self.current, None)
        self.on_change()
        self.refresh()


def _ask_text(parent, title, prompt, initial) -> str:
    from tkinter import simpledialog
    return simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent) or ""


class RampsEditor(ttk.Frame):
    """The hue-shift ramp definitions (textures.json textures.ramps) are a small, rarely
    touched, deeply-nested structure -- edited here as validated JSON text rather than a
    bespoke widget per field."""

    def __init__(self, parent, get_dict: Callable[[], dict], on_change: Callable[[], None]):
        super().__init__(parent)
        self.get_dict = get_dict
        self.on_change = on_change

        ttk.Label(self, text="ramps (metal / gem / ore hue-shift definitions) -- edit as JSON, then Apply:",
                  wraplength=560, justify="left").pack(anchor="w", padx=6, pady=(6, 2))
        self.text = tk.Text(self, height=18, width=70, undo=True)
        self.text.pack(fill="both", expand=True, padx=6)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Apply", command=self._apply).pack(side="left")
        ttk.Button(row, text="Revert", command=self.load).pack(side="left", padx=4)
        self.error_label = ttk.Label(row, text="", foreground="#b00020")
        self.error_label.pack(side="left", padx=8)
        self.load()

    def load(self) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", jsonfmt.dumps(self.get_dict()))
        self.error_label.configure(text="")

    def _apply(self) -> None:
        raw = self.text.get("1.0", "end")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as err:
            self.error_label.configure(text=f"Invalid JSON: {err}")
            return
        if not isinstance(parsed, dict):
            self.error_label.configure(text="Must be a JSON object.")
            return
        d = self.get_dict()
        d.clear()
        d.update(parsed)
        self.error_label.configure(text="Applied.")
        self.on_change()


class PalettesPanel(ttk.Frame):
    def __init__(self, parent, workspace, status):
        super().__init__(parent)
        self.ws = workspace
        self.status = status

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        named_tab = ttk.Frame(notebook)
        notebook.add(named_tab, text="Named palettes (palettes.json)")
        ttk.Label(named_tab, text="Metal/gem palettes materials point at via material.palette.",
                  padding=6).pack(anchor="w")
        PaletteListEditor(named_tab, self.ws.named_palettes,
                          lambda: self.ws.mark_dirty("palettes.json")).pack(fill="both", expand=True)

        rock_tab = ttk.Frame(notebook)
        notebook.add(rock_tab, text="Rock palettes (ores.json)")
        ttk.Label(rock_tab, text="Backgrounds ore blocks sit in (stone/deepslate/netherrack/endstone).",
                  padding=6).pack(anchor="w")
        PaletteListEditor(rock_tab, self.ws.rock_palettes,
                          lambda: self.ws.mark_dirty("ores.json")).pack(fill="both", expand=True)

        ramps_tab = ttk.Frame(notebook)
        notebook.add(ramps_tab, text="Ramps (textures.json, advanced)")
        RampsEditor(ramps_tab, self.ws.ramps, lambda: self.ws.mark_dirty("textures.json")).pack(fill="both", expand=True)
