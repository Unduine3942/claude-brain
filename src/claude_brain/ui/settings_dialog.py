"""Settings modal dialog."""

import tkinter as tk
from tkinter import ttk

from claude_brain.config import COLORS, CONSOLIDATIONS_DIR, DB_PATH, FONTS, PAD_LG, PAD_MD, PAD_SM

C = COLORS


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, repos):
        super().__init__(parent)
        self._repos = repos
        self.title("Settings")
        self.geometry("520x200")
        self.resizable(False, False)
        self.configure(bg=C["bg"])
        self.transient(parent)
        self.grab_set()
        self._build()

    def _build(self) -> None:
        tk.Label(self, text="SETTINGS", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"]).pack(
            anchor="w", padx=PAD_LG, pady=(PAD_LG, PAD_SM)
        )

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        form = ttk.Frame(self, style="TFrame")
        form.pack(fill="x", padx=PAD_LG, pady=PAD_MD)

        for label, value in [
            ("Database:", str(DB_PATH)),
            ("Consolidations:", str(CONSOLIDATIONS_DIR)),
        ]:
            row = ttk.Frame(form, style="TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, style="TLabel", width=18).pack(side="left")
            ttk.Label(row, text=value, style="Muted.TLabel").pack(side="left")

        btn_frame = ttk.Frame(self, style="TFrame")
        btn_frame.pack(fill="x", padx=PAD_LG, pady=(PAD_MD, PAD_LG))
        ttk.Button(btn_frame, text="Close", style="Ghost.TButton", command=self.destroy).pack(
            side="left"
        )
