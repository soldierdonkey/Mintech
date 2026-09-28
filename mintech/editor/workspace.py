"""In-memory state for every mintech/*.json fragment, plus a cached merged config.

Each fragment keeps its own dirty flag and is saved independently, to the same file it
was read from -- editing a material never touches families.json, editing a vein never
touches materials.json, etc. The merged config (what mintech_textures.py /
mintech_worldgen.py actually consume) is rebuilt lazily from these in-memory fragments,
so previews see unsaved edits without ever writing to disk.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import jsonfmt
from . import mintech_bridge as bridge


class Workspace:
    def __init__(self, mintech_dir: Path):
        self.dir = mintech_dir
        self.fragments: Dict[str, dict] = {}
        self.dirty: Dict[str, bool] = {}
        self._merged_cache: Optional[dict] = None
        self.load_all()

    # -- loading / saving ------------------------------------------------
    def load_all(self) -> None:
        for name in bridge.ALL_FILES:
            self.fragments[name] = jsonfmt.read_json(self.dir / name)
            self.dirty[name] = False
        self._merged_cache = None

    def reload(self, name: str) -> None:
        self.fragments[name] = jsonfmt.read_json(self.dir / name)
        self.dirty[name] = False
        self._merged_cache = None

    def mark_dirty(self, name: str) -> None:
        self.dirty[name] = True
        self._merged_cache = None

    def any_dirty(self) -> bool:
        return any(self.dirty.values())

    def dirty_files(self) -> List[str]:
        return [n for n, d in self.dirty.items() if d]

    def save(self, name: str) -> None:
        jsonfmt.write_json(self.dir / name, self.fragments[name])
        self.dirty[name] = False

    def save_all(self) -> List[str]:
        saved = self.dirty_files()
        for name in saved:
            self.save(name)
        return saved

    # -- merged config, for previews / catalog / veins --------------------
    def merged_cfg(self) -> dict:
        if self._merged_cache is None:
            self._merged_cache = bridge.merge_fragments(self.fragments)
        return self._merged_cache

    def invalidate(self) -> None:
        self._merged_cache = None

    @property
    def root(self) -> Path:
        return bridge.config_root(self.merged_cfg())

    def renderer(self) -> "bridge.Renderer":
        return bridge.Renderer(self.merged_cfg())

    # -- convenience accessors, one per owning file ------------------------
    def materials(self) -> dict:
        return self.fragments["materials.json"].setdefault("materials", {})

    def material_defaults(self) -> dict:
        return self.fragments["materials.json"].setdefault("material_defaults", {})

    def families(self) -> dict:
        return self.fragments["families.json"].setdefault("families", {})

    def named_palettes(self) -> dict:
        textures = self.fragments["palettes.json"].setdefault("textures", {})
        return textures.setdefault("palettes", {})

    def rock_palettes(self) -> dict:
        textures = self.fragments["ores.json"].setdefault("textures", {})
        return textures.setdefault("palettes", {})

    def ramps(self) -> dict:
        textures = self.fragments["textures.json"].setdefault("textures", {})
        return textures.setdefault("ramps", {})

    def ore_material_entry(self, material_id: str) -> dict:
        materials = self.fragments["ores.json"].setdefault("materials", {})
        return materials.setdefault(material_id, {})

    def ore_veins(self, material_id: str) -> list:
        return self.ore_material_entry(material_id).setdefault("veins", [])

    def craftable_part_ids(self) -> List[str]:
        return sorted(bridge.real_keys(self.fragments["parts.json"].get("parts", {})))

    def ore_part_ids(self) -> List[str]:
        return sorted(bridge.real_keys(self.fragments["ores.json"].get("parts", {})))

    def all_part_ids(self) -> List[str]:
        return sorted(set(self.craftable_part_ids()) | set(self.ore_part_ids()))

    def known_ramp_names(self) -> List[str]:
        return sorted(bridge.real_keys(self.ramps()))

    def known_palette_names(self) -> List[str]:
        return sorted(bridge.real_keys(self.named_palettes()))

    def merged_palette_names(self) -> List[str]:
        """Every name resolvable as material.palette / material.orePalette at render time --
        palettes.json's named ramps plus ores.json's rock palettes, since both merge into
        the same textures.palettes table."""
        textures = (self.merged_cfg().get("textures") or {}).get("palettes") or {}
        return sorted(bridge.real_keys(textures))

    def material_ids(self) -> List[str]:
        return sorted(bridge.real_keys(self.materials()))

    def family_ids(self) -> List[str]:
        return sorted(bridge.real_keys(self.families()))
