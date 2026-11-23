"""Left project panel — persistent scrollable project list."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from claude_brain.config import COLORS, FONTS, PAD_MD, PAD_SM

C = COLORS

_PANEL_W = 250


class ProjectPanel(tk.Frame):
    """250px persistent left panel listing projects with selection state."""

    def __init__(self, parent: tk.Widget, repos, on_select):
        super().__init__(parent, bg=C["sidebar"], width=_PANEL_W)
        self.pack_propagate(False)
        self._repos = repos
        self._on_select = on_select  # callable(project_id | None)
        self._selected_id: str | None = None
        self._items: list[_ProjectItem] = []
        self._build()

    # ---- public ------------------------------------------------------------

    def refresh(self) -> None:
        self._apply_filter()

    def select(self, project_id: str | None) -> None:
        self._selected_id = project_id
        for item in self._items:
            item.set_selected(item.project_id == project_id)
        self._update_remove_btn()

    # ---- build -------------------------------------------------------------

    def _build(self) -> None:
        # ── Header ──────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=C["sidebar"])
        header.pack(side="top", fill="x")
        tk.Frame(header, bg=C["border"], height=1).pack(fill="x")
        inner_h = tk.Frame(header, bg=C["sidebar"])
        inner_h.pack(fill="x", padx=PAD_MD, pady=PAD_SM)
        tk.Label(
            inner_h,
            text="PROJECTS",
            bg=C["sidebar"],
            fg=C["cyan"],
            font=FONTS["heading"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # ── Search ───────────────────────────────────────────────────────────
        search_frame = tk.Frame(self, bg=C["sidebar"])
        search_frame.pack(side="top", fill="x", padx=PAD_MD, pady=(0, PAD_SM))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        search_entry = tk.Entry(
            search_frame,
            textvariable=self._search_var,
            bg=C["panel_alt"],
            fg=C["text"],
            insertbackground=C["text"],
            relief="flat",
            font=FONTS["body"],
        )
        search_entry.pack(fill="x", ipady=4)
        search_entry.insert(0, "filter projects…")
        search_entry.configure(fg=C["muted"])
        search_entry.bind("<FocusIn>", self._search_focus_in)
        search_entry.bind("<FocusOut>", self._search_focus_out)

        tk.Frame(self, bg=C["border"], height=1).pack(side="top", fill="x")

        # ── Footer — MUST be packed before the expanding canvas ──────────────
        tk.Frame(self, bg=C["border"], height=1).pack(side="bottom", fill="x")
        footer = tk.Frame(self, bg=C["sidebar"])
        footer.pack(side="bottom", fill="x", padx=PAD_MD, pady=PAD_SM)

        add_btn = tk.Label(
            footer,
            text="+  Add Project",
            bg=C["sidebar"],
            fg=C["muted"],
            font=FONTS["caption"],
            cursor="hand2",
            anchor="w",
        )
        add_btn.pack(side="left")
        add_btn.bind("<Button-1>", lambda _e: self._add_project())
        add_btn.bind("<Enter>", lambda _e: add_btn.configure(fg=C["cyan"]))
        add_btn.bind("<Leave>", lambda _e: add_btn.configure(fg=C["muted"]))

        self._remove_btn = tk.Label(
            footer,
            text="−  Remove",  # noqa: RUF001 - pairs with the + Add label
            bg=C["sidebar"],
            fg=C["border"],
            font=FONTS["caption"],
            anchor="e",
        )
        self._remove_btn.pack(side="right")

        # ── Scrollable list ─────────────────────────────────────────────────
        self._canvas = tk.Canvas(self, bg=C["sidebar"], highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._list_frame = tk.Frame(self._canvas, bg=C["sidebar"])
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw"
        )
        self._list_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._list_frame.bind("<MouseWheel>", self._on_mousewheel)

        self._populate()

    # ---- population --------------------------------------------------------

    def _populate(self) -> None:
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._items.clear()

        for proj in self._repos.projects.find_all():
            vault = self._repos.vaults.find_by_project(proj.project_id)
            has_vault = vault and vault.status == "exists"
            count = len(self._repos.sessions.find_by_project(proj.project_id))
            item = _ProjectItem(
                self._list_frame,
                project_id=proj.project_id,
                name=proj.name,
                session_count=count,
                has_vault=has_vault,
                on_click=self._item_clicked,
                on_scroll=self._on_mousewheel,
            )
            item.pack(fill="x")
            self._items.append(item)

        self._apply_filter()
        self._update_remove_btn()

    def _apply_filter(self) -> None:
        term = self._search_var.get().lower().strip()
        placeholder = "filter projects…"
        for item in self._items:
            if not term or term == placeholder or item.name.lower().find(term) != -1:
                item.pack(fill="x")
            else:
                item.pack_forget()

    def _item_clicked(self, project_id: str) -> None:
        if self._selected_id == project_id:
            self.select(None)
            self._on_select(None)
        else:
            self.select(project_id)
            self._on_select(project_id)

    def _update_remove_btn(self) -> None:
        if self._selected_id:
            self._remove_btn.configure(fg=C["muted"], cursor="hand2")
            self._remove_btn.bind("<Button-1>", lambda _e: self._remove_project())
            self._remove_btn.bind("<Enter>", lambda _e: self._remove_btn.configure(fg="#FF4444"))
            self._remove_btn.bind("<Leave>", lambda _e: self._remove_btn.configure(fg=C["muted"]))
        else:
            self._remove_btn.configure(fg=C["border"], cursor="")
            self._remove_btn.unbind("<Button-1>")
            self._remove_btn.unbind("<Enter>")
            self._remove_btn.unbind("<Leave>")

    # ---- actions -----------------------------------------------------------

    def _add_project(self) -> None:
        from pathlib import Path

        from claude_brain.core import vault_manager

        path = filedialog.askdirectory(title="Select Project Folder")
        if not path:
            return
        p = Path(path)
        self._repos.projects.upsert(p.name, str(p))
        proj = self._repos.projects.find_by_path(str(p))
        if proj and vault_manager.vault_exists(str(p)):
            self._repos.vaults.upsert(proj.project_id, str(p), "exists")
        self._populate()

    def _remove_project(self) -> None:
        if not self._selected_id:
            return
        proj = self._repos.projects.find_by_id(self._selected_id)
        name = proj.name if proj else self._selected_id
        if not messagebox.askyesno(
            "Remove Project",
            f"Remove '{name}' from Claude Brain?\n\nThis only removes it from the dashboard — no files are deleted.",
            icon="warning",
        ):
            return
        self._repos.projects.delete(self._selected_id)
        self._on_select(None)
        self._selected_id = None
        self._populate()

    # ---- canvas helpers ----------------------------------------------------

    def _on_frame_configure(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ---- search placeholder ------------------------------------------------

    def _search_focus_in(self, event) -> None:
        if event.widget.get() == "filter projects…":
            event.widget.delete(0, "end")
            event.widget.configure(fg=C["text"])

    def _search_focus_out(self, event) -> None:
        if not event.widget.get():
            event.widget.insert(0, "filter projects…")
            event.widget.configure(fg=C["muted"])


class _ProjectItem(tk.Frame):
    """Single row in the project panel list."""

    def __init__(
        self,
        parent,
        project_id: str,
        name: str,
        session_count: int,
        has_vault: bool,
        on_click,
        on_scroll=None,
    ):
        super().__init__(parent, bg=C["sidebar"], cursor="hand2")
        self.project_id = project_id
        self.name = name
        self._on_click = on_click
        self._selected = False

        self._accent = tk.Frame(self, bg=C["sidebar"], width=3)
        self._accent.pack(side="left", fill="y")

        body = tk.Frame(self, bg=C["sidebar"])
        body.pack(side="left", fill="both", expand=True, padx=(PAD_SM, PAD_MD), pady=PAD_SM)

        top_row = tk.Frame(body, bg=C["sidebar"])
        top_row.pack(fill="x")

        dot_color = C["green"] if has_vault else C["muted"]
        self._dot = tk.Label(
            top_row, text="●", bg=C["sidebar"], fg=dot_color, font=FONTS["caption"]
        )
        self._dot.pack(side="left")

        self._name_lbl = tk.Label(
            top_row, text=f"  {name}", bg=C["sidebar"], fg=C["text"], font=FONTS["bold"], anchor="w"
        )
        self._name_lbl.pack(side="left", fill="x", expand=True)

        session_text = f"{session_count} session{'s' if session_count != 1 else ''}"
        self._sub_lbl = tk.Label(
            body,
            text=session_text,
            bg=C["sidebar"],
            fg=C["muted"],
            font=FONTS["caption"],
            anchor="w",
            padx=PAD_SM,
        )
        self._sub_lbl.pack(fill="x")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", side="bottom")

        for widget in (self, body, top_row, self._name_lbl, self._sub_lbl, self._dot):
            widget.bind("<Button-1>", lambda _e, pid=project_id: on_click(pid))
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            if on_scroll:
                widget.bind("<MouseWheel>", on_scroll)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_style()

    def _update_style(self) -> None:
        bg = C["panel_alt"] if self._selected else C["sidebar"]
        accent = C["cyan"] if self._selected else C["sidebar"]
        name_fg = C["cyan"] if self._selected else C["text"]

        self._accent.configure(bg=accent)
        self._name_lbl.configure(fg=name_fg)
        for w in (
            self,
            self._dot.master,
            self._sub_lbl.master,
            self._name_lbl,
            self._sub_lbl,
            self._dot,
        ):
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def _on_enter(self, _event) -> None:
        if not self._selected:
            bg = C["panel"]
            for w in (self, self._name_lbl.master, self._name_lbl, self._sub_lbl, self._dot):
                try:
                    w.configure(bg=bg)
                except tk.TclError:
                    pass

    def _on_leave(self, _event) -> None:
        self._update_style()
