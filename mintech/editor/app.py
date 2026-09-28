"""Top-level window: wires the workspace up to one tab per concern."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import mintech_bridge as bridge
from . import state as editor_state
from .panels.build import BuildPanel
from .panels.families import FamiliesPanel
from .panels.materials import MaterialsPanel
from .panels.ores import OresPanel
from .panels.palettes import PalettesPanel
from .panels.raw import RawPanel
from .workspace import Workspace


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MinTech Config Editor")
        self.state_data = editor_state.load()
        self.geometry(self.state_data.get("geometry", "1280x860"))

        try:
            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        self.ws = Workspace(bridge.MINTECH_DIR)

        self._build_menu()

        self.status_var = tk.StringVar(value=f"Loaded {bridge.MINTECH_DIR}")
        status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken", padding=(6, 2))
        status_bar.pack(side="bottom", fill="x")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.materials_panel = MaterialsPanel(self.notebook, self.ws, self.set_status)
        self.notebook.add(self.materials_panel, text="Materials")

        self.build_panel = BuildPanel(self.notebook, self.ws, self.set_status)

        self.ores_panel = OresPanel(self.notebook, self.ws, self.set_status, goto_build=self._goto_build_worldgen)
        self.notebook.add(self.ores_panel, text="Ores && Worldgen")

        self.palettes_panel = PalettesPanel(self.notebook, self.ws, self.set_status)
        self.notebook.add(self.palettes_panel, text="Palettes")

        self.families_panel = FamiliesPanel(self.notebook, self.ws, self.set_status)
        self.notebook.add(self.families_panel, text="Families")

        self.raw_panel = RawPanel(self.notebook, self.ws, self.set_status, on_apply=self.refresh_all)
        self.notebook.add(self.raw_panel, text="Raw files")

        self.notebook.add(self.build_panel, text="Build")

        try:
            self.notebook.select(int(self.state_data.get("last_tab", 0)))
        except (tk.TclError, ValueError):
            pass

        self.bind_all("<Command-s>", lambda _e: self._save_all())
        self.bind_all("<Control-s>", lambda _e: self._save_all())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Save All", command=self._save_all, accelerator="Cmd+S")
        file_menu.add_command(label="Reload All From Disk", command=self._reload_all)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.configure(menu=menubar)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def refresh_all(self) -> None:
        """Called after a Raw-tab Apply, so the specialized tabs pick up out-of-band edits."""
        self.materials_panel.refresh_list()
        self.ores_panel.refresh_materials()
        self.families_panel.refresh_list()
        self.set_status("Refreshed all tabs from the applied change.")

    def _save_all(self) -> None:
        saved = self.ws.save_all()
        self.set_status(f"Saved {', '.join(saved)}." if saved else "Nothing to save.")

    def _reload_all(self) -> None:
        if self.ws.any_dirty() and not messagebox.askyesno(
            "Reload all", "This discards unsaved changes in: " + ", ".join(self.ws.dirty_files()) + ". Continue?"
        ):
            return
        self.ws.load_all()
        self.refresh_all()
        self.palettes_panel.destroy()
        self.palettes_panel = PalettesPanel(self.notebook, self.ws, self.set_status)
        self.notebook.insert(2, self.palettes_panel, text="Palettes")
        self.set_status("Reloaded everything from disk.")

    def _goto_build_worldgen(self) -> None:
        self.notebook.select(self.build_panel)
        self.build_panel.run_worldgen()

    def _on_close(self) -> None:
        if self.ws.any_dirty():
            if not messagebox.askyesno(
                "Unsaved changes",
                "You have unsaved changes in: " + ", ".join(self.ws.dirty_files()) + ". Quit without saving?",
            ):
                return
        self.state_data["geometry"] = self.geometry()
        self.state_data["last_tab"] = self.notebook.index(self.notebook.select())
        editor_state.save(self.state_data)
        self.destroy()


def main() -> int:
    app = App()
    app.mainloop()
    return 0
