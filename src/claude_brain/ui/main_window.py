"""Main application window — header + project panel + content area."""

import tkinter as tk
from datetime import datetime

from claude_brain.config import COLORS, FONTS, PAD_LG, PAD_SM
from claude_brain.ui.widgets import StatusBar

C = COLORS


class MainWindow:
    def __init__(self, root: tk.Tk, repos, on_settings):
        self._root = root
        self._repos = repos
        self._on_settings = on_settings
        self._active_view: tk.Frame | None = None

        root.title("Claude Brain")
        root.geometry("1260x780")
        root.minsize(960, 620)
        root.configure(bg=C["bg"])

        self._build_status_bar()
        self._build_header()
        self._build_body()
        self._start_clock()
        self.show_global()

    # ---- public --------------------------------------------------------

    @property
    def status_bar(self) -> StatusBar:
        return self._status_bar

    def show_global(self) -> None:
        self._project_view.pack_forget()
        self._global_view.pack(fill="both", expand=True)
        self._global_view.refresh()
        self._active_view = self._global_view

    def show_project(self, project_id: str) -> None:
        self._global_view.pack_forget()
        self._project_view.pack(fill="both", expand=True)
        self._active_view = self._project_view

    def refresh_status(self, vault_count: int) -> None:
        self._status_bar.set_center(
            f"◈ {vault_count} vault{'s' if vault_count != 1 else ''} active"
        )
        self._global_view.refresh()

    # ---- build ---------------------------------------------------------

    def _build_header(self) -> None:
        header = tk.Frame(self._root, bg=C["sidebar"], height=44)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        tk.Frame(header, bg=C["border"], height=1).pack(side="bottom", fill="x")

        tk.Label(
            header,
            text="CLAUDE BRAIN",
            bg=C["sidebar"],
            fg=C["cyan"],
            font=FONTS["title"],
            anchor="w",
        ).pack(side="left", padx=(PAD_LG, 0))
        tk.Label(
            header,
            text=" — AI Dashboard",
            bg=C["sidebar"],
            fg=C["muted"],
            font=FONTS["body"],
        ).pack(side="left")

        self._clock_lbl = tk.Label(
            header,
            text="",
            bg=C["sidebar"],
            fg=C["muted"],
            font=FONTS["caption"],
        )
        self._clock_lbl.pack(side="right", padx=(0, PAD_LG))

        gear = tk.Label(
            header,
            text="⚙",
            bg=C["sidebar"],
            fg=C["muted"],
            font=FONTS["body"],
            cursor="hand2",
        )
        gear.pack(side="right", padx=(0, PAD_SM))
        gear.bind("<Button-1>", lambda _e: self._on_settings())
        gear.bind("<Enter>", lambda _e: gear.configure(fg=C["cyan"]))
        gear.bind("<Leave>", lambda _e: gear.configure(fg=C["muted"]))

        tk.Label(
            header,
            text="● ONLINE",
            bg=C["sidebar"],
            fg=C["green"],
            font=FONTS["caption"],
        ).pack(side="right", padx=(0, PAD_SM))

    def _build_body(self) -> None:
        from claude_brain.ui.global_view import GlobalView
        from claude_brain.ui.project_panel import ProjectPanel
        from claude_brain.ui.project_view import ProjectView

        self._pane = tk.PanedWindow(
            self._root,
            orient="horizontal",
            bg=C["border"],
            sashwidth=4,
            sashpad=0,
            relief="flat",
            bd=0,
        )
        self._pane.pack(fill="both", expand=True)

        self._project_panel = ProjectPanel(self._pane, self._repos, self._on_project_select)
        self._pane.add(self._project_panel, minsize=180, width=250)

        self._content_frame = tk.Frame(self._pane, bg=C["bg"])
        self._pane.add(self._content_frame, minsize=500)

        self._global_view = GlobalView(self._content_frame, self._repos, self._on_settings)
        self._project_view = ProjectView(self._content_frame, self._repos)

    def _build_status_bar(self) -> None:
        self._status_bar = StatusBar(self._root)
        self._status_bar.pack(side="bottom", fill="x")
        self._status_bar.set_left("● READY")

    def _start_clock(self) -> None:
        def _tick():
            self._clock_lbl.configure(text=datetime.now().strftime("%H:%M:%S"))
            self._root.after(1000, _tick)

        _tick()

    # ---- callbacks -----------------------------------------------------

    def _on_project_select(self, project_id: str | None) -> None:
        if project_id is None:
            self.show_global()
        else:
            self._project_view.load_project(project_id)
            self.show_project(project_id)
