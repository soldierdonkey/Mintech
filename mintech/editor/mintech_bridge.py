"""The only place that touches mintech_textures.py / mintech_worldgen.py directly.

The editor never reimplements catalog expansion, interpolation, shading or worldgen --
it imports the real generator modules (the same ones `python mintech_textures.py` runs)
and calls their public building blocks: build_catalog(), RenderEnv, the renderer/palette
registries, build_veins(). Previews rendered here are pixel-identical to a real build,
because they *are* a real build, just of one item, held in memory instead of written out.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MINTECH_DIR = Path(__file__).resolve().parent.parent  # .../mintech
if str(MINTECH_DIR) not in sys.path:
    sys.path.insert(0, str(MINTECH_DIR))

import mintech_textures as mt  # noqa: E402  (path must be set up first)
import mintech_worldgen as mw  # noqa: E402

ConfigError = mt.ConfigError
Entry = mt.Entry
Vein = mw.Vein

INDEX_FILE = "mintech.json"
FRAGMENT_ORDER = [
    "materials.json", "parts.json", "ores.json", "families.json",
    "worldgen.json", "textures.json", "palettes.json", "templates.json",
]
ALL_FILES = [INDEX_FILE] + FRAGMENT_ORDER

_merge_deep = mt.MERGE_MODES.resolve("deep")


def real_keys(d: Dict[str, Any]) -> List[str]:
    """Keys that are actual entries, not '$doc' comment keys."""
    return [k for k in d if not (str(k).startswith("$") and len(str(k)) > 1)]


def merge_fragments(fragments: Dict[str, dict]) -> dict:
    """Reproduces mintech_textures.load_config()'s merge, from in-memory fragments
    instead of disk, so previews reflect unsaved edits."""
    cfg = mt.strip_comments(copy.deepcopy(fragments[INDEX_FILE]))
    include = cfg.pop("include", [])
    for name in include:
        frag = mt.strip_comments(copy.deepcopy(fragments[name]))
        cfg = _merge_deep(cfg, frag)
    return cfg


def config_root(cfg: dict) -> Path:
    return mt.config_root(MINTECH_DIR / INDEX_FILE, cfg)


def build_catalog(cfg: dict, accept: Callable[[dict], bool] = lambda obj: True) -> List[Entry]:
    return mt.build_catalog(cfg, accept)


def build_veins(cfg: dict) -> List[Vein]:
    return mw.build_veins(cfg)


def png_encoder(cfg: dict):
    options = ((cfg.get("textures") or {}).get("output") or {}).get("png", {})
    return mt.png_encoder(options)


class Renderer:
    """A cheap, reusable render environment over one merged cfg snapshot."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.env = mt.RenderEnv(cfg, config_root(cfg))
        self.encode = png_encoder(cfg)
        self._renderers: Dict[str, Any] = {}

    def render(self, entry: Entry) -> Optional[bytes]:
        renderer_name = entry.spec.get("renderer")
        if renderer_name is None:
            return None
        renderer = self._renderers.get(renderer_name)
        if renderer is None:
            renderer = mt.RENDERERS.resolve(renderer_name)(self.env, self.encode)
            self._renderers[renderer_name] = renderer
        job = renderer.prepare(entry)
        data = job.render()
        return next(iter(data.values()))

    def palette_tones(self, ref: Any):
        """ref: a named palette string, or an inline {"tones": [...]} / {"ramp": ..., "base": ...} spec."""
        return self.env.palette(ref).tones


def entries_for_material(cfg: dict, material_id: str) -> List[Entry]:
    return [e for e in build_catalog(cfg) if e.material == material_id]


def run_generator(script: str, args: List[str]) -> subprocess.Popen:
    """Launch mintech_textures.py / mintech_worldgen.py as the real CLI (not reimplemented),
    merging stdout+stderr into one pipe for the Build tab's log."""
    return subprocess.Popen(
        [sys.executable, str(MINTECH_DIR / script), *args],
        cwd=str(MINTECH_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
