#!/usr/bin/env python3
"""
mintech_textures.py -- data-driven, incremental 16x16 item-texture generator.

    mintech/*.json --> catalog (families x materials x parts) --> renderer --> sink (folder | zip)
                                                                      \\--> manifest (per-folder hash.txt)

Nothing about specific items, metals or item classes lives in this file. Templates, palettes,
shaders, naming patterns and families all come from the JSON next to this script -- mintech.json
is the index and its "include" list names the rest (materials, parts, ores, families, worldgen,
textures, palettes, templates), deep-merged into one config. The code only provides *mechanisms*,
each behind a small registry that you extend with a decorator:

    TEMPLATE_SOURCES  how a silhouette is loaded          ascii, image
    TRANSFORMS        mask edits applied before shading   flip_x, flip_y, rotate, shift
    SHADERS           mask -> palette tone indices        auto, flat
    PALETTE_SOURCES   how a palette's tones are produced  tones, ramp
    CONTEXTS          what a layer "sees" while shading   self, below, all
    BLENDS            how a layer lands on the canvas     over, replace
    RENDERERS         catalog entry -> files              layered
    SINKS             where files are written             dir, zip
    FILTERS           {value|filter} in any string        lower, upper, title, tag, path, ns, ingredient
    MERGE_MODES       how "defaults" are inherited        replace, merge, deep, concat

The catalog expansion and "{...}" interpolation are kept semantically identical to the
KubeJS catalog code (kubejs/startup_scripts/mintech_items.js), so item ids, texture ids and
names always agree between the textures and the registered items.

Run from anywhere; paths resolve against the config's meta.root, not the cwd:
    python mintech/mintech_textures.py                    incremental build
    python mintech/mintech_textures.py --compress         build <pack>.zip instead of a folder
    python mintech/mintech_textures.py --force            ignore hash.txt, re-render everything
    python mintech/mintech_textures.py --only 'metal:iron:*' --dry-run -v
    python mintech/mintech_textures.py --list             print the catalog and exit
    python mintech/mintech_textures.py --preview sheet.png   upscaled contact sheet for eyeballing art
"""
from __future__ import annotations

import argparse
import colorsys
import copy
import difflib
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, cast

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  pip install pillow")

log = logging.getLogger("mintech")


# =============================================================================
# 0. Infrastructure
# =============================================================================
class ConfigError(Exception):
    """Anything wrong in mintech.json. The message says where."""


class Registry(dict):
    """Name -> implementation table, filled with @REGISTRY.register("name")."""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind

    def register(self, name: str):
        def decorator(obj):
            self[name] = obj
            return obj
        return decorator

    def resolve(self, name: Any):
        if name not in self:
            raise ConfigError(f"unknown {self.kind} {name!r}{did_you_mean(name, self)}")
        return self[name]


def did_you_mean(key: Any, candidates) -> str:
    names = [str(c) for c in candidates]
    close = difflib.get_close_matches(str(key), names, n=3, cutoff=0.5)
    if close:
        return f" (did you mean {', '.join(map(repr, close))}?)"
    return f" (known: {', '.join(sorted(names)[:12])})" if names else ""


def require(obj: Any, path: str, where: str = "") -> Any:
    """Fetch a dotted path from nested dicts or raise a ConfigError naming the location."""
    current = obj
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            raise ConfigError(f"missing '{path}'{' in ' + where if where else ''}")
        current = current[key]
    return current


def as_list(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def strip_comments(value: Any) -> Any:
    """Keys starting with '$' are documentation and are dropped everywhere (JS does the same)."""
    if isinstance(value, dict):
        return {k: strip_comments(v) for k, v in value.items() if not str(k).startswith("$")}
    if isinstance(value, list):
        return [strip_comments(v) for v in value]
    return value


_WARNED: set = set()


def warn_once(message: str) -> None:
    if message not in _WARNED:
        _WARNED.add(message)
        log.warning(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: Any, length: int = 16) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:length]


# =============================================================================
# 1. Interpolation: "{path.to.value|filter|filter}"
#    A string that is exactly one token yields the raw value (number, object, ...);
#    otherwise tokens are stringified in place. Results are re-interpolated, so
#    values may themselves contain tokens (bounded by MAX_DEPTH).
# =============================================================================
TOKEN = re.compile(r"\{([^{}]+)\}")
MAX_DEPTH = 8
FILTERS = Registry("filter")


class Unresolved(LookupError):
    """A {path} pointed at nothing. Callers decide whether that is fatal or a skip."""

    def __init__(self, path: str):
        super().__init__(f"unresolved {{{path}}}")
        self.path = path


def stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def title_case(text: Any) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in str(text).split("_") if w)


FILTERS.register("lower")(lambda v: stringify(v).lower())
FILTERS.register("upper")(lambda v: stringify(v).upper())
FILTERS.register("title")(title_case)
FILTERS.register("tag")(lambda v: stringify(v)[1:] if stringify(v).startswith("#") else stringify(v))
FILTERS.register("path")(lambda v: stringify(v).split(":", 1)[-1])
FILTERS.register("ns")(lambda v: stringify(v).lstrip("#").split(":", 1)[0] if ":" in stringify(v) else "minecraft")


@FILTERS.register("ingredient")
def _ingredient_filter(value: Any) -> Dict[str, str]:
    text = stringify(value)
    return {"tag": text[1:]} if text.startswith("#") else {"item": text}


def lookup(ctx: Any, path: str) -> Any:
    current = ctx
    for key in path.split("."):
        if isinstance(current, Mapping) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise Unresolved(path)
    return current


def evaluate(expr: str, ctx: Any) -> Any:
    path, *filters = (piece.strip() for piece in expr.split("|"))
    value = lookup(ctx, path)
    for name in filters:
        value = FILTERS.resolve(name)(value)
    return value


