"""Raw tab: a JSON text fallback for every mintech/*.json fragment, including the ones
without a dedicated tab (parts.json's ascii art, templates.json, textures.json's shaders,
worldgen.json, and the mintech.json index itself). 'Easily edit' for the common cases is the
Materials/Ores/Palettes/Families tabs; this is the catch-all for everything else."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from .. import jsonfmt
from .. import mintech_bridge as bridge


class RawPanel(ttk.Frame):
    def __init__(self, parent, workspace, status, on_apply: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.ws = workspace
        self.status = status
        self.on_apply = on_apply
        self.current_file: Optional[str] = None

        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="file:").pack(side="left")
        self.file_combo = ttk.Combobox(top, state="readonly", width=16, values=bridge.ALL_FILES)
        self.file_combo.pack(side="left", padx=4)
        self.file_combo.bind("<<ComboboxSelected>>", lambda _e: self._load(self.file_combo.get()))
        ttk.Button(top, text="Refresh (pull in-memory edits)", command=self._refresh).pack(side="left", padx=4)
        ttk.Button(top, text="Reload from disk", command=self._reload).pack(side="left", padx=4)
        ttk.Button(top, text="Apply", command=self._apply).pack(side="left", padx=4)
        ttk.Button(top, text="Save this file", command=self._save).pack(side="left", padx=4)
        self.dirty_label = ttk.Label(top, text="")
        self.dirty_label.pack(side="left", padx=8)

        self.text = tk.Text(self, undo=True, wrap="none", font=("Menlo", 12))
        self.text.pack(fill="both", expand=True, padx=6)
        yscroll = ttk.Scrollbar(self.text, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=yscroll.set)

        self.error_label = ttk.Label(self, text="", foreground="#b00020", padding=6)
        self.error_label.pack(anchor="w")

        self.file_combo.set(bridge.ALL_FILES[0])
        self._load(bridge.ALL_FILES[0])

    def _load(self, name: str) -> None:
        self.current_file = name
        self.text.delete("1.0", "end")
        self.text.insert("1.0", jsonfmt.dumps(self.ws.fragments[name]))
        self.error_label.configure(text="")
        self._update_dirty_label()

    def _refresh(self) -> None:
        """Re-render the current file's text from the in-memory fragment -- picks up edits
        made on other tabs (e.g. a material renamed while this tab was showing materials.json)
        without discarding anything to disk."""
        if self.current_file:
            self._load(self.current_file)

    def _reload(self) -> None:
        if not self.current_file:
            return
        self.ws.reload(self.current_file)
        self._load(self.current_file)
        self.status(f"Reloaded {self.current_file} from disk.")

    def _apply(self) -> None:
        if not self.current_file:
            return
        raw = self.text.get("1.0", "end")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as err:
            self.error_label.configure(text=f"Invalid JSON: {err}")
            return
        if not isinstance(parsed, dict):
            self.error_label.configure(text="Top level of a mintech JSON file must be an object.")
            return
        self.ws.fragments[self.current_file] = parsed
        self.ws.mark_dirty(self.current_file)
        self.error_label.configure(text="Applied (not saved to disk yet).")
        self._update_dirty_label()
        if self.on_apply:
            self.on_apply()

    def _save(self) -> None:
        self._apply()
        if not self.current_file:
            return
        self.ws.save(self.current_file)
        self.error_label.configure(text="Saved.")
        self._update_dirty_label()
        self.status(f"Saved {self.current_file}.")

    def _update_dirty_label(self) -> None:
        dirty = self.current_file and self.ws.dirty.get(self.current_file)
        self.dirty_label.configure(text="unsaved" if dirty else "saved", foreground="#b00020" if dirty else "#2e7d32")
