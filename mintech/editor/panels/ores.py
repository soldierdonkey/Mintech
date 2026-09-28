"""Ores & Worldgen tab: edits the 'veins' each material spawns as (ores.json), with a live
ore-block preview rendered through mintech_textures.py, and vein validation/listing done by
calling mintech_worldgen.build_veins() directly -- the same function the real generator uses."""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from .. import mintech_bridge as bridge
from ..widgets import CheckList, Thumbnail

DEFAULT_VEIN = {
    "name": "new_vein", "parts": ["ore", "deepslate_ore"], "count": 6, "size": 6,
    "height": {"type": "uniform", "min": 0, "max": 64}, "biomes": "#minecraft:is_overworld",
}


class OresPanel(ttk.Frame):
    def __init__(self, parent, workspace, status, goto_build: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.ws = workspace
        self.status = status
        self.goto_build = goto_build
        self.current_material: Optional[str] = None
        self.current_vein_index: Optional[int] = None
        self._building = False

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        mat_col = ttk.Frame(paned, padding=4)
        paned.add(mat_col, weight=1)
        ttk.Label(mat_col, text="Materials", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.mat_list = tk.Listbox(mat_col, exportselection=False, width=16)
        self.mat_list.pack(fill="both", expand=True, pady=(2, 4))
        self.mat_list.bind("<<ListboxSelect>>", self._on_material_select)
        self.enable_btn = ttk.Button(mat_col, text="Enable ore group", command=self._enable_ore)
        self.enable_btn.pack(fill="x")

        vein_col = ttk.Frame(paned, padding=4)
        paned.add(vein_col, weight=1)
        ttk.Label(vein_col, text="Veins", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.vein_list = tk.Listbox(vein_col, exportselection=False, width=20)
        self.vein_list.pack(fill="both", expand=True, pady=(2, 4))
        self.vein_list.bind("<<ListboxSelect>>", self._on_vein_select)
        vbtns = ttk.Frame(vein_col)
        vbtns.pack(fill="x")
        ttk.Button(vbtns, text="+ Add", command=self._add_vein).pack(side="left")
        ttk.Button(vbtns, text="Remove", command=self._remove_vein).pack(side="left", padx=2)

        detail = ttk.Frame(paned, padding=4)
        paned.add(detail, weight=2)
        self._build_detail_form(detail)

        preview = ttk.Labelframe(self, text="Ore block preview", padding=6)
        preview.pack(fill="x", padx=4, pady=(0, 4))
        self.preview_row = ttk.Frame(preview)
        self.preview_row.pack(fill="x")
        actions = ttk.Frame(preview)
        actions.pack(fill="x", pady=(4, 0))
        ttk.Button(actions, text="Validate veins", command=self._validate).pack(side="left")
        if goto_build:
            ttk.Button(actions, text="Generate worldgen datapack -> Build tab",
                       command=goto_build).pack(side="left", padx=6)
        self.info_label = ttk.Label(preview, text="", foreground="#b00020", wraplength=520, justify="left")
        self.info_label.pack(anchor="w", pady=(4, 0))

        self.refresh_materials()

    # -- material list ------------------------------------------------------
    def refresh_materials(self, select: Optional[str] = None) -> None:
        self.mat_list.delete(0, "end")
        ids = self.ws.material_ids()
        for mid in ids:
            groups = self.ws.materials().get(mid, {}).get("groups", [])
            label = f"{mid} [ore]" if "ore" in groups else mid
            self.mat_list.insert("end", label)
        target = select or self.current_material or (ids[0] if ids else None)
        if target in ids:
            self.mat_list.selection_set(ids.index(target))
            self._load_material(target)

    def _on_material_select(self, _evt=None) -> None:
        sel = self.mat_list.curselection()
        if sel:
            self._load_material(self.ws.material_ids()[sel[0]])

    def _load_material(self, mid: str) -> None:
        self.current_material = mid
        material = self.ws.materials().get(mid, {})
        has_ore = "ore" in material.get("groups", [])
        self.enable_btn.configure(state="disabled" if has_ore else "normal")
        self.refresh_veins()

    def _enable_ore(self) -> None:
        if not self.current_material:
            return
        material = self.ws.materials().setdefault(self.current_material, {})
        groups = material.setdefault("groups", [])
        if "ore" not in groups:
            groups.append("ore")
        material.setdefault("orePalette", {"ramp": "ore", "base": material.get("palette", {}).get("base", "#A9B4BD")
                             if isinstance(material.get("palette"), dict) else "#A9B4BD"})
        self.ws.mark_dirty("materials.json")
        self.refresh_materials(select=self.current_material)
        self.status(f"Enabled the 'ore' group for '{self.current_material}' -- set its orePalette on the Materials tab.")

    # -- vein list ------------------------------------------------------------
    def refresh_veins(self, select_index: Optional[int] = None) -> None:
        self.vein_list.delete(0, "end")
        veins = self.ws.ore_veins(self.current_material) if self.current_material else []
        for v in veins:
            self.vein_list.insert("end", v.get("name", "(unnamed)"))
        self.current_vein_index = None
        idx = select_index if select_index is not None else (0 if veins else None)
        if idx is not None and 0 <= idx < len(veins):
            self.vein_list.selection_set(idx)
            self._load_vein(idx)
        else:
            self._clear_detail()
        self._refresh_preview()

    def _on_vein_select(self, _evt=None) -> None:
        sel = self.vein_list.curselection()
        if sel:
            self._load_vein(sel[0])

    def _add_vein(self) -> None:
        if not self.current_material:
            messagebox.showinfo("Add vein", "Select a material first.")
            return
        veins = self.ws.ore_veins(self.current_material)
        vein = copy.deepcopy(DEFAULT_VEIN)
        vein["name"] = f"{self.current_material}_{len(veins) + 1}"
        veins.append(vein)
        self.ws.mark_dirty("ores.json")
        self.refresh_veins(select_index=len(veins) - 1)

    def _remove_vein(self) -> None:
        if self.current_material is None or self.current_vein_index is None:
            return
        veins = self.ws.ore_veins(self.current_material)
        del veins[self.current_vein_index]
        self.ws.mark_dirty("ores.json")
        self.refresh_veins()

    # -- vein detail form -------------------------------------------------------
    def _build_detail_form(self, parent) -> None:
        self.detail = parent
        row = 0

        def label(text):
            nonlocal row
            ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=2)

        self.name_var = tk.StringVar()
        label("name:")
        e = ttk.Entry(parent, textvariable=self.name_var, width=24)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        self.family_var = tk.StringVar()
        label("family:")
        e = ttk.Entry(parent, textvariable=self.family_var, width=24)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        label("parts:")
        self.parts_holder = ttk.Frame(parent)
        self.parts_holder.grid(row=row, column=1, sticky="w")
        row += 1

        self.count_var = tk.StringVar()
        label("count (per chunk):")
        e = ttk.Entry(parent, textvariable=self.count_var, width=8)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        self.size_var = tk.StringVar()
        label("size (blocks per vein):")
        e = ttk.Entry(parent, textvariable=self.size_var, width=8)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        label("height type:")
        self.height_type = ttk.Combobox(parent, state="readonly", width=12,
                                         values=["uniform", "trapezoid", "triangle"])
        self.height_type.grid(row=row, column=1, sticky="w")
        self.height_type.bind("<<ComboboxSelected>>", self._commit)
        row += 1

        self.height_min_var = tk.StringVar()
        label("height min:")
        e = ttk.Entry(parent, textvariable=self.height_min_var, width=8)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        self.height_max_var = tk.StringVar()
        label("height max:")
        e = ttk.Entry(parent, textvariable=self.height_max_var, width=8)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        self.biomes_var = tk.StringVar()
        label("biomes (tag or list):")
        e = ttk.Entry(parent, textvariable=self.biomes_var, width=28)
        e.grid(row=row, column=1, sticky="w"); e.bind("<FocusOut>", self._commit); e.bind("<Return>", self._commit)
        row += 1

        self.squared_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="squared placement", variable=self.squared_var,
                         command=self._commit).grid(row=row, column=1, sticky="w")
        row += 1

    def _clear_detail(self) -> None:
        for var in (self.name_var, self.family_var, self.count_var, self.size_var,
                    self.height_min_var, self.height_max_var, self.biomes_var):
            var.set("")
        self.height_type.set("")
        self.squared_var.set(True)
        for w in self.parts_holder.winfo_children():
            w.destroy()

    def _load_vein(self, index: int) -> None:
        self._building = True
        try:
            self.current_vein_index = index
            vein = self.ws.ore_veins(self.current_material)[index]
            self.name_var.set(vein.get("name", ""))
            self.family_var.set(vein.get("family", "ore"))
            self.count_var.set(str(vein.get("count", "")))
            self.size_var.set(str(vein.get("size", "")))
            height = vein.get("height", {})
            self.height_type.set(height.get("type", "uniform"))
            self.height_min_var.set(str(height.get("min", "")))
            self.height_max_var.set(str(height.get("max", "")))
            biomes = vein.get("biomes", "")
            self.biomes_var.set(biomes if isinstance(biomes, str) else ", ".join(biomes))
            self.squared_var.set(bool(vein.get("squared", True)))
            for w in self.parts_holder.winfo_children():
                w.destroy()
            CheckList(self.parts_holder, self.ws.ore_part_ids(), vein.get("parts", []),
                      lambda sel: self._set_parts(sel), height=4).pack()
        finally:
            self._building = False

    def _set_parts(self, selected) -> None:
        if self._building or self.current_vein_index is None:
            return
        vein = self.ws.ore_veins(self.current_material)[self.current_vein_index]
        vein["parts"] = selected
        self._commit_dirty()

    def _commit(self, _evt=None) -> None:
        if self._building or self.current_material is None or self.current_vein_index is None:
            return
        vein = self.ws.ore_veins(self.current_material)[self.current_vein_index]
        vein["name"] = self.name_var.get().strip() or vein.get("name", "vein")
        vein["family"] = self.family_var.get().strip() or "ore"
        vein["count"] = _to_number(self.count_var.get(), vein.get("count", 1))
        vein["size"] = _to_number(self.size_var.get(), vein.get("size", 1))
        vein["height"] = {
            "type": self.height_type.get() or "uniform",
            "min": _to_number(self.height_min_var.get(), 0),
            "max": _to_number(self.height_max_var.get(), 64),
        }
        biomes_text = self.biomes_var.get().strip()
        vein["biomes"] = biomes_text
        vein["squared"] = self.squared_var.get()
        self._commit_dirty()
        # keep the vein list label in sync if the name changed
        idx = self.current_vein_index
        self.vein_list.delete(idx)
        self.vein_list.insert(idx, vein["name"])
        self.vein_list.selection_set(idx)

    def _commit_dirty(self) -> None:
        self.ws.mark_dirty("ores.json")
        self._refresh_preview()

    # -- preview & validation -----------------------------------------------------
    def _refresh_preview(self) -> None:
        for w in self.preview_row.winfo_children():
            w.destroy()
        self.info_label.configure(text="")
        if not self.current_material:
            return
        try:
            cfg = self.ws.merged_cfg()
            entries = [e for e in bridge.entries_for_material(cfg, self.current_material) if e.family == "ore"]
        except bridge.ConfigError as err:
            self.info_label.configure(text=f"Config error: {err}")
            return
        if not entries:
            ttk.Label(self.preview_row, text="(this material has no ore blocks yet)").pack(side="left")
            return
        try:
            renderer = self.ws.renderer()
        except bridge.ConfigError as err:
            self.info_label.configure(text=f"Config error: {err}")
            return
        for entry in entries:
            thumb = Thumbnail(self.preview_row, caption=entry.part)
            thumb.pack(side="left", padx=3)
            try:
                thumb.set_png(renderer.render(entry))
            except bridge.ConfigError as err:
                thumb.set_png(None, missing_text="error")
                self.info_label.configure(text=f"{entry.part}: {err}")

    def _validate(self) -> None:
        try:
            cfg = self.ws.merged_cfg()
            veins = bridge.build_veins(cfg)
        except bridge.ConfigError as err:
            messagebox.showerror("Vein validation failed", str(err))
            return
        lines = [f"{v.key}: {v.id}  (count={v.spec.get('count')}, size={v.spec.get('size')}, "
                 f"biomes={v.spec.get('biomes')})" for v in veins]
        messagebox.showinfo("Veins (valid)", f"{len(veins)} vein(s) resolved OK:\n\n" + "\n".join(lines[:40]))


def _to_number(text: str, fallback):
    text = text.strip()
    if not text:
        return fallback
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return fallback
