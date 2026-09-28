"""Families tab: which materials get which parts (families.json)."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .. import jsonfmt
from ..widgets import CheckList, ScrollableFrame

COMMON_MATERIAL_SELECTORS = ["*", "@metal", "@gem", "@fluid", "@ore", "@custom"]


class StringListEditor(ttk.Frame):
    """A plain list of strings, e.g. family.exclude rules like '@gem:foil'."""

    def __init__(self, parent, get_list, on_change, width=28, height=5):
        super().__init__(parent)
        self.get_list = get_list
        self.on_change = on_change
        self.listbox = tk.Listbox(self, width=width, height=height, exportselection=False)
        self.listbox.pack(side="top", fill="x")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(2, 0))
        self.entry_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.entry_var, width=width - 8)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._add())
        ttk.Button(row, text="+ Add", command=self._add).pack(side="left", padx=2)
        ttk.Button(row, text="Remove selected", command=self._remove).pack(side="left")
        self.refresh()

    def refresh(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.get_list():
            self.listbox.insert("end", item)

    def _add(self) -> None:
        text = self.entry_var.get().strip()
        if not text:
            return
        self.get_list().append(text)
        self.entry_var.set("")
        self.on_change()
        self.refresh()

    def _remove(self) -> None:
        sel = list(self.listbox.curselection())
        items = self.get_list()
        for i in reversed(sel):
            del items[i]
        if sel:
            self.on_change()
            self.refresh()


class FamiliesPanel(ttk.Frame):
    def __init__(self, parent, workspace, status):
        super().__init__(parent)
        self.ws = workspace
        self.status = status
        self.current_id: Optional[str] = None
        self._building = False

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="Families", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False, width=16)
        self.listbox.pack(fill="both", expand=True, pady=(2, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="+ New", command=self._new_family).pack(side="left")
        ttk.Button(btns, text="Delete", command=self._delete_family).pack(side="left", padx=2)

        right_scroll = ScrollableFrame(paned)
        paned.add(right_scroll, weight=3)
        self.form = right_scroll.body
        self._build_form()

        self.refresh_list()

    def refresh_list(self, select: Optional[str] = None) -> None:
        self.listbox.delete(0, "end")
        ids = self.ws.family_ids()
        for fid in ids:
            self.listbox.insert("end", fid)
        target = select or self.current_id or (ids[0] if ids else None)
        if target in ids:
            self.listbox.selection_set(ids.index(target))
            self._load(target)
        else:
            self.current_id = None

    def _on_select(self, _evt=None) -> None:
        sel = self.listbox.curselection()
        if sel:
            self._load(self.listbox.get(sel[0]))

    def _new_family(self) -> None:
        families = self.ws.families()
        base, n = "new_family", 1
        fid = base
        while fid in families:
            n += 1
            fid = f"{base}_{n}"
        families[fid] = {"materials": ["*"], "parts": ["*"]}
        self.ws.mark_dirty("families.json")
        self.refresh_list(select=fid)

    def _delete_family(self) -> None:
        if not self.current_id:
            return
        if not messagebox.askyesno("Delete family", f"Delete family '{self.current_id}'?"):
            return
        self.ws.families().pop(self.current_id, None)
        self.ws.mark_dirty("families.json")
        self.current_id = None
        self.refresh_list()

    def _build_form(self) -> None:
        f = self.form
        header = ttk.Frame(f)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="id:").pack(side="left")
        self.id_var = tk.StringVar()
        ttk.Entry(header, textvariable=self.id_var, width=18).pack(side="left", padx=4)
        ttk.Button(header, text="Rename", command=self._rename).pack(side="left")

        mats_frame = ttk.Labelframe(f, text="materials (selectors)", padding=6)
        mats_frame.pack(fill="x", pady=4)
        self.material_sel_vars = {}
        row = ttk.Frame(mats_frame)
        row.pack(fill="x")
        for sel in COMMON_MATERIAL_SELECTORS:
            var = tk.BooleanVar()
            self.material_sel_vars[sel] = var
            ttk.Checkbutton(row, text=sel, variable=var, command=self._commit_materials).pack(side="left", padx=(0, 8))
        extra_row = ttk.Frame(mats_frame)
        extra_row.pack(fill="x", pady=(4, 0))
        ttk.Label(extra_row, text="exact material ids (comma-separated):").pack(side="left")
        self.extra_materials_var = tk.StringVar()
        e = ttk.Entry(extra_row, textvariable=self.extra_materials_var, width=28)
        e.pack(side="left", padx=4)
        e.bind("<FocusOut>", lambda _e: self._commit_materials())
        e.bind("<Return>", lambda _e: self._commit_materials())

        parts_frame = ttk.Labelframe(f, text="parts (selectors)", padding=6)
        parts_frame.pack(fill="x", pady=4)
        self.parts_all_var = tk.BooleanVar()
        ttk.Checkbutton(parts_frame, text="* (all parts)", variable=self.parts_all_var,
                         command=self._commit_parts).pack(anchor="w")
        self.parts_checklist_holder = ttk.Frame(parts_frame)
        self.parts_checklist_holder.pack(fill="x", pady=(4, 0))

        exclude_frame = ttk.Labelframe(f, text="exclude (rules like '@gem:foil')", padding=6)
        exclude_frame.pack(fill="x", pady=4)
        self.exclude_holder = ttk.Frame(exclude_frame)
        self.exclude_holder.pack(fill="x")

        defaults_frame = ttk.Labelframe(f, text="defaults (merged onto every item this family produces)", padding=6)
        defaults_frame.pack(fill="both", expand=True, pady=4)
        self.defaults_text = tk.Text(defaults_frame, height=10, width=60, undo=True)
        self.defaults_text.pack(fill="both", expand=True)
        drow = ttk.Frame(defaults_frame)
        drow.pack(fill="x", pady=(4, 0))
        ttk.Button(drow, text="Apply", command=self._apply_defaults).pack(side="left")
        ttk.Button(drow, text="Revert", command=self._load_defaults_text).pack(side="left", padx=4)
        self.defaults_error = ttk.Label(drow, text="", foreground="#b00020")
        self.defaults_error.pack(side="left", padx=8)

    def _family(self) -> dict:
        return self.ws.families().setdefault(self.current_id, {})

    def _load(self, fid: str) -> None:
        self._building = True
        try:
            self.current_id = fid
            family = self.ws.families().get(fid, {})
            self.id_var.set(fid)

            materials = list(family.get("materials", []) or [])
            for sel, var in self.material_sel_vars.items():
                var.set(sel in materials)
            self.extra_materials_var.set(", ".join(m for m in materials if m not in COMMON_MATERIAL_SELECTORS))

            parts = family.get("parts", [])
            is_all = parts == "*" or parts == ["*"]
            self.parts_all_var.set(is_all)
            for w in self.parts_checklist_holder.winfo_children():
                w.destroy()
            selected = [] if is_all else (parts if isinstance(parts, list) else [parts])
            self.parts_checklist = CheckList(self.parts_checklist_holder, self.ws.all_part_ids(),
                                             selected, lambda _sel: self._commit_parts(), height=8)
            self.parts_checklist.pack(fill="x")

            for w in self.exclude_holder.winfo_children():
                w.destroy()
            exclude = family.setdefault("exclude", [])
            StringListEditor(self.exclude_holder, lambda: exclude,
                             lambda: self.ws.mark_dirty("families.json")).pack(fill="x")

            self._load_defaults_text()
        finally:
            self._building = False

    def _load_defaults_text(self) -> None:
        defaults = self._family().get("defaults", {}) if self.current_id else {}
        self.defaults_text.delete("1.0", "end")
        self.defaults_text.insert("1.0", jsonfmt.dumps(defaults))
        self.defaults_error.configure(text="")

    def _apply_defaults(self) -> None:
        if not self.current_id:
            return
        raw = self.defaults_text.get("1.0", "end")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as err:
            self.defaults_error.configure(text=f"Invalid JSON: {err}")
            return
        if not isinstance(parsed, dict):
            self.defaults_error.configure(text="Must be a JSON object.")
            return
        self._family()["defaults"] = parsed
        self.ws.mark_dirty("families.json")
        self.defaults_error.configure(text="Applied.")

    def _commit_materials(self) -> None:
        if self._building or not self.current_id:
            return
        selectors = [s for s in COMMON_MATERIAL_SELECTORS if self.material_sel_vars[s].get()]
        extra = [m.strip() for m in self.extra_materials_var.get().split(",") if m.strip()]
        self._family()["materials"] = selectors + extra
        self.ws.mark_dirty("families.json")

    def _commit_parts(self) -> None:
        if self._building or not self.current_id:
            return
        if self.parts_all_var.get():
            self._family()["parts"] = ["*"]
        else:
            self._family()["parts"] = self.parts_checklist.selected()
        self.ws.mark_dirty("families.json")

    def _rename(self) -> None:
        if not self.current_id:
            return
        new_id = self.id_var.get().strip()
        old_id = self.current_id
        if not new_id or new_id == old_id:
            return
        families = self.ws.families()
        if new_id in families:
            messagebox.showerror("Rename family", f"'{new_id}' already exists.")
            self.id_var.set(old_id)
            return
        families[new_id] = families.pop(old_id)
        self.ws.mark_dirty("families.json")
        self.refresh_list(select=new_id)
