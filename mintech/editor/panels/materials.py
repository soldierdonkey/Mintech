"""Materials tab: groups/inputs/stats, palette & orePalette color pickers with a live
derived-ramp swatch strip, and live item/ore-block previews rendered through the real
mintech_textures.py pipeline (mintech_bridge.Renderer)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .. import mintech_bridge as bridge
from ..widgets import CheckList, ColorField, KeyValueEditor, ScrollableFrame, SwatchStrip, Thumbnail

KNOWN_GROUPS = ["metal", "gem", "fluid", "ore", "custom"]
PREFERRED_PARTS = [
    "ingot", "nugget", "block", "plate", "rod", "gear_medium", "wire_medium",
    "ring_medium", "bolt", "screw", "bottle", "ore", "deepslate_ore", "nether_ore", "end_ore",
]
MAX_THUMBNAILS = 12


class MaterialsPanel(ttk.Frame):
    def __init__(self, parent, workspace, status):
        super().__init__(parent)
        self.ws = workspace
        self.status = status
        self.current_id: Optional[str] = None
        self._building = False  # guard against callbacks firing while we populate the form

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="Materials", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False, width=18)
        self.listbox.pack(fill="both", expand=True, pady=(2, 4))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        btns = ttk.Frame(left)
        btns.pack(fill="x")
        ttk.Button(btns, text="+ New", command=self._new_material).pack(side="left")
        ttk.Button(btns, text="Duplicate", command=self._duplicate_material).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete", command=self._delete_material).pack(side="left")

        right_scroll = ScrollableFrame(paned)
        paned.add(right_scroll, weight=4)
        self.form = right_scroll.body
        self._build_form()

        self.refresh_list()

    # -- list management --------------------------------------------------
    def refresh_list(self, select: Optional[str] = None) -> None:
        self.listbox.delete(0, "end")
        ids = self.ws.material_ids()
        for mid in ids:
            self.listbox.insert("end", mid)
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

    def _new_material(self) -> None:
        materials = self.ws.materials()
        base, n = "new_material", 1
        mid = base
        while mid in materials:
            n += 1
            mid = f"{base}_{n}"
        materials[mid] = {"groups": ["metal"], "inputs": {"base": f"#forge:ingots/{mid}"}, "stats": {"durability": 200}}
        self.ws.mark_dirty("materials.json")
        self.refresh_list(select=mid)

    def _duplicate_material(self) -> None:
        if not self.current_id:
            return
        import copy
        materials = self.ws.materials()
        base = f"{self.current_id}_copy"
        mid, n = base, 1
        while mid in materials:
            n += 1
            mid = f"{base}{n}"
        materials[mid] = copy.deepcopy(materials[self.current_id])
        self.ws.mark_dirty("materials.json")
        self.refresh_list(select=mid)

    def _delete_material(self) -> None:
        if not self.current_id:
            return
        if not messagebox.askyesno("Delete material", f"Delete material '{self.current_id}' from materials.json?"):
            return
        self.ws.materials().pop(self.current_id, None)
        self.ws.mark_dirty("materials.json")
        self.current_id = None
        self.refresh_list()

    # -- form ---------------------------------------------------------------
    def _build_form(self) -> None:
        f = self.form
        header = ttk.Frame(f)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="id:").pack(side="left")
        self.id_var = tk.StringVar()
        ttk.Entry(header, textvariable=self.id_var, width=18).pack(side="left", padx=4)
        ttk.Button(header, text="Rename", command=self._rename).pack(side="left")
        ttk.Label(header, text="  name override:").pack(side="left")
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(header, textvariable=self.name_var, width=16)
        name_entry.pack(side="left", padx=4)
        name_entry.bind("<FocusOut>", lambda _e: self._commit_name())
        name_entry.bind("<Return>", lambda _e: self._commit_name())

        groups_frame = ttk.Labelframe(f, text="Groups", padding=6)
        groups_frame.pack(fill="x", pady=4)
        self.group_vars = {}
        gbox = ttk.Frame(groups_frame)
        gbox.pack(fill="x")
        for g in KNOWN_GROUPS:
            var = tk.BooleanVar()
            self.group_vars[g] = var
            ttk.Checkbutton(gbox, text=g, variable=var, command=self._commit_groups).pack(side="left", padx=(0, 10))
        extra_row = ttk.Frame(groups_frame)
        extra_row.pack(fill="x", pady=(4, 0))
        ttk.Label(extra_row, text="other groups (comma-separated):").pack(side="left")
        self.extra_groups_var = tk.StringVar()
        extra_entry = ttk.Entry(extra_row, textvariable=self.extra_groups_var, width=24)
        extra_entry.pack(side="left", padx=4)
        extra_entry.bind("<FocusOut>", lambda _e: self._commit_groups())
        extra_entry.bind("<Return>", lambda _e: self._commit_groups())

        inputs_frame = ttk.Labelframe(f, text="inputs (crafting source item/tag)", padding=6)
        inputs_frame.pack(fill="x", pady=4)
        self.inputs_holder = ttk.Frame(inputs_frame)
        self.inputs_holder.pack(fill="x")

        stats_frame = ttk.Labelframe(f, text="stats", padding=6)
        stats_frame.pack(fill="x", pady=4)
        self.stats_holder = ttk.Frame(stats_frame)
        self.stats_holder.pack(fill="x")

        self.palette_frame = ttk.Labelframe(f, text="palette (refined-metal look)", padding=6)
        self.palette_frame.pack(fill="x", pady=4)
        self._build_palette_section(self.palette_frame, ore=False)

        self.ore_palette_frame = ttk.Labelframe(f, text="orePalette (ore-block speckles)", padding=6)
        self.ore_palette_frame.pack(fill="x", pady=4)
        self._build_palette_section(self.ore_palette_frame, ore=True)

        preview_frame = ttk.Labelframe(f, text="Live preview (rendered through mintech_textures.py)", padding=6)
        preview_frame.pack(fill="both", expand=True, pady=4)
        self.preview_row = ttk.Frame(preview_frame)
        self.preview_row.pack(fill="x")
        ttk.Button(preview_frame, text="Refresh preview", command=self.refresh_thumbnails).pack(anchor="w", pady=(4, 0))
        self.preview_error = ttk.Label(preview_frame, text="", foreground="#b00020")
        self.preview_error.pack(anchor="w")

    def _build_palette_section(self, parent, ore: bool) -> None:
        modes = ["none", "named", "ramp"] if ore else ["auto", "named", "ramp"]
        mode_var = tk.StringVar(value=modes[0])
        row = ttk.Frame(parent)
        row.pack(fill="x")
        for m in modes:
            label = {"auto": "Auto (by id)", "none": "None", "named": "Named palette", "ramp": "Custom ramp"}[m]
            ttk.Radiobutton(row, text=label, value=m, variable=mode_var,
                             command=lambda: self._commit_palette(ore)).pack(side="left", padx=(0, 8))

        detail = ttk.Frame(parent)
        detail.pack(fill="x", pady=(4, 0))
        named_combo = ttk.Combobox(detail, state="readonly", width=16)
        ramp_combo = ttk.Combobox(detail, state="readonly", width=10)
        color_holder = ttk.Frame(detail)

        strip = SwatchStrip(parent)
        strip.pack(anchor="w", pady=(6, 0))

        state = {"mode_var": mode_var, "named_combo": named_combo, "ramp_combo": ramp_combo,
                 "color_holder": color_holder, "color_field": None, "detail": detail, "strip": strip}
        if ore:
            self._ore_state = state
        else:
            self._metal_state = state

        named_combo.bind("<<ComboboxSelected>>", lambda _e: self._commit_palette(ore))
        ramp_combo.bind("<<ComboboxSelected>>", lambda _e: self._commit_palette(ore))

    # -- loading a material into the form ------------------------------------
    def _load(self, mid: str) -> None:
        self._building = True
        try:
            self.current_id = mid
            material = self.ws.materials().get(mid, {})
            self.id_var.set(mid)
            self.name_var.set(material.get("name", ""))

            groups = list(material.get("groups", []))
            for g, var in self.group_vars.items():
                var.set(g in groups)
            self.extra_groups_var.set(", ".join(g for g in groups if g not in KNOWN_GROUPS))

            inputs = material.setdefault("inputs", {})
            for w in self.inputs_holder.winfo_children():
                w.destroy()
            KeyValueEditor(self.inputs_holder, inputs, self._on_field_changed).pack(fill="x")

            stats = material.setdefault("stats", {})
            for w in self.stats_holder.winfo_children():
                w.destroy()
            KeyValueEditor(self.stats_holder, stats, self._on_field_changed, numeric=True).pack(fill="x")

            self._load_palette_section(self._metal_state, material.get("palette"), ore=False)
            self._load_palette_section(self._ore_state, material.get("orePalette"), ore=True)
        finally:
            self._building = False
        self._refresh_palette_previews()
        self.refresh_thumbnails()

    def _load_palette_section(self, state, value, ore: bool) -> None:
        if value is None:
            state["mode_var"].set("none" if ore else "auto")
        elif isinstance(value, str):
            state["mode_var"].set("named")
        else:
            state["mode_var"].set("ramp")

        state["named_combo"].configure(values=self.ws.merged_palette_names())
        if isinstance(value, str):
            state["named_combo"].set(value)
        elif not state["named_combo"].get():
            names = self.ws.merged_palette_names()
            if names:
                state["named_combo"].set(names[0])

        state["ramp_combo"].configure(values=self.ws.known_ramp_names())
        base = value.get("base", "#808080") if isinstance(value, dict) else "#808080"
        ramp = value.get("ramp", "metal") if isinstance(value, dict) else "metal"
        if isinstance(ramp, str):
            state["ramp_combo"].set(ramp)
        for w in state["color_holder"].winfo_children():
            w.destroy()
        state["color_field"] = ColorField(state["color_holder"], base, lambda _hex: self._commit_palette(ore))

        self._show_palette_detail(state)

    def _show_palette_detail(self, state) -> None:
        for w in (state["named_combo"], state["ramp_combo"], state["color_holder"]):
            w.pack_forget()
        mode = state["mode_var"].get()
        if mode == "named":
            state["named_combo"].pack(side="left")
        elif mode == "ramp":
            ttk.Label(state["detail"], text="").pack_forget()
            state["ramp_combo"].pack(side="left")
            state["color_holder"].pack(side="left", padx=(6, 0))
            state["color_field"].pack()

    # -- commit handlers ------------------------------------------------------
    def _material(self) -> dict:
        return self.ws.materials().setdefault(self.current_id, {})

    def _on_field_changed(self) -> None:
        if self._building or not self.current_id:
            return
        self.ws.mark_dirty("materials.json")
        self.refresh_thumbnails()

    def _commit_name(self) -> None:
        if self._building or not self.current_id:
            return
        material = self._material()
        text = self.name_var.get().strip()
        if text:
            material["name"] = text
        else:
            material.pop("name", None)
        self._on_field_changed()

    def _commit_groups(self) -> None:
        if self._building or not self.current_id:
            return
        groups = [g for g, var in self.group_vars.items() if var.get()]
        extra = [g.strip() for g in self.extra_groups_var.get().split(",") if g.strip()]
        self._material()["groups"] = groups + extra
        self._on_field_changed()

    def _commit_palette(self, ore: bool) -> None:
        state = self._ore_state if ore else self._metal_state
        self._show_palette_detail(state)
        if self._building or not self.current_id:
            return
        material = self._material()
        key = "orePalette" if ore else "palette"
        mode = state["mode_var"].get()
        if mode in ("auto", "none"):
            material.pop(key, None)
        elif mode == "named":
            name = state["named_combo"].get()
            if name:
                material[key] = name
        else:  # ramp
            ramp = state["ramp_combo"].get() or "metal"
            base = state["color_field"].value if state["color_field"] else "#808080"
            material[key] = {"ramp": ramp, "base": base}
        self.ws.mark_dirty("materials.json")
        self._refresh_palette_previews()
        self.refresh_thumbnails()

    # -- previews ---------------------------------------------------------
    def _refresh_palette_previews(self) -> None:
        for state, ore in ((self._metal_state, False), (self._ore_state, True)):
            mode = state["mode_var"].get()
            if mode in ("none",):
                state["strip"].clear()
                continue
            try:
                renderer = self.ws.renderer()
                if mode == "auto":
                    spec = self.current_id
                elif mode == "named":
                    spec = state["named_combo"].get() or self.current_id
                else:
                    spec = {"ramp": state["ramp_combo"].get() or "metal",
                            "base": state["color_field"].value if state["color_field"] else "#808080"}
                tones = renderer.palette_tones(spec)
                state["strip"].set_colors(list(tones))
            except bridge.ConfigError:
                state["strip"].clear()

    def refresh_thumbnails(self) -> None:
        for w in self.preview_row.winfo_children():
            w.destroy()
        self.preview_error.configure(text="")
        if not self.current_id:
            return
        try:
            cfg = self.ws.merged_cfg()
            entries = bridge.entries_for_material(cfg, self.current_id)
        except bridge.ConfigError as err:
            self.preview_error.configure(text=f"Config error: {err}")
            return
        by_part = {}
        for e in entries:
            by_part.setdefault(e.part, e)
        ordered = [p for p in PREFERRED_PARTS if p in by_part]
        ordered += [p for p in by_part if p not in ordered]
        ordered = ordered[:MAX_THUMBNAILS]
        if not ordered:
            ttk.Label(self.preview_row, text="(no parts produced for this material yet)").pack(side="left")
            return
        try:
            renderer = self.ws.renderer()
        except bridge.ConfigError as err:
            self.preview_error.configure(text=f"Config error: {err}")
            return
        for part in ordered:
            entry = by_part[part]
            thumb = Thumbnail(self.preview_row, caption=part)
            thumb.pack(side="left", padx=3)
            try:
                data = renderer.render(entry)
                thumb.set_png(data, missing_text="no\nrenderer")
            except bridge.ConfigError as err:
                thumb.set_png(None, missing_text="error")
                self.preview_error.configure(text=f"{part}: {err}")

    def _rename(self) -> None:
        if not self.current_id:
            return
        new_id = self.id_var.get().strip()
        old_id = self.current_id
        if not new_id or new_id == old_id:
            return
        materials = self.ws.materials()
        if new_id in materials:
            messagebox.showerror("Rename material", f"'{new_id}' already exists.")
            self.id_var.set(old_id)
            return
        materials[new_id] = materials.pop(old_id)
        ore_materials = self.ws.fragments["ores.json"].setdefault("materials", {})
        if old_id in ore_materials:
            ore_materials[new_id] = ore_materials.pop(old_id)
            self.ws.mark_dirty("ores.json")
        self.ws.mark_dirty("materials.json")
        self.status(f"Renamed '{old_id}' -> '{new_id}'. Check families.json for any exact-name selectors "
                     f"still pointing at '{old_id}'.")
        self.refresh_list(select=new_id)
