"""Reusable Tk widgets: color picker, palette swatch strips, rendered-texture thumbnails,
a dynamic key/value table, and a checklist for material/part selectors."""
from __future__ import annotations

import io
import re
import tkinter as tk
from tkinter import colorchooser, ttk
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageTk

HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def normalize_hex(text: str) -> Optional[str]:
    t = text.strip()
    if not t.startswith("#"):
        t = "#" + t
    return t.upper() if HEX_RE.match(t) else None


def tone_to_hex(tone) -> str:
    if isinstance(tone, str):
        return normalize_hex(tone) or tone
    r, g, b = tone[0], tone[1], tone[2]
    return "#%02X%02X%02X" % (r, g, b)


class ColorField(ttk.Frame):
    """A color swatch (click to open the system color picker) + an editable hex entry."""

    def __init__(self, parent, value: str, on_change: Callable[[str], None]):
        super().__init__(parent)
        self.on_change = on_change
        self.value = normalize_hex(value) or "#808080"
        self.canvas = tk.Canvas(self, width=24, height=20, highlightthickness=1,
                                 highlightbackground="#888888", cursor="hand2")
        self._rect = self.canvas.create_rectangle(0, 0, 24, 20, fill=self.value, outline="")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", lambda _e: self._pick())
        self.entry_var = tk.StringVar(value=self.value)
        self.entry = ttk.Entry(self, textvariable=self.entry_var, width=9)
        self.entry.pack(side="left", padx=(4, 0))
        self.entry.bind("<Return>", self._on_entry)
        self.entry.bind("<FocusOut>", self._on_entry)

    def _pick(self) -> None:
        _rgb, hexv = colorchooser.askcolor(color=self.value, title="Pick a color", parent=self)
        if hexv:
            self._set(hexv.upper())

    def _on_entry(self, _evt=None) -> None:
        norm = normalize_hex(self.entry_var.get())
        if norm and norm != self.value:
            self._set(norm)
        elif not norm:
            self.entry_var.set(self.value)

    def _set(self, hexv: str) -> None:
        self.value = hexv
        self.entry_var.set(hexv)
        self.canvas.itemconfigure(self._rect, fill=hexv)
        self.on_change(hexv)

    def set_value(self, hexv: str) -> None:
        norm = normalize_hex(hexv) or hexv
        self.value = norm
        self.entry_var.set(norm)
        self.canvas.itemconfigure(self._rect, fill=norm)


class SwatchStrip(ttk.Frame):
    """A read-only row of color swatches -- used to preview a derived palette ramp."""

    def __init__(self, parent, count: int = 5, size: int = 22):
        super().__init__(parent)
        self.size = size
        self.canvas = tk.Canvas(self, width=size * count, height=size,
                                 highlightthickness=1, highlightbackground="#888888")
        self.canvas.pack()
        self.rects = [self.canvas.create_rectangle(i * size, 0, (i + 1) * size, size, outline="")
                      for i in range(count)]

    def set_colors(self, colors: Sequence[Any]) -> None:
        for i, rect in enumerate(self.rects):
            if i < len(colors):
                self.canvas.itemconfigure(rect, fill=tone_to_hex(colors[i]), state="normal")
            else:
                self.canvas.itemconfigure(rect, state="hidden")

    def clear(self) -> None:
        for rect in self.rects:
            self.canvas.itemconfigure(rect, state="hidden")


