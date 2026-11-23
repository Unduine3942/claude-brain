"""Reusable Sentinel-themed Tkinter widgets."""

import tkinter as tk
from tkinter import ttk

from claude_brain.config import COLORS, FONTS, PAD_LG, PAD_MD, PAD_SM

C = COLORS


# ---- layout helpers ----------------------------------------------------


def section_header(parent: tk.Widget, text: str) -> ttk.Label:
    lbl = ttk.Label(parent, text=text.upper(), style="Heading.TLabel")
    lbl.pack(anchor="w", padx=PAD_LG, pady=(PAD_LG, PAD_SM))
    return lbl


def separator(parent: tk.Widget) -> ttk.Separator:
    sep = ttk.Separator(parent, orient="horizontal")
    sep.pack(fill="x", padx=PAD_LG, pady=PAD_SM)
    return sep


def panel(parent: tk.Widget, title: str = "", padx: int = PAD_LG, pady: int = PAD_SM) -> ttk.Frame:
    """Bordered dark panel frame, optionally with a title label."""
    outer = tk.Frame(parent, bg=C["border"], padx=1, pady=1)
    outer.pack(fill="x", padx=padx, pady=pady)
    inner = ttk.Frame(outer, style="Panel.TFrame")
    inner.pack(fill="both", expand=True)
    if title:
        ttk.Label(inner, text=title.upper(), style="Panel.Heading.TLabel").pack(
            anchor="w", padx=PAD_MD, pady=(PAD_MD, PAD_SM)
        )
    return inner


def scrolled_text(parent: tk.Widget, height: int = 8, **kw) -> tk.Text:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill="both", expand=True, padx=PAD_LG, pady=PAD_SM)
    sb = ttk.Scrollbar(frame, orient="vertical")
    txt = tk.Text(
        frame,
        height=height,
        bg=C["panel_alt"],
        fg=C["text"],
        insertbackground=C["text"],
        selectbackground=C["select"],
        font=FONTS["mono"],
        relief="flat",
        bd=0,
        wrap="word",
        yscrollcommand=sb.set,
        **kw,
    )
    sb.configure(command=txt.yview)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return txt


# ---- compound widgets --------------------------------------------------


class StatCard(tk.Frame):
    """Large metric value + caption with icon and flash animation on update."""

    def __init__(self, parent, value: str, caption: str, color: str = "cyan", icon: str = "", **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._color_key = color
        self._base_color = C.get(color, C["cyan"])
        self._icon_lbl: tk.Label | None = None

        if icon:
            self._icon_lbl = tk.Label(
                self,
                text=icon,
                bg=C["panel"],
                fg=self._base_color,
                font=("Segoe UI", 16),
            )
            self._icon_lbl.pack(anchor="center", pady=(PAD_LG, 0))

        self._val_var = tk.StringVar(value=value)
        self._val_lbl = tk.Label(
            self,
            textvariable=self._val_var,
            bg=C["panel"],
            fg=self._base_color,
            font=FONTS["metric"],
        )
        self._val_lbl.pack(anchor="center", padx=PAD_LG, pady=(PAD_SM if icon else PAD_LG, 0))

        tk.Label(
            self,
            text=caption.upper(),
            bg=C["panel"],
            fg=C["muted"],
            font=FONTS["caption"],
        ).pack(anchor="center", padx=PAD_LG, pady=(0, PAD_LG))

    def update_value(self, value: str) -> None:
        if self._val_var.get() != value:
            self._val_var.set(value)
            self._flash()

    def _flash(self) -> None:
        target = self._icon_lbl or self._val_lbl
        flash_color = C["amber"] if self._color_key != "amber" else "#FFFFFF"
        target.configure(fg=flash_color)
        try:
            self.after(
                380,
                lambda: target.configure(fg=self._base_color) if target.winfo_exists() else None,
            )
        except Exception:
            pass


class LabeledEntry(ttk.Frame):
    """Label + Entry pair."""

    def __init__(self, parent, label: str, width: int = 40, **kw):
        super().__init__(parent, style="TFrame", **kw)
        ttk.Label(self, text=label, style="TLabel", width=22).pack(side="left")
        self._var = tk.StringVar()
        self._entry = ttk.Entry(self, textvariable=self._var, width=width)
        self._entry.pack(side="left", fill="x", expand=True)

    @property
    def value(self) -> str:
        return self._var.get()

    @value.setter
    def value(self, v: str) -> None:
        self._var.set(v)

    def get(self) -> str:
        return self._var.get()

    def set(self, v: str) -> None:
        self._var.set(v)


class StatusBar(ttk.Frame):
    """Three-column bottom status bar."""

    def __init__(self, parent, **kw):
        super().__init__(parent, style="PanelAlt.TFrame", height=24, **kw)
        self.pack_propagate(False)
        self._left = tk.StringVar(value="● READY")
        self._center = tk.StringVar(value="")
        self._right = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._left, style="StatusBarActive.TLabel").pack(
            side="left", padx=PAD_MD
        )
        ttk.Label(self, textvariable=self._right, style="StatusBar.TLabel").pack(
            side="right", padx=PAD_MD
        )
        ttk.Label(self, textvariable=self._center, style="StatusBar.TLabel").pack(
            side="left", expand=True
        )

    def set_left(self, text: str, active: bool = True) -> None:
        self._left.set(text)

    def set_center(self, text: str) -> None:
        self._center.set(text)


class AgentChip(tk.Label):
    """Pill-shaped label for displaying an agent name."""

    def __init__(self, parent, name: str, **kw):
        super().__init__(
            parent,
            text=name,
            bg=C["select"],
            fg=C["cyan"],
            font=FONTS["caption"],
            padx=PAD_SM + 2,
            pady=2,
            relief="flat",
            **kw,
        )


class SectionPanel(tk.Frame):
    """Bordered panel with a heading label and inner content frame."""

    def __init__(self, parent, title: str, bg=None, **kw):
        bg = bg or C["panel"]
        super().__init__(parent, bg=C["border"], padx=1, pady=1, **kw)
        self._inner = tk.Frame(self, bg=bg)
        self._inner.pack(fill="both", expand=True)
        tk.Label(
            self._inner,
            text=title.upper(),
            bg=bg,
            fg=C["cyan"],
            font=FONTS["heading"],
            anchor="w",
        ).pack(fill="x", padx=PAD_MD, pady=(PAD_MD, PAD_SM))

    @property
    def body(self) -> tk.Frame:
        return self._inner