def interpolate(value: Any, ctx: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise ConfigError(f"interpolation nested deeper than {MAX_DEPTH} levels (reference cycle?)")
    if isinstance(value, str):
        whole = TOKEN.fullmatch(value)
        if whole:
            return interpolate(evaluate(whole.group(1), ctx), ctx, depth + 1)
        if "{" not in value:
            return value
        out = TOKEN.sub(lambda m: stringify(evaluate(m.group(1), ctx)), value)
        return out if out == value else interpolate(out, ctx, depth + 1)
    if isinstance(value, list):
        return [interpolate(v, ctx, depth) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, ctx, depth) for k, v in value.items()}
    return value


# =============================================================================
# 2. Inheritance: defaults -> family.defaults -> part -> material.overrides
#    Per-field merge modes come from mintech.json "inheritance"; unlisted = replace.
# =============================================================================
MERGE_MODES = Registry("merge mode")


@MERGE_MODES.register("replace")
def _merge_replace(old: Any, new: Any) -> Any:
    return copy.deepcopy(new)


@MERGE_MODES.register("merge")
def _merge_shallow(old: Any, new: Any) -> Any:
    out = copy.deepcopy(old) if isinstance(old, dict) else {}
    out.update(copy.deepcopy(new) if isinstance(new, dict) else {})
    return out


@MERGE_MODES.register("deep")
def _merge_deep(old: Any, new: Any) -> Any:
    if not (isinstance(old, dict) and isinstance(new, dict)):
        return copy.deepcopy(new)
    out = copy.deepcopy(old)
    for key, value in new.items():
        out[key] = _merge_deep(out[key], value) if key in out else copy.deepcopy(value)
    return out


@MERGE_MODES.register("concat")
def _merge_concat(old: Any, new: Any) -> Any:
    head = copy.deepcopy(old) if isinstance(old, list) else []
    return head + (copy.deepcopy(new) if isinstance(new, list) else [copy.deepcopy(new)])


