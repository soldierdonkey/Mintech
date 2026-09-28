"""Build tab: runs the real mintech_textures.py / mintech_worldgen.py CLIs as subprocesses
(never reimplemented here), streaming their output live, plus in-process validation via the
same build_catalog()/build_veins() calls the generators use."""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from .. import mintech_bridge as bridge


class BuildPanel(ttk.Frame):
    def __init__(self, parent, workspace, status):
        super().__init__(parent)
        self.ws = workspace
        self.status = status
        self._queue: "queue.Queue" = queue.Queue()
        self._proc: Optional[subprocess.Popen] = None
        self._on_done = None

        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="Save all changes", command=self._save_all).pack(side="left")
        ttk.Button(top, text="Validate config", command=self._validate).pack(side="left", padx=4)
        ttk.Button(top, text="Render textures", command=lambda: self.run_textures([])).pack(side="left", padx=(12, 4))
        ttk.Button(top, text="Render textures (force)", command=lambda: self.run_textures(["--force"])).pack(side="left")
        ttk.Button(top, text="Generate worldgen", command=lambda: self.run_worldgen([])).pack(side="left", padx=4)
        ttk.Button(top, text="Preview sheet...", command=self._preview_sheet).pack(side="left", padx=4)

        self.log = tk.Text(self, height=28, font=("Menlo", 11), state="disabled",
                            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4")
        self.log.pack(fill="both", expand=True, padx=6, pady=(0, 6))

    # -- public entry points used by other tabs -----------------------------
    def run_worldgen(self, args: Optional[List[str]] = None) -> None:
        self._run("mintech_worldgen.py", args or [])

    def run_textures(self, args: Optional[List[str]] = None) -> None:
        self._run("mintech_textures.py", args or [])

    # -- actions --------------------------------------------------------------
    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _save_all(self) -> None:
        saved = self.ws.save_all()
        if saved:
            self._append(f"Saved: {', '.join(saved)}\n")
            self.status(f"Saved {len(saved)} file(s).")
        else:
            self._append("Nothing to save.\n")

    def _validate(self) -> None:
        try:
            cfg = self.ws.merged_cfg()
            entries = bridge.build_catalog(cfg)
            veins = bridge.build_veins(cfg)
        except bridge.ConfigError as err:
            self._append(f"CONFIG ERROR: {err}\n")
            self.status("Validation failed -- see Build log.")
            return
        self._append(f"OK -- {len(entries)} item(s), {len(veins)} vein(s), no config errors.\n")
        self.status("Config is valid.")

    def _run(self, script: str, args: List[str], on_done=None) -> None:
        if self._proc is not None:
            self._append("A generator is already running.\n")
            return
        unsaved = self.ws.dirty_files()
        if unsaved:
            if not messagebox.askyesno(
                "Unsaved changes",
                f"{', '.join(unsaved)} have unsaved changes.\n"
                f"{script} reads mintech/*.json from disk -- save now and continue?",
            ):
                return
            self.ws.save_all()
        self._append(f"\n$ {sys.executable} {script} {' '.join(args)}\n")
        self._proc = bridge.run_generator(script, args)
        self._on_done = on_done
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        self.after(80, self._poll)

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self._queue.put(line)
        proc.wait()
        self._queue.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    code = self._proc.returncode if self._proc else None
                    self._append(f"(exit code {code})\n")
                    proc, on_done = self._proc, self._on_done
                    self._proc, self._on_done = None, None
                    self.status(f"{'Finished' if code == 0 else 'Failed'} ({code}).")
                    if on_done and code == 0:
                        on_done()
                    return
                self._append(item)
        except queue.Empty:
            pass
        if self._proc is not None:
            self.after(80, self._poll)

    def _preview_sheet(self) -> None:
        out = bridge.MINTECH_DIR / "editor" / "preview_sheet.png"

        def opened():
            self._append(f"Preview sheet written to {out}\n")
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(out)])

        self._run("mintech_textures.py", ["--preview", str(out)], on_done=opened)