def _checkerboard(size: Tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    px = img.load()
    step = 6
    for y in range(h):
        for x in range(w):
            light = ((x // step) + (y // step)) % 2 == 0
            px[x, y] = (150, 150, 150, 255) if light else (110, 110, 110, 255)
    return img


class Thumbnail(ttk.Frame):
    """One rendered part texture, upscaled with nearest-neighbour over a checkerboard,
    the same way mintech_textures.py's --preview contact sheet renders tiles."""

    def __init__(self, parent, caption: str = "", scale: int = 7):
        super().__init__(parent, padding=2)
        self.scale = scale
        self.image_label = tk.Label(self, relief="groove", bd=1, bg="#2b2b2b")
        self.image_label.pack()
        self.caption_label = ttk.Label(self, text=caption, anchor="center", font=("TkDefaultFont", 9))
        self.caption_label.pack(fill="x")
        self._photo = None

    def set_png(self, data: Optional[bytes], missing_text: str = "n/a") -> None:
        if not data:
            self._photo = None
            self.image_label.configure(image="", text=missing_text, width=12, height=6, compound="center")
            return
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        size = (img.width * self.scale, img.height * self.scale)
        big = img.resize(size, Image.NEAREST)
        canvas = _checkerboard(size)
        canvas.alpha_composite(big)
        self._photo = ImageTk.PhotoImage(canvas)
        self.image_label.configure(image=self._photo, text="", width=size[0], height=size[1])

    def set_caption(self, text: str) -> None:
        self.caption_label.configure(text=text)


class KeyValueEditor(ttk.Frame):
    """Editable rows for a flat dict (material.inputs, material.stats, ...)."""

    def __init__(self, parent, data: Dict[str, Any], on_change: Callable[[], None],
                 numeric: bool = False, value_width: int = 30):
        super().__init__(parent)
        self.data = data
        self.on_change = on_change
        self.numeric = numeric
        self.value_width = value_width
        self.rows_frame = ttk.Frame(self)
        self.rows_frame.pack(fill="x")
        ttk.Button(self, text="+ Add field", command=self._add_row).pack(anchor="w", pady=(2, 0))
        self.rebuild()

    def rebuild(self) -> None:
        for w in self.rows_frame.winfo_children():
            w.destroy()
        for key, value in list(self.data.items()):
            self._make_row(key, value)

    def _make_row(self, key: str, value: Any) -> None:
        row = ttk.Frame(self.rows_frame)
        row.pack(fill="x", pady=1)
        kvar = tk.StringVar(value=key)
        vvar = tk.StringVar(value=str(value))
        ke = ttk.Entry(row, textvariable=kvar, width=14)
        ke.pack(side="left")
        ve = ttk.Entry(row, textvariable=vvar, width=self.value_width)
        ve.pack(side="left", padx=(4, 4))

        def commit(_evt=None, orig=key):
            self._commit_row(orig, kvar.get().strip(), vvar.get())

        ke.bind("<Return>", commit)
        ke.bind("<FocusOut>", commit)
        ve.bind("<Return>", commit)
        ve.bind("<FocusOut>", commit)
        ttk.Button(row, text="x", width=2, command=lambda: self._remove(key)).pack(side="left")

    def _commit_row(self, orig_key: str, new_key: str, raw_value: str) -> None:
        value: Any = raw_value
        if self.numeric:
            try:
                value = int(raw_value)
            except ValueError:
                try:
                    value = float(raw_value)
                except ValueError:
                    pass
        changed = orig_key != new_key or self.data.get(orig_key) != value
        if not changed:
            return
        if orig_key in self.data and orig_key != new_key:
            del self.data[orig_key]
        if new_key:
            self.data[new_key] = value
        self.on_change()
        self.rebuild()

    def _remove(self, key: str) -> None:
        self.data.pop(key, None)
        self.on_change()
        self.rebuild()

    def _add_row(self) -> None:
        base, n = "field", 1
        while base in self.data:
            n += 1
            base = f"field{n}"
        self.data[base] = 0 if self.numeric else ""
        self.on_change()
        self.rebuild()


class ScrollableFrame(ttk.Frame):
    """A vertically-scrolling container; put widgets in .body."""

    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)
        self._window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas = canvas

        def _wheel(event):
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        # Bind the wheel globally only while the pointer is actually over this canvas,
        # so several ScrollableFrames (one per tab) don't fight over bind_all.
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))


class CheckList(ttk.Frame):
    """A scrollable checklist -- e.g. picking which parts/materials a family selects."""

    def __init__(self, parent, options: Iterable[str], selected: Iterable[str],
                 on_change: Callable[[List[str]], None], height: int = 8):
        super().__init__(parent)
        self.on_change = on_change
        self.vars: Dict[str, tk.BooleanVar] = {}
        canvas = tk.Canvas(self, height=height * 20, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        selected_set = set(selected)
        for opt in options:
            var = tk.BooleanVar(value=opt in selected_set)
            self.vars[opt] = var
            ttk.Checkbutton(inner, text=opt, variable=var,
                             command=self._changed).pack(anchor="w")

    def _changed(self) -> None:
        self.on_change(self.selected())

    def selected(self) -> List[str]:
        return [name for name, var in self.vars.items() if var.get()]
