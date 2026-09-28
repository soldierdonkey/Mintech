"""MinTech config editor -- a GUI for mintech/*.json.

Everything the editor itself owns (its code, its small UI-state file) lives in this
folder. The JSON files it edits (materials.json, parts.json, ...) stay where they are,
one level up, and are read/written in place -- this package never copies their data in.
"""
