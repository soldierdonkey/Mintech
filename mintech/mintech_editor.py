#!/usr/bin/env python3
"""
mintech_editor.py -- GUI editor for the mintech/*.json config (materials, families, parts,
ore veins, palettes, ...) that mintech_textures.py and mintech_worldgen.py generate from.

    mintech/editor/  the editor's own code + its tiny UI-state file (state.json)
    mintech/*.json   the actual config, read and written in place -- never copied

The editor imports mintech_textures.py / mintech_worldgen.py directly and reuses their
catalog expansion, palette/ramp resolution and layered renderer for every preview, so a
swatch or item thumbnail shown here is produced by the exact same code path a real build
uses -- nothing about shading, palettes or worldgen is reimplemented in the GUI.

Tabs:
    Materials         groups / inputs / stats / palette & orePalette color pickers,
                      with live item + ore-block previews
    Ores & Worldgen   per-material veins (ores.json), live ore-block preview,
                      vein validation via mintech_worldgen.build_veins()
    Palettes          named palettes, rock palettes, and hue-shift ramp definitions
    Families          which materials get which parts (families.json)
    Raw files         a JSON text fallback for any of the 9 config files
    Build             Save All / Validate / run the real generator scripts, with a live log

Requires Pillow (already a dependency of mintech_textures.py) and Tkinter (bundled with
most Python installs; on some Linux distros install python3-tk separately).

Run from anywhere:
    python3 mintech/mintech_editor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from editor.app import main  # noqa: E402  (path must be set up first)

if __name__ == "__main__":
    sys.exit(main())
