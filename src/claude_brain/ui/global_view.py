"""Global AI Dashboard — shown when no project is selected."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from claude_brain.config import COLORS, FONTS, PAD_LG, PAD_MD, PAD_SM
from claude_brain.ui.widgets import AgentChip

C = COLORS

_MODEL_NAME = "claude-sonnet-4-6"
_CONTEXT_WIN = "200K tokens"
_CUTOFF = "Aug 2025"

_KNOWN_AGENTS = [
    "planner",
    "architect",
    "tdd-guide",
    "code-reviewer",
    "security-reviewer",
    "build-error-resolver",
    "e2e-runner",
    "refactor-cleaner",
    "doc-updater",
]


class GlobalView(tk.Frame):
    """Full-height scrollable global AI dashboard."""

    def __init__(self, parent: tk.Widget, repos, on_settings):
        super().__init__(parent, bg=C["bg"])
        self._repos = repos
        self._on_settings = on_settings
        self._build()

    # ---- public ------------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_stats()
        self._refresh_recent()

    # ---- build -------------------------------------------------------------

    def _build(self) -> None:
        # Scrollable canvas wrapper
        canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        self._scroll_frame = tk.Frame(canvas, bg=C["bg"])
        win_id = canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")
        self._scroll_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        sf = self._scroll_frame

        # ── Section: AI Context header ──────────────────────────────────────
        tk.Label(
            sf, text="AI CONTEXT", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"], anchor="w"
        ).pack(fill="x", padx=PAD_LG, pady=(PAD_LG, PAD_SM))
        tk.Frame(sf, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        # Stat cards row
        cards = tk.Frame(sf, bg=C["bg"])
        cards.pack(fill="x", padx=PAD_LG, pady=PAD_SM)

        self._card_model = _InfoCard(cards, _MODEL_NAME, "Active Model", "cyan")
        self._card_context = _InfoCard(cards, _CONTEXT_WIN, "Context Window", "green")
        self._card_cutoff = _InfoCard(cards, _CUTOFF, "Knowledge Cutoff", "cyan")
        self._card_projects = _InfoCard(cards, "—", "Projects", "green")
        self._card_sessions = _InfoCard(cards, "—", "Sessions", "cyan")
        for card in (
            self._card_model,
            self._card_context,
            self._card_cutoff,
            self._card_projects,
            self._card_sessions,
        ):
            card.pack(side="left", padx=(0, PAD_SM), pady=PAD_SM)

        # ── Section: Agents ─────────────────────────────────────────────────
        tk.Label(
            sf, text="AVAILABLE AGENTS", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"], anchor="w"
        ).pack(fill="x", padx=PAD_LG, pady=(PAD_MD, PAD_SM))
        tk.Frame(sf, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        agent_wrap = tk.Frame(sf, bg=C["bg"])
        agent_wrap.pack(fill="x", padx=PAD_LG, pady=PAD_SM)

        for name in self._discover_agents():
            chip = AgentChip(agent_wrap, name)
            chip.pack(side="left", padx=(0, PAD_SM), pady=PAD_SM)

        # ── Section: Quick Actions ──────────────────────────────────────────
        tk.Label(
            sf, text="QUICK ACTIONS", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"], anchor="w"
        ).pack(fill="x", padx=PAD_LG, pady=(PAD_MD, PAD_SM))
        tk.Frame(sf, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        actions = tk.Frame(sf, bg=C["bg"])
        actions.pack(fill="x", padx=PAD_LG, pady=PAD_SM)
        ttk.Button(
            actions, text="↺  Resync All Sessions", style="Accent.TButton", command=self._resync_all
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            actions, text="⟳  Scan All Patterns", style="Ghost.TButton", command=self._scan_all
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            actions, text="⚙  Settings", style="Ghost.TButton", command=self._on_settings
        ).pack(side="left")

        # ── Section: Recent Activity ─────────────────────────────────────────
        tk.Label(
            sf, text="RECENT ACTIVITY", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"], anchor="w"
        ).pack(fill="x", padx=PAD_LG, pady=(PAD_MD, PAD_SM))
        tk.Frame(sf, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        tree_outer = tk.Frame(sf, bg=C["border"], padx=1, pady=1)
        tree_outer.pack(fill="both", expand=True, padx=PAD_LG, pady=PAD_SM)
        tree_inner = tk.Frame(tree_outer, bg=C["panel"])
        tree_inner.pack(fill="both", expand=True)

        cols = ("date", "project", "summary", "files", "tools")
        self._tree = ttk.Treeview(
            tree_inner, columns=cols, show="headings", selectmode="browse", height=14
        )
        self._tree.heading("date", text="Date")
        self._tree.heading("project", text="Project")
        self._tree.heading("summary", text="Summary")
        self._tree.heading("files", text="Files")
        self._tree.heading("tools", text="Tools")
        self._tree.column("date", width=100, stretch=False)
        self._tree.column("project", width=160, stretch=False)
        self._tree.column("summary", width=420)
        self._tree.column("files", width=55, stretch=False)
        self._tree.column("tools", width=55, stretch=False)

        sb = ttk.Scrollbar(tree_inner, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---- data refresh ──────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        self._card_projects.update_value(str(len(self._repos.projects.find_all())))
        self._card_sessions.update_value(str(self._repos.sessions.count()))

    def _refresh_recent(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for s in self._repos.sessions.recent(30):
            proj = self._repos.projects.find_by_id(s.project_id) if s.project_id else None
            proj_name = proj.name if proj else (s.source_dir or "—")
            first = s.summary.splitlines()[0][:80] if s.summary else "—"
            self._tree.insert(
                "", "end", values=(s.date or "—", proj_name, first, s.file_count, s.tool_count)
            )

    # ---- actions ───────────────────────────────────────────────────────────

    def _resync_all(self) -> None:
        """Delegate to the single ingest implementation in core.ingest."""
        from tkinter import messagebox

        from claude_brain.core.ingest import ingest_all, relink_unlinked

        try:
            result = ingest_all(self._repos)
            linked = relink_unlinked(self._repos)
        except Exception as exc:
            messagebox.showerror("Resync Failed", str(exc))
            return

        self.refresh()
        lines = [f"Imported {result.ingested} new session(s)."]
        if result.projects_registered:
            lines.append(f"Registered {result.projects_registered} new project(s).")
        if linked:
            lines.append(f"Linked {linked} previously unlinked session(s).")
        if result.failed:
            lines.append(f"{result.failed} transcript(s) failed - see the log.")
        messagebox.showinfo("Resync Complete", "\n".join(lines))

    def _scan_all(self) -> None:
        from tkinter import messagebox

        sessions = self._repos.sessions.find_all()
        if not sessions:
            messagebox.showinfo("Patterns", "No sessions found.")
            return
        _show_patterns_window(self, sessions, "All Projects")

    # ── agent discovery ────────────────────────────────────────────────────

    def _discover_agents(self) -> list[str]:
        agents_dir = Path.home() / ".claude" / "agents"
        if agents_dir.exists():
            names = sorted(p.stem for p in agents_dir.glob("*.md") if not p.name.startswith("_"))
            if names:
                return names
        return _KNOWN_AGENTS


# ── helpers ──────────────────────────────────────────────────────────────────


class _InfoCard(tk.Frame):
    """Compact metric card (smaller than StatCard — suited for multi-column rows)."""

    def __init__(self, parent, value: str, caption: str, color: str = "cyan"):
        super().__init__(parent, bg=C["border"], padx=1, pady=1)
        inner = tk.Frame(self, bg=C["panel"])
        inner.pack(fill="both", expand=True)
        fg = C[color] if color in C else C["cyan"]
        self._var = tk.StringVar(value=value)
        tk.Label(
            inner,
            textvariable=self._var,
            bg=C["panel"],
            fg=fg,
            font=FONTS["title"],
            padx=PAD_LG,
            pady=PAD_SM,
        ).pack()
        tk.Label(
            inner, text=caption, bg=C["panel"], fg=C["muted"], font=FONTS["caption"], padx=PAD_LG
        ).pack(pady=(0, PAD_SM))

    def update_value(self, v: str) -> None:
        self._var.set(v)


def _show_patterns_window(parent: tk.Widget, sessions: list, title: str) -> None:
    from claude_brain.core.vault_manager import scan_patterns
    from claude_brain.ui.widgets import AgentChip

    patterns = scan_patterns(sessions)
    win = tk.Toplevel(parent)
    win.title(f"Patterns — {title}")
    win.geometry("520x380")
    win.configure(bg=C["bg"])
    tk.Label(
        win, text=f"PATTERN SCAN — {title.upper()}", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"]
    ).pack(anchor="w", padx=PAD_LG, pady=(PAD_LG, PAD_SM))
    tk.Label(
        win,
        text=(
            f"{patterns['session_count']} sessions · "
            f"{patterns['date_range'][0]} → {patterns['date_range'][1]}"
        ),
        bg=C["bg"],
        fg=C["muted"],
        font=FONTS["caption"],
    ).pack(anchor="w", padx=PAD_LG, pady=(0, PAD_MD))
    tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)
    tk.Label(win, text="RECURRING KEYWORDS", bg=C["bg"], fg=C["cyan"], font=FONTS["heading"]).pack(
        anchor="w", padx=PAD_LG, pady=(PAD_SM, PAD_SM)
    )
    kw_frame = tk.Frame(win, bg=C["bg"])
    kw_frame.pack(fill="x", padx=PAD_LG, pady=(0, PAD_MD))
    keywords = patterns.get("recurring_keywords", [])
    if keywords:
        for i, kw in enumerate(keywords):
            AgentChip(kw_frame, kw).grid(row=i // 3, column=i % 3, padx=4, pady=2, sticky="w")
    else:
        tk.Label(
            kw_frame,
            text="No recurring keywords found.",
            bg=C["bg"],
            fg=C["muted"],
            font=FONTS["caption"],
        ).pack()
    ttk.Button(win, text="Close", style="Ghost.TButton", command=win.destroy).pack(pady=PAD_MD)