def inherit(sources: Sequence[Optional[dict]], rules: Mapping[str, str]) -> dict:
    out: dict = {}
    for source in sources:
        for key, value in (source or {}).items():
            if key in out:
                out[key] = MERGE_MODES.resolve(rules.get(key, "replace"))(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
    return out


# =============================================================================
# 3. Catalog: families x materials x parts -> entries   (mirrors buildCatalog() in JS)
# =============================================================================
@dataclass
class Entry:
    family: str
    material: str
    part: str
    spec: dict   # fully inherited part spec
    ctx: dict    # interpolation context

    @property
    def key(self) -> str:
        return f"{self.family}:{self.material}:{self.part}"

    @property
    def id(self) -> str:
        return self.ctx["self"]

    @property
    def texture(self) -> str:
        return self.ctx["texture"]


def qualify(resource: str, namespace: str) -> str:
    return resource if ":" in resource else f"{namespace}:{resource}"


def normalize_materials(raw: Mapping[str, Any], defaults: Optional[Mapping[str, Any]] = None) -> Dict[str, dict]:
    out = {}
    for mid, spec in raw.items():
        material = {"name": title_case(mid), "palette": mid, "groups": [], "inputs": {},
                    "outputs": {}, "stats": {}, "overrides": {}}
        material.update(copy.deepcopy(defaults or {}))
        material.update(spec or {})
        material["id"] = mid
        out[mid] = material
    return out


def material_matches(selector: str, material: dict) -> bool:
    if selector == "*":
        return True
    if selector.startswith("@"):
        return selector[1:] in as_list(material.get("groups"))
    return selector == material["id"]


def select_materials(selectors: Any, materials: Dict[str, dict], where: str) -> List[str]:
    chosen: List[str] = []
    for selector in as_list(selectors):
        if selector != "*" and not selector.startswith("@") and selector not in materials:
            raise ConfigError(f"{where}: unknown material {selector!r}{did_you_mean(selector, materials)}")
        for mid, material in materials.items():
            if material_matches(selector, material) and mid not in chosen:
                chosen.append(mid)
    return chosen


def select_parts(selectors: Any, parts: Mapping[str, Any], where: str) -> List[str]:
    chosen: List[str] = []
    for selector in as_list(selectors):
        ids = list(parts) if selector == "*" else [selector]
        for pid in ids:
            if pid not in parts:
                raise ConfigError(f"{where}: unknown part {pid!r}{did_you_mean(pid, parts)}")
            if pid not in chosen:
                chosen.append(pid)
    return chosen


def is_excluded(rules: Any, material: dict, part_id: str) -> bool:
    for rule in as_list(rules):
        mat_selector, _, part_selector = str(rule).partition(":")
        if material_matches(mat_selector, material) and part_selector in ("", "*", part_id):
            return True
    return False


def build_catalog(cfg: dict, accept: Callable[[dict], bool] = lambda obj: True) -> List[Entry]:
    namespace = require(cfg, "meta.namespace")
    rules = cfg.get("inheritance", {})
    materials = normalize_materials(cfg.get("materials", {}), cfg.get("material_defaults"))
    parts = cfg.get("parts", {})
    index: Dict[str, Dict[str, Dict[str, str]]] = {}
    entries: List[Entry] = []

    for fam_id, fam_spec in cfg.get("families", {}).items():
        family = {**fam_spec, "id": fam_id}
        if not accept(family):
            continue
        where = f"families.{fam_id}"
        for mid in select_materials(family.get("materials", "*"), materials, where):
            material = materials[mid]
            overrides = material.get("overrides") or {}
            for pid in select_parts(family.get("parts", "*"), parts, where):
                if is_excluded(family.get("exclude"), material, pid):
                    continue
                spec = inherit([cfg.get("defaults"), family.get("defaults"), parts[pid],
                                overrides.get("*"), overrides.get(pid)], rules)
                spec["id"] = pid
                if spec.get("name") is None:
                    spec["name"] = title_case(pid)
                if not accept(spec):
                    continue
                ctx = {"meta": cfg["meta"], "vars": cfg.get("vars", {}), "family": family,
                       "material": material, "part": spec, "items": index}
                entry = Entry(fam_id, mid, pid, spec, ctx)
                item_id = qualify(str(_pattern(entry, "id")), namespace)
                index.setdefault(fam_id, {}).setdefault(mid, {})[pid] = item_id
                entries.append(entry)

    owners: Dict[str, str] = {}
    for entry in entries:
        ctx = entry.ctx
        ctx["parts"] = index[entry.family][entry.material]
        ctx["self"] = ctx["parts"][entry.part]
        if ctx["self"] in owners:
            raise ConfigError(f"item id {ctx['self']} produced by both {owners[ctx['self']]} and {entry.key}")
        owners[ctx["self"]] = entry.key
        ctx["texture"] = qualify(str(_pattern(entry, "texture")), namespace)
        ctx["name"] = str(_pattern(entry, "name"))
    return entries


def _pattern(entry: Entry, name: str) -> Any:
    pattern = require(entry.spec, f"patterns.{name}", entry.key)
    try:
        return interpolate(pattern, entry.ctx)
    except Unresolved as err:
        raise ConfigError(f"{entry.key}: patterns.{name} {err}") from None


# =============================================================================
# 4. Rendering primitives
#    A Mask is a grid of cells: CLEAR (transparent), AUTO (let the shader decide)
#    or an int (a fixed palette tone).
# =============================================================================
CLEAR, AUTO = None, "auto"
Cell = Any


@dataclass(frozen=True)
class Mask:
    rows: Tuple[Tuple[Cell, ...], ...]

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def solid(self, y: int, x: int) -> bool:
        return 0 <= y < self.height and 0 <= x < self.width and self.rows[y][x] is not CLEAR

    def union(self, others: Sequence["Mask"]) -> "Mask":
        """Own cells win; CLEAR cells become solid where any other mask is solid."""
        return Mask(tuple(
            tuple(cell if cell is not CLEAR or not any(o.solid(y, x) for o in others) else AUTO
                  for x, cell in enumerate(row))
            for y, row in enumerate(self.rows)))

    def canonical(self) -> list:
        return [list(row) for row in self.rows]


def decode_symbol(symbol: Any, where: str) -> Cell:
    if symbol is None or symbol == "clear":
        return CLEAR
    if symbol == "auto":
        return AUTO
    if isinstance(symbol, int) and not isinstance(symbol, bool) and symbol >= 0:
        return symbol
    raise ConfigError(f"{where}: symbol must be 'clear', 'auto' or a tone index, got {symbol!r}")


# -- template sources ---------------------------------------------------------
TEMPLATE_SOURCES = Registry("template source")
CHANNELS: Dict[str, Callable[[int, int, int], float]] = {
    "r": lambda r, g, b: r / 255, "g": lambda r, g, b: g / 255, "b": lambda r, g, b: b / 255,
    "luma": lambda r, g, b: (0.299 * r + 0.587 * g + 0.114 * b) / 255,
    "max": lambda r, g, b: max(r, g, b) / 255,
}


@TEMPLATE_SOURCES.register("ascii")
def _ascii_template(spec: dict, env: "RenderEnv", where: str) -> Mask:
    legend = {**env.legend, **spec.get("legend", {})}
    rows = []
    for y, line in enumerate(require(spec, "rows", where)):
        row = []
        for x, char in enumerate(line):
            if char not in legend:
                raise ConfigError(f"{where}: {char!r} at row {y}, col {x} is not in the legend")
            row.append(decode_symbol(legend[char], where))
        rows.append(tuple(row))
    return Mask(tuple(rows))


@TEMPLATE_SOURCES.register("image")
def _image_template(spec: dict, env: "RenderEnv", where: str) -> Mask:
    """Greyscale sprite -> tones via brightness thresholds (the sprite_modularity.rs approach)."""
    path = env.root / require(spec, "path", where)
    measure = CHANNELS.get(spec.get("channel", "luma"))
    if measure is None:
        raise ConfigError(f"{where}: unknown channel {spec.get('channel')!r}{did_you_mean(spec.get('channel'), CHANNELS)}")
    bands = sorted(((float(limit), decode_symbol(tone, where)) for limit, tone in require(spec, "thresholds", where)),
                   key=lambda band: -band[0])
    with Image.open(path) as source:
        image = source.convert("RGBA")
    rows = []
    for y in range(image.height):
        row = []
        for x in range(image.width):
            r, g, b, a = cast(Tuple[int, int, int, int], image.getpixel((x, y)))
            level = measure(r, g, b)
            row.append(CLEAR if a == 0 else next((tone for limit, tone in bands if level > limit), bands[-1][1]))
        rows.append(tuple(row))
    return Mask(tuple(rows))


# -- transforms (applied before shading, so lighting stays consistent) -------
TRANSFORMS = Registry("transform")


@TRANSFORMS.register("flip_x")
def _flip_x(mask: Mask, _arg: Any) -> Mask:
    return Mask(tuple(tuple(reversed(row)) for row in mask.rows))


@TRANSFORMS.register("flip_y")
def _flip_y(mask: Mask, _arg: Any) -> Mask:
    return Mask(tuple(reversed(mask.rows)))


@TRANSFORMS.register("rotate")
def _rotate(mask: Mask, degrees: Any) -> Mask:
    rows = mask.rows
    for _ in range((int(degrees) // 90) % 4):  # clockwise quarter turns
        rows = tuple(zip(*rows[::-1]))
    return Mask(tuple(tuple(row) for row in rows))


@TRANSFORMS.register("shift")
def _shift(mask: Mask, offset: Any) -> Mask:
    dx, dy = (int(v) for v in offset)
    return Mask(tuple(
        tuple(mask.rows[y - dy][x - dx] if 0 <= y - dy < mask.height and 0 <= x - dx < mask.width else CLEAR
              for x in range(mask.width))
        for y in range(mask.height)))


def apply_transforms(mask: Mask, specs: Any, where: str) -> Mask:
    for spec in as_list(specs):
        if isinstance(spec, str):
            mask = TRANSFORMS.resolve(spec)(mask, None)
        elif isinstance(spec, dict) and len(spec) == 1:
            (name, arg), = spec.items()
            mask = TRANSFORMS.resolve(name)(mask, arg)
        else:
            raise ConfigError(f"{where}: transform must be \"name\" or {{\"name\": arg}}, got {spec!r}")
    return mask


# -- shaders ------------------------------------------------------------------
SHADERS = Registry("shader type")


class Shader(ABC):
    def __init__(self, spec: dict, where: str):
        self.spec, self.where = spec, where

    @abstractmethod
    def auto_tone(self, y: int, x: int, context: Mask) -> int:
        """Tone for an AUTO cell. `context` decides which neighbours count as solid."""

    def shade(self, mask: Mask, context: Mask) -> List[List[Optional[int]]]:
        return [[None if cell is CLEAR else cell if isinstance(cell, int) else self.auto_tone(y, x, context)
                 for x, cell in enumerate(row)]
                for y, row in enumerate(mask.rows)]


@SHADERS.register("flat")
class FlatShader(Shader):
    def auto_tone(self, y, x, context):
        return int(require(self.spec, "tone", self.where))


@SHADERS.register("auto")
class AutoShader(Shader):
    """
    Generalised port of metal_parts.py's shade_pixel(): an outward surface normal from the
    8-neighbourhood, dotted with a configurable light vector, plus an ambient gradient along
    the light direction. With light [-1, -1] and the stock numbers it is pixel-identical to
    the original. Every tone and threshold is read from the shader spec.
    """
    ROLES = ("thin_lit", "thin_shade", "outline", "rim_shade", "rim_lit", "highlight", "lit",
             "shade", "ambient_high", "ambient_mid", "ambient_low")
    LIMITS = ("thin_dot", "thin_ambient", "rim_dot", "rim_ambient", "highlight_dot",
              "highlight_ambient", "ambient_high", "ambient_mid")

    def __init__(self, spec, where):
        super().__init__(spec, where)
        self.lx, self.ly = (float(v) for v in require(spec, "light", where))
        self.sx, self.sy = _sign(self.lx), _sign(self.ly)
        self.role = {k: int(require(spec, f"tones.{k}", where)) for k in self.ROLES}
        self.limit = {k: float(require(spec, f"thresholds.{k}", where)) for k in self.LIMITS}

    def ambient(self, y: int, x: int, height: int, width: int) -> float:
        project = lambda yy, xx: -(self.lx * xx + self.ly * yy)
        corners = [project(yy, xx) for yy in (0, height - 1) for xx in (0, width - 1)]
        low, high = min(corners), max(corners)
        return 0.5 if high == low else 1.0 - (project(y, x) - low) / (high - low)

    def auto_tone(self, y, x, context):
        def empty(dy: int, dx: int) -> bool:
            return not context.solid(y + dy, x + dx)

        top, bottom, left, right = empty(-1, 0), empty(1, 0), empty(0, -1), empty(0, 1)
        tl, tr, bl, br = empty(-1, -1), empty(-1, 1), empty(1, -1), empty(1, 1)
        ny = (bottom - top) + 0.5 * (bl + br - tl - tr)   # outward normal, toward empty space
        nx = (right - left) + 0.5 * (tr + br - tl - bl)
        light = nx * self.lx + ny * self.ly
        ambient = self.ambient(y, x, context.height, context.width)
        toward_edge = _side(self.sy, top, bottom) or _side(self.sx, left, right)
        away_edge = _side(-self.sy, top, bottom) or _side(-self.sx, left, right)
        lit_corner = bool(self.sx or self.sy) and empty(self.sy, self.sx)
        dark_corner = bool(self.sx or self.sy) and empty(-self.sy, -self.sx)
        role, limit = self.role, self.limit

        if (top and bottom) or (left and right) or (tl and br) or (tr and bl):   # 1-2px features
            lit = light > limit["thin_dot"] or ambient > limit["thin_ambient"]
            return role["thin_lit"] if lit else role["thin_shade"]
        if top or bottom or left or right:                                      # silhouette rim
            if light < limit["rim_dot"] or away_edge:
                return role["outline"]
            return role["rim_shade"] if ambient < limit["rim_ambient"] else role["rim_lit"]
        if lit_corner or toward_edge:                                           # just inside, lit side
            bright = light > limit["highlight_dot"] and ambient > limit["highlight_ambient"]
            return role["highlight"] if bright else role["lit"]
        if dark_corner or away_edge:                                            # just inside, dark side
            return role["shade"]
        if ambient > limit["ambient_high"]:                                     # deep interior
            return role["ambient_high"]
        return role["ambient_mid"] if ambient > limit["ambient_mid"] else role["ambient_low"]


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _side(sign: int, negative: bool, positive: bool) -> bool:
    return negative if sign < 0 else positive if sign > 0 else False


# -- shading contexts, blend modes, colours -----------------------------------
CONTEXTS = Registry("shading context")
CONTEXTS.register("self")(lambda i, masks: masks[i])
CONTEXTS.register("below")(lambda i, masks: masks[i].union(masks[:i]))
CONTEXTS.register("all")(lambda i, masks: masks[i].union(masks[:i] + masks[i + 1:]))

BLENDS = Registry("blend mode")
BLENDS.register("over")(lambda canvas, layer: Image.alpha_composite(canvas, layer))


@BLENDS.register("replace")
def _blend_replace(canvas: Image.Image, layer: Image.Image) -> Image.Image:
    out = canvas.copy()
    out.paste(layer, (0, 0), layer.getchannel("A").point(lambda a: 255 if a else 0))
    return out


RGBA = Tuple[int, int, int, int]


def parse_color(value: Any, where: str) -> RGBA:
    if isinstance(value, str) and re.fullmatch(r"#?(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value):
        digits = value.lstrip("#")
        channels = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
        return tuple(channels + [255] * (4 - len(channels)))  # type: ignore[return-value]
    if isinstance(value, list) and len(value) in (3, 4) and all(isinstance(c, int) and 0 <= c <= 255 for c in value):
        return tuple(value + [255] * (4 - len(value)))  # type: ignore[return-value]
    raise ConfigError(f"{where}: bad colour {value!r} (use '#RRGGBB', '#RRGGBBAA' or [r,g,b(,a)])")


@dataclass(frozen=True)
class Palette:
    tones: Tuple[RGBA, ...]
    where: str

    def color(self, tone: int) -> RGBA:
        if not 0 <= tone < len(self.tones):
            raise ConfigError(f"{self.where}: tone {tone} requested but palette has {len(self.tones)} tones")
        return self.tones[tone]


# -- palette sources: explicit tone lists, or a hue-shifted ramp from one base colour ---
PALETTE_SOURCES = Registry("palette source")


@PALETTE_SOURCES.register("tones")
def _palette_tones(spec: dict, env: "RenderEnv", where: str) -> Tuple[RGBA, ...]:
    return tuple(parse_color(c, where) for c in as_list(require(spec, "tones", where)))


def _toward_hue(hue: float, target_deg: float, amount: float) -> float:
    delta = ((target_deg / 360.0 - hue + 0.5) % 1.0) - 0.5   # shortest way round the wheel
    return (hue + delta * amount) % 1.0


@PALETTE_SOURCES.register("ramp")
def _palette_ramp(spec: dict, env: "RenderEnv", where: str) -> Tuple[RGBA, ...]:
    """
    {"ramp": "metal", "base": "#B87333"}  (ramp may also be inline).  Each ramp step is
    {"light": x, "sat": m, "hue": h}:  light < 0 scales lightness toward black, > 0 toward white;
    sat multiplies saturation; hue moves that fraction of the way toward the ramp's "cool"
    (shadow steps) or "warm" (highlight steps) hue in degrees. A spec may set "steps" or any
    ramp key directly to tweak a single material.
    """
    ramp_ref = spec.get("ramp", {})
    ramp = dict(env.ramp(ramp_ref) if isinstance(ramp_ref, str) else ramp_ref)
    ramp.update({k: v for k, v in spec.items() if k not in ("ramp", "base", "source")})
    r, g, b, a = parse_color(require(spec, "base", where), where)
    h0, l0, s0 = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    tones = []
    for i, step in enumerate(require(ramp, "steps", where)):
        light, sat, hue = float(step.get("light", 0)), float(step.get("sat", 1)), float(step.get("hue", 0))
        lightness = l0 * (1 + light) if light < 0 else l0 + (1 - l0) * light
        target = ramp.get("cool" if light < 0 else "warm")
        h = _toward_hue(h0, float(target), hue) if target is not None and hue else h0
        rgb = colorsys.hls_to_rgb(h, min(1.0, max(0.0, lightness)), min(1.0, max(0.0, s0 * sat)))
        tones.append(tuple(round(c * 255) for c in rgb) + (a,))
    return tuple(tones)


# =============================================================================
# 5. Render environment: resolves template / palette / shader references
# =============================================================================
class RenderEnv:
    def __init__(self, cfg: dict, root: Path):
        textures = require(cfg, "textures")
        self.root = root
        self.size = int(require(textures, "size", "textures"))
        self.legend = require(textures, "legend", "textures")
        self.templates = textures.get("templates", {})
        self.palettes = textures.get("palettes", {})
        self.shaders = textures.get("shaders", {})
        self.ramps = textures.get("ramps", {})
        self._masks: Dict[str, Mask] = {}
        self._palettes: Dict[str, Palette] = {}
        self._shaders: Dict[str, Tuple[Shader, dict]] = {}

    def mask(self, ref: Any) -> Mask:
        """ref: template id, inline row list, or inline template object."""
        key = ref if isinstance(ref, str) else canonical(ref)
        if key not in self._masks:
            if isinstance(ref, str):
                if ref not in self.templates:
                    raise ConfigError(f"unknown template {ref!r}{did_you_mean(ref, self.templates)}")
                spec, where = self.templates[ref], f"textures.templates.{ref}"
            else:
                spec, where = ref, "inline template"
            if isinstance(spec, list):
                spec = {"source": "ascii", "rows": spec}
            mask = TEMPLATE_SOURCES.resolve(spec.get("source", "ascii"))(spec, self, where)
            if (mask.height, mask.width) != (self.size, self.size) or any(len(r) != self.size for r in mask.rows):
                raise ConfigError(f"{where}: must be {self.size}x{self.size}, got {mask.width}x{mask.height}")
            self._masks[key] = mask
        return self._masks[key]

    def palette(self, ref: Any) -> Palette:
        key = ref if isinstance(ref, str) else canonical(ref)
        if key not in self._palettes:
            if isinstance(ref, str):
                if ref not in self.palettes:
                    raise ConfigError(f"unknown palette {ref!r}{did_you_mean(ref, self.palettes)}")
                spec, where = self.palettes[ref], f"textures.palettes.{ref}"
            else:
                spec, where = ref, "inline palette"
            if not isinstance(spec, dict):
                spec = {"tones": spec}
            source = spec.get("source") or ("ramp" if "base" in spec else "tones")
            self._palettes[key] = Palette(PALETTE_SOURCES.resolve(source)(spec, self, where), where)
        return self._palettes[key]

    def ramp(self, name: str) -> dict:
        if name not in self.ramps:
            raise ConfigError(f"unknown ramp {name!r}{did_you_mean(name, self.ramps)}")
        return self.ramps[name]

    def shader(self, ref: Any) -> Tuple[Shader, dict]:
        key = ref if isinstance(ref, str) else canonical(ref)
        if key not in self._shaders:
            spec = self._shader_spec(ref, ())
            where = f"textures.shaders.{ref}" if isinstance(ref, str) else "inline shader"
            self._shaders[key] = (SHADERS.resolve(require(spec, "type", where))(spec, where), spec)
        return self._shaders[key]

    def _shader_spec(self, ref: Any, chain: Tuple[str, ...]) -> dict:
        if isinstance(ref, str):
            if ref in chain:
                raise ConfigError(f"shader 'extends' cycle: {' -> '.join(chain + (ref,))}")
            if ref not in self.shaders:
                raise ConfigError(f"unknown shader {ref!r}{did_you_mean(ref, self.shaders)}")
            spec, chain = self.shaders[ref], chain + (ref,)
        else:
            spec = ref
        if "extends" not in spec:
            return copy.deepcopy(spec)
        own = {k: v for k, v in spec.items() if k != "extends"}
        return _merge_deep(self._shader_spec(spec["extends"], chain), own)


# =============================================================================
# 6. Renderers: entry -> Job (owned files + fingerprint payload + lazy render)
# =============================================================================
RENDERERS = Registry("renderer")


@dataclass
class Job:
    entry: Entry
    files: List[str]
    payload: Any
    render: Callable[[], Dict[str, bytes]]


class Renderer(ABC):
    def __init__(self, env: RenderEnv, encode: Callable[[Image.Image], bytes]):
        self.env, self.encode = env, encode

    @staticmethod
    def texture_file(texture_id: str) -> str:
        namespace, path = texture_id.split(":", 1)
        return f"assets/{namespace}/textures/{path}.png"

    @abstractmethod
    def prepare(self, entry: Entry) -> Job:
        ...


@RENDERERS.register("layered")
class LayeredRenderer(Renderer):
    """Composites N layers (template + palette + shader + blend). One layer = mono-construction."""

    LAYER_KEYS = ("template", "palette", "shader", "blend", "context", "transforms")

    def prepare(self, entry: Entry) -> Job:
        spec, env = entry.spec, self.env
        raw_layers = spec.get("layers") or [spec.get("template", spec["id"])]
        defaults = spec.get("layer_defaults", {})
        layers = []
        for i, raw in enumerate(raw_layers):
            raw = {"template": raw} if isinstance(raw, (str, list)) else raw
            try:
                layers.append(interpolate({**defaults, **raw}, entry.ctx))
            except Unresolved as err:
                raise ConfigError(f"{entry.key}: layer {i} {err}") from None

        where = lambda i: f"{entry.key} layer {i}"
        for i, layer in enumerate(layers):
            for key in layer:
                if key not in self.LAYER_KEYS:
                    warn_once(f"parts.{entry.part} layer {i}: unknown key {key!r} ignored"
                              f"{did_you_mean(key, self.LAYER_KEYS)}")
        masks = [apply_transforms(env.mask(require(l, "template", where(i))), l.get("transforms"), where(i))
                 for i, l in enumerate(layers)]
        planned, payload = [], []
        for i, layer in enumerate(layers):
            context_mode = layer.get("context", "self")
            shader, shader_spec = env.shader(require(layer, "shader", where(i)))
            palette = env.palette(require(layer, "palette", where(i)))
            blend_mode = layer.get("blend", "over")
            planned.append((masks[i], CONTEXTS.resolve(context_mode)(i, masks), shader, palette,
                            BLENDS.resolve(blend_mode)))
            payload.append({"mask": masks[i].canonical(), "context": context_mode, "shader": shader_spec,
                            "palette": palette.tones, "blend": blend_mode})

        path = self.texture_file(entry.texture)
        size = env.size

        def render() -> Dict[str, bytes]:
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            for mask, context, shader, palette, blend in planned:
                layer_image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                for y, row in enumerate(shader.shade(mask, context)):
                    for x, tone in enumerate(row):
                        if tone is not None:
                            layer_image.putpixel((x, y), palette.color(tone))
                canvas = blend(canvas, layer_image)
            return {path: self.encode(canvas)}

        return Job(entry, [path], {"renderer": "layered", "size": size, "layers": payload}, render)


# =============================================================================
# 7. Sinks: a folder or a zip, behind one interface
# =============================================================================
SINKS = Registry("sink")


class Sink(ABC):
    label = "sink"

    @abstractmethod
    def read(self, rel: str) -> Optional[bytes]: ...

    @abstractmethod
    def _put(self, rel: str, data: bytes) -> None: ...

    @abstractmethod
    def delete(self, rel: str) -> bool: ...

    @abstractmethod
    def find(self, filename: str) -> Iterator[str]:
        """Every stored path whose last component equals filename."""

    def exists(self, rel: str) -> bool:
        return self.read(rel) is not None

    def write(self, rel: str, data: bytes) -> bool:
        """Write unless identical bytes are already stored. Returns True if changed."""
        if self.read(rel) == data:
            return False
        self._put(rel, data)
        return True

    def commit(self) -> bool:
        return False


@SINKS.register("dir")
class DirectorySink(Sink):
    def __init__(self, parent: Path, name: str):
        self.root = parent / name
        self.label = f"{self.root} (folder)"
        self._touched: set = set()

    def _path(self, rel: str) -> Path:
        return self.root.joinpath(*PurePosixPath(rel).parts)

    def read(self, rel):
        path = self._path(rel)
        return path.read_bytes() if path.is_file() else None

    def exists(self, rel):
        return self._path(rel).is_file()

    def _put(self, rel, data):
        path = self._path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def delete(self, rel):
        path = self._path(rel)
        if not path.is_file():
            return False
        path.unlink()
        self._touched.add(path.parent)
        return True

    def find(self, filename):
        if self.root.is_dir():
            for path in sorted(self.root.rglob(filename)):
                yield path.relative_to(self.root).as_posix()

    def commit(self):
        for folder in sorted(self._touched, key=lambda p: len(p.parts), reverse=True):
            while folder != self.root and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        return bool(self._touched)


@SINKS.register("zip")
class ZipSink(Sink):
    """Whole archive held in memory (16x16 PNGs are tiny) and rewritten only if something changed."""

    STAMP = (1980, 1, 1, 0, 0, 0)  # fixed timestamps -> byte-identical zips for identical content

    def __init__(self, parent: Path, name: str, level: int = 9):
        self.path = parent / f"{name}.zip"
        self.label = f"{self.path} (zip)"
        self.level = level
        self.entries: Dict[str, bytes] = {}
        self.dirty = False
        if self.path.is_file():
            with zipfile.ZipFile(self.path) as archive:
                self.entries = {i.filename: archive.read(i) for i in archive.infolist() if not i.is_dir()}

    def read(self, rel):
        return self.entries.get(rel)

    def exists(self, rel):
        return rel in self.entries

    def _put(self, rel, data):
        self.entries[rel] = data
        self.dirty = True

    def delete(self, rel):
        if self.entries.pop(rel, None) is None:
            return False
        self.dirty = True
        return True

    def find(self, filename):
        return iter(sorted(n for n in self.entries if PurePosixPath(n).name == filename))

    def commit(self):
        if not self.dirty:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=self.level) as archive:
            for name in sorted(self.entries):
                info = zipfile.ZipInfo(name, date_time=self.STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, self.entries[name])
        os.replace(tmp, self.path)
        return True


class DryRunSink(Sink):
    """Reads through to the real sink, records writes/deletes, never touches disk."""

    def __init__(self, inner: Sink):
        self.inner, self.label = inner, f"{inner.label} [dry run]"
        self.writes: Dict[str, bytes] = {}
        self.deletes: set = set()

    def read(self, rel):
        return None if rel in self.deletes else self.writes.get(rel, self.inner.read(rel))

    def _put(self, rel, data):
        self.writes[rel] = data
        self.deletes.discard(rel)
        log.info("  would write %s", rel)

    def delete(self, rel):
        if self.read(rel) is None:
            return False
        self.deletes.add(rel)
        log.info("  would delete %s", rel)
        return True

    def find(self, filename):
        return self.inner.find(filename)


# =============================================================================
# 8. Manifests: one hash.txt per texture folder, "<file name>\t<fingerprint>" per line
# =============================================================================
class ManifestStore:
    def __init__(self, sink: Sink, filename: str, header: str):
        self.sink, self.filename, self.header = sink, filename, header
        self.old: Dict[str, Dict[str, str]] = {}
        self.new: Dict[str, Dict[str, str]] = {}

    @staticmethod
    def split(rel: str) -> Tuple[str, str]:
        path = PurePosixPath(rel)
        return str(path.parent), path.name

    def _manifest(self, folder: str) -> str:
        return f"{folder}/{self.filename}"

    def _load(self, folder: str) -> Dict[str, str]:
        if folder not in self.old:
            raw = self.sink.read(self._manifest(folder)) or b""
            entries = {}
            for line in raw.decode("utf-8").splitlines():
                if line and not line.startswith("#") and "\t" in line:
                    name, digest = line.split("\t", 1)
                    entries[name] = digest.strip()
            self.old[folder] = entries
        return self.old[folder]

    def is_current(self, files: Sequence[str], digest: str) -> bool:
        return all(self._load(folder).get(name) == digest and self.sink.exists(rel)
                   for rel in files for folder, name in [self.split(rel)])

    def record(self, files: Sequence[str], digest: str) -> None:
        for rel in files:
            folder, name = self.split(rel)
            self._load(folder)
            self.new.setdefault(folder, {})[name] = digest

    def finish(self, prune: bool) -> int:
        """Write changed manifests. With prune, delete files owned by stale manifest lines."""
        for manifest in list(self.sink.find(self.filename)):
            self._load(str(PurePosixPath(manifest).parent))
        pruned = 0
        for folder, old in self.old.items():
            current = dict(self.new.get(folder, {}))
            for name in (n for n in old if n not in current):
                if prune:
                    pruned += self.sink.delete(f"{folder}/{name}")
                else:
                    current[name] = old[name]  # partial run: keep what we did not look at
            if current:
                body = "".join(f"{n}\t{d}\n" for n, d in sorted(current.items()))
                self.sink.write(self._manifest(folder), (self.header + body).encode("utf-8"))
            else:
                self.sink.delete(self._manifest(folder))
        return pruned


# =============================================================================
# 9. Pipeline + CLI
# =============================================================================
DEFAULT_CONFIG = Path(__file__).resolve().parent / "mintech.json"


def read_json(path: Path, what: str = "config") -> dict:
    try:
        return strip_comments(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (pass --config to point at another {what})") from None
    except json.JSONDecodeError as err:
        raise ConfigError(f"{path}: invalid JSON at line {err.lineno}, col {err.colno}: {err.msg}") from None


def load_config(path: Path) -> dict:
    """The index file plus everything its 'include' list names, deep-merged in order.

    Each include is a fragment keyed by the same top-level sections as the index, so a section
    may be spread over several files ('parts' across parts.json and ores.json, say) and still
    arrive here as one dict. Later files win on a conflict; the JS loader merges identically."""
    cfg = read_json(path)
    for name in as_list(cfg.pop("include", None)):
        fragment = read_json(path.parent / str(name), f"'{name}', included from {path}")
        if "include" in fragment:
            raise ConfigError(f"{name}: nested 'include' is not supported; list it in {path.name} instead")
        cfg = _merge_deep(cfg, fragment)
    for key in ("meta.namespace", "meta.version", "textures.output"):
        require(cfg, key, str(path))
    return cfg


def config_root(path: Path, cfg: Mapping[str, Any]) -> Path:
    """Where the config's relative paths (output roots, template images) are anchored:
    meta.root resolved from the config's own folder, so the cwd never matters."""
    return (path.parent / str(cfg["meta"].get("root", "."))).resolve()


def png_encoder(options: Mapping[str, Any]) -> Callable[[Image.Image], bytes]:
    def encode(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, "PNG", **options)  # any Pillow PNG option from textures.output.png
        return buffer.getvalue()
    return encode


def source_digest() -> str:
    """Hash of this script: editing the generator invalidates every fingerprint automatically."""
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    except OSError:  # pragma: no cover  (frozen / embedded)
        return "unknown"


def write_preview(path: Path, tiles: Mapping[Tuple[str, str], Mapping[str, bytes]], columns: Sequence[str],
                  size: int, scale: int) -> None:
    """Upscaled contact sheet (rows = family:material, columns = parts) for eyeballing templates.
    Written outside the pack; never part of the resource pack itself."""
    cell, gap = size * scale, max(2, scale // 2)
    rows = list(tiles)
    sheet = Image.new("RGBA", (gap + len(columns) * (cell + gap), gap + len(rows) * (cell + gap)), (48, 48, 56, 255))
    checker = Image.new("RGBA", (size, size))
    checker.putdata([(139, 139, 139, 255) if (x + y) % 2 else (150, 150, 150, 255)
                     for y in range(size) for x in range(size)])
    for r, row in enumerate(rows):
        for part, data in tiles[row].items():
            tile = checker.copy()
            tile.alpha_composite(Image.open(io.BytesIO(data)).convert("RGBA"))
            sheet.paste(tile.resize((cell, cell), Image.Resampling.NEAREST),
                        (gap + columns.index(part) * (cell + gap), gap + r * (cell + gap)))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    log.warning("preview: %d rows x %d columns -> %s", len(rows), len(columns), path)


class Pipeline:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config_path = args.config.resolve()
        self.root = self.config_path.parent  # replaced by meta.root once the config is loaded

    def selected(self, entries: List[Entry]) -> List[Entry]:
        if not self.args.only:
            return entries
        return [e for e in entries if any(fnmatch.fnmatchcase(e.key, pattern) for pattern in self.args.only)]

    def open_sink(self, output: dict, compress: bool) -> Sink:
        parent = self.root / require(output, "root", "textures.output")
        name = require(output, "pack_name", "textures.output")
        sink = ZipSink(parent, name, int(output.get("zip_level", 9))) if compress else DirectorySink(parent, name)
        other = parent / name if compress else parent / f"{name}.zip"
        if other.exists():
            log.warning("both %s and %s exist; Paxi will load both. Delete the one you do not use.",
                        sink.label.split(" (")[0], other)
        return DryRunSink(sink) if self.args.dry_run else sink

    def run(self) -> int:
        started = time.perf_counter()
        cfg = load_config(self.config_path)
        self.root = config_root(self.config_path, cfg)
        textures, meta = cfg["textures"], cfg["meta"]
        output = textures["output"]
        entries = build_catalog(cfg)  # 'when' conditions are game-side; textures cover everything

        if self.args.list:
            for e in entries:
                print(f"{e.key:36} {e.id:32} {e.texture:36} {e.ctx['name']}")
            return 0

        compress = output.get("compress", False) if self.args.compress is None else self.args.compress
        prune = output.get("prune", False) if self.args.prune is None else self.args.prune
        partial = bool(self.args.only)
        if prune and partial:
            log.info("--only given: pruning disabled for this partial run")

        png_options = output.get("png", {})
        env = RenderEnv(cfg, self.root)
        sink = self.open_sink(output, compress)
        salt = {"version": meta["version"], "code": source_digest(), "png": png_options}
        manifests = ManifestStore(
            sink, output.get("manifest", "hash.txt"),
            f"# {meta['namespace']} texture manifest (meta.version {meta['version']}) - generated, do not edit\n")

        mcmeta = interpolate(textures.get("mcmeta", {}), {"meta": meta, "vars": cfg.get("vars", {})})
        if mcmeta:
            sink.write("pack.mcmeta", (json.dumps(mcmeta, indent=2) + "\n").encode("utf-8"))

        renderers: Dict[str, Renderer] = {}
        owners: Dict[str, str] = {}
        stats: Counter = Counter()
        tiles: Dict[Tuple[str, str], Dict[str, bytes]] = {}
        columns: List[str] = []
        for entry in self.selected(entries):
            renderer_name = entry.spec.get("renderer")
            if renderer_name is None:
                stats["no renderer"] += 1
                continue
            if renderer_name not in renderers:
                renderers[renderer_name] = RENDERERS.resolve(renderer_name)(env, png_encoder(png_options))
            job = renderers[renderer_name].prepare(entry)
            for rel in job.files:
                if rel in owners:
                    raise ConfigError(f"{rel} is produced by both {owners[rel]} and {entry.key}")
                owners[rel] = entry.key

            produced: Optional[Dict[str, bytes]] = None
            digest = fingerprint({"salt": salt, "job": job.payload})
            if not self.args.force and manifests.is_current(job.files, digest):
                stats["unchanged"] += 1
                log.debug("  unchanged %s", entry.key)
            else:
                produced = job.render()
                if sorted(produced) != sorted(job.files):
                    raise RuntimeError(f"renderer {renderer_name} produced {sorted(produced)}, declared {job.files}")
                for rel, data in produced.items():
                    stats["written"] += sink.write(rel, data)
                stats["rendered"] += 1
                log.info("  rendered  %-28s -> %s", entry.key, ", ".join(job.files))
            manifests.record(job.files, digest)
            if self.args.preview and len(job.files) == 1:
                data = (produced or {}).get(job.files[0]) or sink.read(job.files[0])
                if data:
                    tiles.setdefault((entry.family, entry.material), {})[entry.part] = data
                    columns += [] if entry.part in columns else [entry.part]

        stats["pruned"] = manifests.finish(prune=prune and not partial)
        committed = sink.commit()
        if self.args.preview:
            write_preview(self.args.preview, tiles, columns, env.size, self.args.preview_scale)
        summary = ", ".join(f"{k} {v}" for k, v in stats.items() if v) or "nothing to do"
        log.warning("%s -> %s%s in %.2fs", summary, sink.label,
                    " (archive rewritten)" if committed and compress else "", time.perf_counter() - started)
        return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MinTech item textures from mintech/mintech.json.")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"path to the config index (default: {DEFAULT_CONFIG.name} next to this script); "
                             "it pulls in its own 'include' files, and output paths resolve against its meta.root")
    parser.add_argument("--compress", action=argparse.BooleanOptionalAction, default=None,
                        help="write <pack_name>.zip instead of a folder (default: textures.output.compress)")
    parser.add_argument("--prune", action=argparse.BooleanOptionalAction, default=None,
                        help="delete generated files no longer in the catalog (default: textures.output.prune)")
    parser.add_argument("-f", "--force", action="store_true", help="ignore hash.txt and re-render everything")
    parser.add_argument("--only", action="append", metavar="FAMILY:MATERIAL:PART",
                        help="glob filter, repeatable, e.g. 'metal:*:gear' (disables pruning)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--list", action="store_true", help="print the expanded catalog and exit")
    parser.add_argument("--preview", type=Path, metavar="PNG",
                        help="also write an upscaled contact sheet of every selected texture (outside the pack)")
    parser.add_argument("--preview-scale", type=int, default=12, metavar="N", help="preview upscale factor (default 12)")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v per-file log, -vv debug")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(message)s")
    try:
        return Pipeline(args).run()
    except ConfigError as err:
        log.error("%s: %s", args.config, err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
