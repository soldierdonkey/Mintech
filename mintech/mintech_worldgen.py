#!/usr/bin/env python3
"""
mintech_worldgen.py -- data-driven ore worldgen datapack generator.

    mintech.json --> materials x veins --> configured_feature + placed_feature + biome_modifier
                                                \\--> sink (folder | zip) + manifest

Companion to mintech_textures.py; it reuses that module's config loading, catalog expansion,
interpolation, sinks and manifests, and adds only the mechanisms specific to worldgen:

    TARGETS     "#tag" / "block" -> an ore target predicate
    HEIGHTS     a {type, min, max} height range -> a vanilla height provider

Ore placement cannot be scripted in this pack: KubeJS 6 for 1.20.1 ships
WorldgenEvents.addOre() as a stub whose whole body logs "not supported in 1.20 yet". On Forge
a forge:biome_modifier is also the only thing that injects a placed feature into biomes, and
biome modifiers are datapack-only, so every vein becomes three JSON files.

A vein is one feature with one target per part, so 'ore' and 'deepslate_ore' share a single
roll exactly like vanilla iron instead of rolling independently and doubling the spawn rate.

Run from the instance root (next to mintech.json):
    python mintech_worldgen.py                    incremental build
    python mintech_worldgen.py --list             print the veins and exit
    python mintech_worldgen.py --dry-run -v       report what would change
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mintech_textures import (
    ConfigError, DirectorySink, DryRunSink, ManifestStore, Registry, Sink, Unresolved, ZipSink,
    as_list, build_catalog, did_you_mean, fingerprint, inherit, interpolate, load_config,
    qualify, require,
)

log = logging.getLogger("mintech.worldgen")

TARGETS = Registry("target predicate")
TARGETS.register("tag")(lambda value: {"predicate_type": "minecraft:tag_match", "tag": value})
TARGETS.register("block")(lambda value: {"predicate_type": "minecraft:block_match", "block": value})

HEIGHTS = Registry("height type")
HEIGHTS.register("uniform")("minecraft:uniform")
HEIGHTS.register("trapezoid")("minecraft:trapezoid")
HEIGHTS.register("triangle")("minecraft:trapezoid")


def target(replaces: str) -> dict:
    text = str(replaces)
    kind, value = ("tag", text[1:]) if text.startswith("#") else ("block", text)
    return TARGETS.resolve(kind)(value)


def height_provider(spec: Any, where: str) -> dict:
    if not isinstance(spec, Mapping):
        raise ConfigError(f"{where}: height must be {{type, min, max}}, got {spec!r}")
    return {"type": HEIGHTS.resolve(spec.get("type", "uniform")),
            "min_inclusive": {"absolute": int(require(spec, "min", where))},
            "max_inclusive": {"absolute": int(require(spec, "max", where))}}


@dataclass
class Vein:
    material: str
    name: str
    id: str
    spec: dict
    targets: List[Tuple[str, str]]

    @property
    def key(self) -> str:
        return f"{self.material}:{self.name}"


def build_veins(cfg: dict) -> List[Vein]:
    namespace = require(cfg, "meta.namespace")
    rules = cfg.get("inheritance", {})
    defaults = require(cfg, "worldgen.vein_defaults")
    parts = cfg.get("parts", {})

    blocks: Dict[Tuple[str, str], Dict[str, str]] = {}
    for entry in build_catalog(cfg):
        blocks.setdefault((entry.family, entry.material), {})[entry.part] = entry.id

    veins: List[Vein] = []
    owners: Dict[str, str] = {}
    for mid, material in (cfg.get("materials") or {}).items():
        for i, raw in enumerate(as_list((material or {}).get("veins"))):
            where = f"materials.{mid}.veins[{i}]"
            merged = inherit([defaults, raw], rules)
            ctx = {"meta": cfg["meta"], "vars": cfg.get("vars", {}),
                   "material": {**(material or {}), "id": mid}, "vein": merged}
            try:
                spec = interpolate(merged, ctx)
            except Unresolved as err:
                raise ConfigError(f"{where}: {err}") from None

            name = str(require(spec, "name", where))
            family = str(require(spec, "family", where))
            available = blocks.get((family, mid), {})
            targets = []
            for pid in as_list(require(spec, "parts", where)):
                if pid not in available:
                    raise ConfigError(
                        f"{where}: part {pid!r} is not built for {mid} in family {family!r}"
                        f" (add it to families.{family}.parts){did_you_mean(pid, available)}")
                replaces = require(parts.get(pid) or {}, "worldgen.replaces", f"parts.{pid}")
                targets.append((str(replaces), available[pid]))

            vein_id = qualify(str(require(spec, "patterns.id", where)), namespace)
            if vein_id in owners:
                raise ConfigError(f"vein id {vein_id} produced by both {owners[vein_id]} and {where}")
            owners[vein_id] = where
            veins.append(Vein(mid, name, vein_id, spec, targets))
    return veins


def vein_files(vein: Vein) -> Dict[str, Any]:
    spec, where = vein.spec, vein.key
    namespace, path = vein.id.split(":", 1)

    configured = {
        "type": "minecraft:ore",
        "config": {
            "size": int(require(spec, "size", where)),
            "discard_chance_on_air_exposure": float(spec.get("discard_chance", 0)),
            "targets": [{"target": target(replaces), "state": {"Name": block}}
                        for replaces, block in vein.targets],
        },
    }

    placement: List[dict] = [{"type": "minecraft:count", "count": require(spec, "count", where)}]
    if spec.get("squared", True):
        placement.append({"type": "minecraft:in_square"})
    placement.append({"type": "minecraft:height_range",
                      "height": height_provider(require(spec, "height", where), where)})
    placement.append({"type": "minecraft:biome"})

    biome_modifier = {
        "type": "forge:add_features",
        "biomes": require(spec, "biomes", where),
        "features": vein.id,
        "step": str(require(spec, "step", where)),
    }

    return {
        f"data/{namespace}/worldgen/configured_feature/{path}.json": configured,
        f"data/{namespace}/worldgen/placed_feature/{path}.json": {
            "feature": vein.id, "placement": placement},
        f"data/{namespace}/forge/biome_modifier/{path}.json": biome_modifier,
    }


def encode(document: Any) -> bytes:
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def source_digest() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    except OSError:  # pragma: no cover
        return "unknown"


class Pipeline:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config_path = args.config.resolve()
        self.root = self.config_path.parent

    def selected(self, veins: List[Vein]) -> List[Vein]:
        if not self.args.only:
            return veins
        return [v for v in veins
                if any(fnmatch.fnmatchcase(v.key, pattern) for pattern in self.args.only)]

    def open_sink(self, output: dict, compress: bool) -> Sink:
        parent = self.root / require(output, "root", "worldgen.output")
        name = require(output, "pack_name", "worldgen.output")
        sink = ZipSink(parent, name, int(output.get("zip_level", 9))) if compress \
            else DirectorySink(parent, name)
        other = parent / name if compress else parent / f"{name}.zip"
        if other.exists():
            log.warning("both %s and %s exist; Paxi will load both. Delete the one you do not use.",
                        sink.label.split(" (")[0], other)
        return DryRunSink(sink) if self.args.dry_run else sink

    def run(self) -> int:
        started = time.perf_counter()
        cfg = load_config(self.config_path)
        meta = cfg["meta"]
        output = require(cfg, "worldgen.output")
        veins = build_veins(cfg)

        if self.args.list:
            for v in veins:
                blocks = ", ".join(f"{replaces} -> {block}" for replaces, block in v.targets)
                log.warning("%-28s %-26s count %-4s size %-3s %s", v.key, v.id,
                            v.spec.get("count"), v.spec.get("size"), v.spec.get("biomes"))
                log.warning("%30s%s", "", blocks)
            return 0

        compress = output.get("compress", False) if self.args.compress is None else self.args.compress
        prune = output.get("prune", False) if self.args.prune is None else self.args.prune
        partial = bool(self.args.only)
        if prune and partial:
            log.info("--only given: pruning disabled for this partial run")

        sink = self.open_sink(output, compress)
        salt = {"version": meta["version"], "code": source_digest()}
        manifests = ManifestStore(
            sink, output.get("manifest", "hash.txt"),
            f"# {meta['namespace']} worldgen manifest (meta.version {meta['version']})"
            " - generated, do not edit\n")

        mcmeta = interpolate(cfg["worldgen"].get("mcmeta", {}),
                            {"meta": meta, "vars": cfg.get("vars", {})})
        if mcmeta:
            sink.write("pack.mcmeta", encode(mcmeta))

        owners: Dict[str, str] = {}
        stats: Counter = Counter()
        for vein in self.selected(veins):
            documents = vein_files(vein)
            for rel in documents:
                if rel in owners:
                    raise ConfigError(f"{rel} is produced by both {owners[rel]} and {vein.key}")
                owners[rel] = vein.key

            files = sorted(documents)
            digest = fingerprint({"salt": salt, "documents": [documents[f] for f in files]})
            if not self.args.force and manifests.is_current(files, digest):
                stats["unchanged"] += 1
                log.debug("  unchanged %s", vein.key)
            else:
                for rel in files:
                    stats["written"] += sink.write(rel, encode(documents[rel]))
                stats["generated"] += 1
                log.info("  generated %-28s -> %s", vein.key, ", ".join(files))
            manifests.record(files, digest)

        stats["pruned"] = manifests.finish(prune=prune and not partial)
        committed = sink.commit()
        summary = ", ".join(f"{k} {v}" for k, v in stats.items() if v) or "nothing to do"
        log.warning("%s -> %s%s in %.2fs", summary, sink.label,
                    " (archive rewritten)" if committed and compress else "",
                    time.perf_counter() - started)
        return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the MinTech ore worldgen datapack from mintech.json.")
    parser.add_argument("-c", "--config", type=Path, default=Path("mintech.json"),
                        help="path to mintech.json (default: ./mintech.json); output paths are relative to its folder")
    parser.add_argument("--compress", action=argparse.BooleanOptionalAction, default=None,
                        help="write <pack_name>.zip instead of a folder (default: worldgen.output.compress)")
    parser.add_argument("--prune", action=argparse.BooleanOptionalAction, default=None,
                        help="delete generated files no longer in the config (default: worldgen.output.prune)")
    parser.add_argument("-f", "--force", action="store_true", help="ignore hash.txt and rewrite everything")
    parser.add_argument("--only", action="append", metavar="MATERIAL:VEIN",
                        help="glob filter, repeatable, e.g. 'titanium:*' (disables pruning)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--list", action="store_true", help="print the expanded veins and exit")
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
