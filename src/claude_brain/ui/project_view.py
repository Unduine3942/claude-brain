"""Project detail dashboard — shown when a project is selected."""

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from claude_brain.config import COLORS, FONTS, PAD_LG, PAD_MD, PAD_SM
from claude_brain.core import vault_manager, vault_writer
from claude_brain.core.claude_md_parser import parse_claude_md, parse_todo_md, write_todo_md
from claude_brain.ui.widgets import SectionPanel

C = COLORS


class ProjectView(tk.Frame):
    """Full project dashboard — loads a project and renders all its data."""

    def __init__(self, parent: tk.Widget, repos):
        super().__init__(parent, bg=C["bg"])
        self._repos = repos
        self._project_id: str | None = None
        self._todo_tasks: list[dict] = []
        self._todo_vars: list[tk.BooleanVar] = []
        self._build()

    # ---- public ------------------------------------------------------------

    def load_project(self, project_id: str) -> None:
        self._project_id = project_id
        proj = self._repos.projects.find_by_id(project_id)
        if not proj:
            return
        self._render(proj)

    # ---- build skeleton (called once) ──────────────────────────────────────

    def _build(self) -> None:
        # Scrollable outer canvas
        self._canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._canvas.bind(
            "<MouseWheel>", lambda e: self._canvas.yview_scroll(int(-e.delta / 120), "units")
        )

        self._body = tk.Frame(self._canvas, bg=C["bg"])
        self._win_id = self._canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind(
            "<Configure>", lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )
        self._canvas.bind(
            "<Configure>", lambda e: self._canvas.itemconfig(self._win_id, width=e.width)
        )

    # ---- render (called each time project changes) ─────────────────────────

    def _render(self, proj) -> None:
        for w in self._body.winfo_children():
            w.destroy()
        self._todo_tasks.clear()
        self._todo_vars.clear()

        vault = self._repos.vaults.find_by_project(proj.project_id)
        has_vault = vault and vault.status == "exists"
        sessions = self._repos.sessions.find_by_project(proj.project_id)
        # find_by_project is newest-first, so the most recent session is [0].
        last_session = sessions[0] if sessions else None

        # ── Project header ──────────────────────────────────────────────────
        self._render_header(proj, has_vault, len(sessions), last_session)

        # ── Two-column body ─────────────────────────────────────────────────
        body_row = tk.Frame(self._body, bg=C["bg"])
        body_row.pack(fill="both", expand=True, padx=PAD_LG, pady=PAD_SM)
        body_row.columnconfigure(0, weight=45, uniform="cols")
        body_row.columnconfigure(1, weight=55, uniform="cols")

        left = tk.Frame(body_row, bg=C["bg"])
        right = tk.Frame(body_row, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, PAD_SM))
        right.grid(row=0, column=1, sticky="nsew", padx=(PAD_SM, 0))

        claude_data = parse_claude_md(str(Path(proj.path) / "CLAUDE.md"))
        self._render_left(left, proj, claude_data, last_session)
        self._render_right(right, proj, sessions)

    # ── Header ──────────────────────────────────────────────────────────────

    def _render_header(self, proj, has_vault: bool, session_count: int, last_session) -> None:
        header = tk.Frame(self._body, bg=C["sidebar"])
        header.pack(fill="x")
        tk.Frame(self._body, bg=C["border"], height=1).pack(fill="x")

        inner = tk.Frame(header, bg=C["sidebar"])
        inner.pack(fill="x", padx=PAD_LG, pady=PAD_MD)

        # Title row
        title_row = tk.Frame(inner, bg=C["sidebar"])
        title_row.pack(fill="x")

        vault_dot = "●" if has_vault else "○"
        vault_col = C["green"] if has_vault else C["muted"]
        tk.Label(
            title_row, text=vault_dot, bg=C["sidebar"], fg=vault_col, font=FONTS["heading"]
        ).pack(side="left")

        tk.Label(
            title_row,
            text=f"  {proj.name.upper()}",
            bg=C["sidebar"],
            fg=C["text"],
            font=FONTS["title"],
            anchor="w",
        ).pack(side="left")

        vault_status = "vault active" if has_vault else "no vault"
        last_date = (last_session.date or "—")[:10] if last_session else "—"
        meta = f"  {vault_status}  ·  {session_count} session{'s' if session_count != 1 else ''}  ·  last: {last_date}"
        tk.Label(title_row, text=meta, bg=C["sidebar"], fg=C["muted"], font=FONTS["caption"]).pack(
            side="left", padx=(PAD_MD, 0)
        )

        # Action buttons row
        btn_row = tk.Frame(inner, bg=C["sidebar"])
        btn_row.pack(fill="x", pady=(PAD_SM, 0))

        ttk.Button(
            btn_row,
            text="Open in Obsidian",
            style="Accent.TButton",
            command=lambda: self._open_obsidian(proj),
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row,
            text="⚡ Open in Claude",
            style="Accent.TButton",
            command=lambda: self._open_in_claude(proj),
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row, text="↺ Resync", style="TButton", command=lambda: self._resync(proj)
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row,
            text="⊞ Summarize Sessions",
            style="TButton",
            command=lambda: self._summarize(proj),
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row, text="⟳ Consolidate", style="TButton", command=lambda: self._consolidate(proj)
        ).pack(side="left", padx=(0, PAD_SM))
        if not has_vault:
            ttk.Button(
                btn_row,
                text="Spin Up Vault",
                style="Ghost.TButton",
                command=lambda: self._spin_up(proj),
            ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row,
            text="Open Folder",
            style="Ghost.TButton",
            command=lambda: vault_manager.open_in_explorer(proj.path),
        ).pack(side="left")

    # ── Left column ──────────────────────────────────────────────────────────

    def _render_left(self, parent, proj, claude_data: dict, last_session) -> None:
        # Priorities
        priorities = claude_data.get("priorities", [])
        sec = SectionPanel(parent, "Current Priorities")
        sec.pack(fill="x", pady=(0, PAD_SM))
        if priorities:
            for item in priorities[:8]:
                tk.Label(
                    sec.body,
                    text=f"• {item}",
                    bg=C["panel"],
                    fg=C["text"],
                    font=FONTS["body"],
                    anchor="w",
                    wraplength=280,
                ).pack(fill="x", padx=PAD_MD, pady=1)
        else:
            tk.Label(
                sec.body,
                text="No priorities in CLAUDE.md yet.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(sec.body, text="", bg=C["panel"]).pack()  # bottom padding

        # Project context
        ctx_sec = SectionPanel(parent, "Project Context")
        ctx_sec.pack(fill="x", pady=(0, PAD_SM))
        purpose = claude_data.get("purpose", "")
        stack = claude_data.get("stack", "")
        if purpose:
            _kv_row(ctx_sec.body, "Purpose", purpose)
        if stack:
            _kv_row(ctx_sec.body, "Stack", stack)
        if not purpose and not stack:
            tk.Label(
                ctx_sec.body,
                text="Fill in CLAUDE.md to see project context here.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(ctx_sec.body, text="", bg=C["panel"]).pack()

        # Notes for next session
        notes_sec = SectionPanel(parent, "Notes for Next Session")
        notes_sec.pack(fill="x", pady=(0, PAD_SM))
        notes = last_session.notes if last_session else ""
        if notes:
            txt = tk.Text(
                notes_sec.body,
                bg=C["panel_alt"],
                fg=C["text"],
                font=FONTS["mono"],
                relief="flat",
                bd=0,
                wrap="word",
                height=5,
                state="normal",
            )
            txt.insert("1.0", notes)
            txt.configure(state="disabled")
            txt.pack(fill="x", padx=PAD_MD, pady=(0, PAD_MD))
        else:
            tk.Label(
                notes_sec.body,
                text="No notes from last session.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(notes_sec.body, text="", bg=C["panel"]).pack()

        # Vault files
        files_sec = SectionPanel(parent, "Vault Files")
        files_sec.pack(fill="x", pady=(0, PAD_SM))
        vault_root = Path(proj.path)
        md_files = sorted(vault_root.glob("*.md"))
        if md_files:
            for f in md_files[:8]:
                _file_chip(files_sec.body, f.name)
        else:
            tk.Label(
                files_sec.body,
                text="No vault files found. Spin up a vault first.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(files_sec.body, text="", bg=C["panel"]).pack()

    # ── Right column ─────────────────────────────────────────────────────────

    def _render_right(self, parent, proj, sessions: list) -> None:
        # TODO
        todo_sec = SectionPanel(parent, "To-Do")
        todo_sec.pack(fill="x", pady=(0, PAD_SM))
        todo_path = Path(proj.path) / "TODO.md"
        self._todo_tasks = parse_todo_md(str(todo_path))
        if self._todo_tasks:
            for task in self._todo_tasks:
                var = tk.BooleanVar(value=task["checked"])
                self._todo_vars.append(var)
                cb = tk.Checkbutton(
                    todo_sec.body,
                    text=task["text"],
                    variable=var,
                    bg=C["panel"],
                    fg=C["text"],
                    selectcolor=C["panel_alt"],
                    activebackground=C["panel"],
                    font=FONTS["body"],
                    anchor="w",
                    command=lambda p=str(todo_path): self._save_todo(p),
                )
                cb.pack(fill="x", padx=PAD_MD, pady=1)
        else:
            tk.Label(
                todo_sec.body,
                text="No TODO items. Add them to TODO.md.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(todo_sec.body, text="", bg=C["panel"]).pack()

        # Recent sessions
        sess_sec = SectionPanel(parent, "Sessions")
        sess_sec.pack(fill="x", pady=(0, PAD_SM))
        if sessions:
            tree = ttk.Treeview(
                sess_sec.body,
                columns=("type", "date", "summary", "files"),
                show="headings",
                height=min(len(sessions), 7),
                selectmode="browse",
            )
            tree.heading("type", text="")
            tree.heading("date", text="Date")
            tree.heading("summary", text="Summary")
            tree.heading("files", text="Files")
            tree.column("type", width=24, stretch=False, anchor="center")
            tree.column("date", width=90, stretch=False)
            tree.column("summary", width=240)
            tree.column("files", width=50, stretch=False)
            tree.tag_configure("master", foreground=C["cyan"])
            sb = ttk.Scrollbar(sess_sec.body, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="x", expand=True, padx=(PAD_MD, 0), pady=(0, PAD_MD))
            sb.pack(side="right", fill="y", pady=(0, PAD_MD), padx=(0, PAD_MD))
            for s in sessions:  # already newest-first
                first = s.summary.splitlines()[0][:60] if s.summary else "—"
                icon = "⊞" if s.is_master else "·"
                tags = ("master",) if s.is_master else ()
                tree.insert("", "end", values=(icon, s.date or "—", first, s.file_count), tags=tags)
        else:
            tk.Label(
                sess_sec.body,
                text="No sessions recorded yet. Run Resync.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(sess_sec.body, text="", bg=C["panel"]).pack()

        # Hot files
        hot_sec = SectionPanel(parent, "Most Active Files")
        hot_sec.pack(fill="x", pady=(0, PAD_SM))
        hot = self._hot_files(proj.project_id)
        if hot:
            for fp in hot[:10]:
                _file_chip(hot_sec.body, fp)
        else:
            tk.Label(
                hot_sec.body,
                text="No file activity recorded yet.",
                bg=C["panel"],
                fg=C["muted"],
                font=FONTS["caption"],
            ).pack(padx=PAD_MD, pady=PAD_SM)
        tk.Label(hot_sec.body, text="", bg=C["panel"]).pack()

    # ── actions ─────────────────────────────────────────────────────────────

    def _open_obsidian(self, proj) -> None:
        if not vault_manager.vault_exists(proj.path):
            if messagebox.askyesno("No Vault", "No vault found. Create it now?"):
                self._spin_up(proj)
            else:
                return
        ok = vault_manager.open_in_obsidian(proj.path, proj.name)
        if not ok:
            vault_manager.open_in_explorer(proj.path)

    def _open_in_claude(self, proj) -> None:
        import shutil

        proj_path = Path(proj.path)
        if not proj_path.exists():
            messagebox.showerror("Error", f"Project path does not exist:\n{proj.path}")
            return

        # Build context string from best available source
        context = self._build_project_context(proj)

        # Write context to a staging file the user can reference
        context_file = proj_path / ".claude_session_context.md"
        try:
            context_file.write_text(context, encoding="utf-8")
        except OSError:
            context_file = None

        # Copy context to clipboard for easy pasting
        try:
            self.winfo_toplevel().clipboard_clear()
            self.winfo_toplevel().clipboard_append(context)
            clipboard_ok = True
        except Exception:
            clipboard_ok = False

        # Launch Claude in the project directory in a new terminal
        launched = False
        try:
            if shutil.which("wt"):  # Windows Terminal
                subprocess.Popen(["wt", "-d", str(proj_path), "cmd", "/k", "claude"], shell=False)
                launched = True
            else:
                subprocess.Popen(f'start cmd /k "cd /d "{proj_path!s}" && claude"', shell=True)
                launched = True
        except Exception as exc:
            messagebox.showerror("Launch Error", f"Could not open terminal:\n{exc}")
            return

        msg_parts = []
        if launched:
            msg_parts.append("Claude launched in a new terminal window.")
        if clipboard_ok:
            msg_parts.append("Session context copied to clipboard — paste as your first message.")
        if context_file:
            msg_parts.append(f"Context also saved to:\n{context_file.name}")
        messagebox.showinfo("Open in Claude", "\n\n".join(msg_parts))

    def _build_project_context(self, proj) -> str:
        """Build a context string from consolidations, master session, or recent sessions."""
        import re

        from claude_brain.config import CONSOLIDATIONS_DIR

        def slugify(name: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()

        # Prefer the most recent consolidation file for this project
        slug = slugify(proj.name)
        candidates = sorted(CONSOLIDATIONS_DIR.glob(f"{slug}_*.md"), reverse=True)
        if candidates:
            try:
                return candidates[0].read_text(encoding="utf-8")
            except OSError:
                pass

        # Fall back to building from DB sessions
        sessions = self._repos.sessions.find_by_project(proj.project_id)
        if not sessions:
            return f"# {proj.name}\n\nNo session history found. This is a fresh start."

        lines = [
            f"# {proj.name} — Project Context",
            "",
            f"_Loaded from Claude Brain · {len(sessions)} session(s) recorded_",
            "",
            "---",
            "",
            "## Session History",
            "",
        ]
        for s in sorted(sessions, key=lambda x: x.date or ""):
            marker = "⊞ MASTER" if s.is_master else f"· {s.date or '?'}"
            first = s.summary.splitlines()[0][:120] if s.summary else "—"
            lines.append(f"**{marker}**: {first}")
            if s.notes:
                lines.append(f"  _Notes_: {s.notes}")
            lines.append("")

        # Carry-forward notes from most recent session. sessions is newest-first,
        # so iterate it directly - reversing it took the OLDEST notes.
        last_notes = next((s.notes for s in sessions if s.notes), None)
        if last_notes:
            lines += ["---", "", "## Notes for This Session", "", last_notes, ""]

        # Hot files
        from collections import Counter

        all_files: list[str] = []
        for s in sessions[:5]:  # newest-first: the 5 most recent
            all_files += [
                f.file_path for f in self._repos.session_files.find_by_session(s.session_id)
            ]
        hot = [fp for fp, _ in Counter(all_files).most_common(10)]
        if hot:
            lines += ["---", "", "## Most Active Files", ""]
            for fp in hot:
                lines.append(f"- `{fp}`")
            lines.append("")

        return "\n".join(lines)

    def _spin_up(self, proj) -> None:
        try:
            vault_writer.ensure_vault(proj.path, proj.name)
            self._repos.vaults.upsert(proj.project_id, proj.path, status="exists")
            self.load_project(proj.project_id)
        except Exception as exc:
            messagebox.showerror("Vault Error", str(exc))

    def _resync(self, proj) -> None:
        """Delegate to the single ingest implementation in core.ingest.

        The sweep covers every project; this view just reloads afterwards. The
        previous per-project copy re-derived project matching with a weaker
        substring rule and swallowed every error.
        """
        from tkinter import messagebox

        from claude_brain.core.ingest import ingest_all

        try:
            result = ingest_all(self._repos)
        except Exception as exc:
            messagebox.showerror("Resync Failed", str(exc))
            return

        self.load_project(proj.project_id)
        lines = [f"Imported {result.ingested} new session(s)."]
        if result.failed:
            lines.append(f"{result.failed} transcript(s) failed - see the log.")
        messagebox.showinfo("Resync", "\n".join(lines))

    def _consolidate(self, proj) -> None:
        _ConsolidateDialog(self, self._repos, proj)

    def _summarize(self, proj) -> None:
        sessions = self._repos.sessions.find_by_project(proj.project_id)
        # Include masters so multiple summarize runs collapse into one
        to_merge = [s for s in sessions if not s.is_master]
        masters = [s for s in sessions if s.is_master]
        if not to_merge and not masters:
            messagebox.showinfo("Summarize", "No sessions to summarize yet.")
            return
        all_sessions = to_merge + masters
        master_note = f" ({len(masters)} existing master(s) will also be merged)" if masters else ""
        if not messagebox.askyesno(
            "Summarize Sessions",
            f"Merge {len(all_sessions)} session(s) into one master session?{master_note}\n\n"
            "The originals will be deleted. The master summary is compact\n"
            "to conserve tokens when used as context. Continue?",
            icon="question",
        ):
            return
        try:
            from claude_brain.core.summarizer import build_master_session

            files_by_session = {
                s.session_id: self._repos.session_files.find_by_session(s.session_id)
                for s in all_sessions
            }
            master = build_master_session(proj.project_id, all_sessions, files_by_session)
            self._repos.sessions.create(master)
            self._repos.sessions.delete_by_ids([s.session_id for s in all_sessions])
            self.load_project(proj.project_id)
            messagebox.showinfo(
                "Done",
                f"Created master session from {len(all_sessions)} session(s).\n"
                "New sessions added after this point will appear alongside it.",
            )
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def _save_todo(self, todo_path: str) -> None:
        for i, var in enumerate(self._todo_vars):
            if i < len(self._todo_tasks):
                self._todo_tasks[i] = {**self._todo_tasks[i], "checked": var.get()}
        write_todo_md(todo_path, self._todo_tasks)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _hot_files(self, project_id: str) -> list[str]:
        from collections import Counter

        sessions = self._repos.sessions.find_by_project(project_id)
        all_files: list[str] = []
        for s in sessions[:5]:  # newest-first: the 5 most recent
            all_files += [
                f.file_path for f in self._repos.session_files.find_by_session(s.session_id)
            ]
        return [fp for fp, _ in Counter(all_files).most_common(10)]


# ── Consolidate inline dialog ─────────────────────────────────────────────────


class _ConsolidateDialog(tk.Toplevel):
    def __init__(self, parent, repos, proj):
        super().__init__(parent)
        self._repos = repos
        self._proj = proj
        self.title(f"Consolidate — {proj.name}")
        self.geometry("560x420")
        self.configure(bg=C["bg"])
        self.transient(parent)
        self.grab_set()
        self._session_ids: list[str] = []
        self._build()

    def _build(self) -> None:
        tk.Label(
            self,
            text="SELECT SESSIONS TO CONSOLIDATE",
            bg=C["bg"],
            fg=C["cyan"],
            font=FONTS["heading"],
        ).pack(anchor="w", padx=PAD_LG, pady=(PAD_LG, PAD_SM))
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=PAD_LG)

        list_outer = tk.Frame(self, bg=C["border"], padx=1, pady=1)
        list_outer.pack(fill="both", expand=True, padx=PAD_LG, pady=PAD_SM)
        list_inner = tk.Frame(list_outer, bg=C["panel"])
        list_inner.pack(fill="both", expand=True)

        self._listbox = tk.Listbox(
            list_inner,
            selectmode="multiple",
            bg=C["panel_alt"],
            fg=C["text"],
            selectbackground=C["select"],
            selectforeground=C["cyan"],
            font=FONTS["mono"],
            relief="flat",
            bd=0,
            activestyle="dotbox",
        )
        sb = ttk.Scrollbar(list_inner, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        self._listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        sessions = self._repos.sessions.find_by_project(self._proj.project_id)
        for s in sessions:
            icon = "⊞" if s.is_master else "·"
            first = s.summary.splitlines()[0][:50] if s.summary else "—"
            display = f"{icon} {s.date or '?'}  |  {s.file_count} files  |  {first}"
            self._listbox.insert("end", display)
            self._session_ids.append(s.session_id)
        # Pre-select everything so masters are included by default
        self._listbox.select_set(0, "end")

        btn_row = ttk.Frame(self, style="TFrame")
        btn_row.pack(fill="x", padx=PAD_LG, pady=PAD_SM)
        ttk.Button(
            btn_row,
            text="Select All",
            style="Ghost.TButton",
            command=lambda: self._listbox.select_set(0, "end"),
        ).pack(side="left", padx=(0, PAD_SM))
        ttk.Button(
            btn_row,
            text="Deselect All",
            style="Ghost.TButton",
            command=lambda: self._listbox.select_clear(0, "end"),
        ).pack(side="left", padx=(0, PAD_LG))
        ttk.Button(btn_row, text="Generate", style="Accent.TButton", command=self._generate).pack(
            side="left"
        )
        ttk.Button(btn_row, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(
            side="left", padx=(PAD_SM, 0)
        )

    def _generate(self) -> None:
        import subprocess

        from claude_brain.core.consolidator import consolidate

        indices = self._listbox.curselection()
        if not indices:
            messagebox.showinfo("Consolidate", "Select at least one session.")
            return
        sid_list = [self._session_ids[i] for i in indices]
        sessions = [self._repos.sessions.find_by_id(sid) for sid in sid_list]
        sessions = [s for s in sessions if s]
        files_by_session = {
            s.session_id: self._repos.session_files.find_by_session(s.session_id) for s in sessions
        }
        try:
            result = consolidate(
                project_name=self._proj.name,
                project_id=self._proj.project_id,
                sessions=sessions,
                files_by_session=files_by_session,
            )
            self._repos.consolidations.create(result)
            if vault_manager.vault_exists(self._proj.path):
                import shutil

                sessions_dir = Path(self._proj.path) / "Sessions"
                sessions_dir.mkdir(exist_ok=True)
                shutil.copy2(result.markdown_path, sessions_dir / Path(result.markdown_path).name)
                # The index is an Obsidian Base now, reading frontmatter directly.
                vault_writer.write_sessions_base(self._proj.path)
            self.destroy()
            if messagebox.askyesno("Done", f"Saved to:\n{result.markdown_path}\n\nOpen the file?"):
                subprocess.Popen(
                    ["cmd", "/c", "start", "", result.markdown_path],
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0,
                )
        except Exception as exc:
            messagebox.showerror("Error", str(exc))


# ── small helpers ─────────────────────────────────────────────────────────────


def _kv_row(parent: tk.Widget, label: str, value: str) -> None:
    row = tk.Frame(parent, bg=C["panel"])
    row.pack(fill="x", padx=PAD_MD, pady=1)
    tk.Label(
        row,
        text=f"{label}:",
        bg=C["panel"],
        fg=C["muted"],
        font=FONTS["caption"],
        width=10,
        anchor="w",
    ).pack(side="left")
    tk.Label(
        row, text=value, bg=C["panel"], fg=C["text"], font=FONTS["body"], anchor="w", wraplength=260
    ).pack(side="left", fill="x", expand=True)


def _file_chip(parent: tk.Widget, name: str) -> None:
    tk.Label(
        parent,
        text=name,
        bg=C["panel_deep"],
        fg=C["muted"],
        font=FONTS["mono"],
        padx=PAD_SM,
        pady=1,
        relief="flat",
    ).pack(anchor="w", padx=PAD_MD, pady=1)
