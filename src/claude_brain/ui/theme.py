"""Sentinel dark theme — ttk style configuration."""

import tkinter as tk
from tkinter import ttk

from claude_brain.config import COLORS, FONTS


def apply(root: tk.Tk) -> None:
    """Configure ttk.Style with the Sentinel palette. Call once on app start."""
    style = ttk.Style(root)
    style.theme_use("clam")

    C = COLORS
    F = FONTS

    # --- root window ---
    root.configure(bg=C["bg"])

    # --- TFrame ---
    style.configure("TFrame", background=C["bg"])
    style.configure("Sidebar.TFrame", background=C["sidebar"])
    style.configure("Panel.TFrame", background=C["panel"])
    style.configure("PanelAlt.TFrame", background=C["panel_alt"])
    style.configure("Border.TFrame", background=C["border"])

    # --- TLabel ---
    style.configure("TLabel", background=C["bg"], foreground=C["text"], font=F["body"])
    style.configure("Sidebar.TLabel", background=C["sidebar"], foreground=C["text"], font=F["body"])
    style.configure("Panel.TLabel", background=C["panel"], foreground=C["text"], font=F["body"])
    style.configure("Muted.TLabel", background=C["bg"], foreground=C["muted"], font=F["caption"])
    style.configure("Heading.TLabel", background=C["bg"], foreground=C["cyan"], font=F["heading"])
    style.configure(
        "Panel.Heading.TLabel", background=C["panel"], foreground=C["cyan"], font=F["heading"]
    )
    style.configure("Metric.TLabel", background=C["panel"], foreground=C["cyan"], font=F["metric"])
    style.configure(
        "MetricGreen.TLabel", background=C["panel"], foreground=C["green"], font=F["metric"]
    )
    style.configure(
        "Caption.TLabel", background=C["panel"], foreground=C["muted"], font=F["caption"]
    )
    style.configure("NavItem.TLabel", background=C["sidebar"], foreground=C["text"], font=F["body"])
    style.configure(
        "NavActive.TLabel", background=C["sidebar"], foreground=C["cyan"], font=F["bold"]
    )
    style.configure(
        "StatusBar.TLabel", background=C["panel_alt"], foreground=C["muted"], font=F["caption"]
    )
    style.configure(
        "StatusBarActive.TLabel",
        background=C["panel_alt"],
        foreground=C["green"],
        font=F["caption"],
    )
    style.configure(
        "AppTitle.TLabel", background=C["sidebar"], foreground=C["cyan"], font=F["title"]
    )
    style.configure(
        "AppSub.TLabel", background=C["sidebar"], foreground=C["muted"], font=F["caption"]
    )
    style.configure(
        "Tag.Cyan.TLabel", background=C["select"], foreground=C["cyan"], font=F["caption"]
    )
    style.configure(
        "Tag.Green.TLabel", background=C["panel"], foreground=C["green"], font=F["caption"]
    )
    style.configure(
        "Tag.Amber.TLabel", background=C["panel"], foreground=C["amber"], font=F["caption"]
    )

    # --- TButton ---
    style.configure(
        "TButton",
        background=C["panel_alt"],
        foreground=C["text"],
        font=F["body"],
        relief="flat",
        borderwidth=0,
        padding=(16, 6),
    )
    style.map(
        "TButton",
        background=[("active", C["border"]), ("pressed", C["select"])],
        foreground=[("active", C["text"])],
    )
    style.configure(
        "Accent.TButton",
        background=C["cyan"],
        foreground=C["bg"],
        font=F["bold"],
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Accent.TButton",
        background=[("active", "#00AACC"), ("pressed", "#008FAA")],
        foreground=[("active", C["bg"])],
    )
    style.configure(
        "Danger.TButton",
        background=C["red"],
        foreground=C["bg"],
        font=F["bold"],
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#CC3A49"), ("pressed", "#AA2F3C")],
        foreground=[("active", C["bg"])],
    )
    style.configure(
        "Ghost.TButton",
        background=C["sidebar"],
        foreground=C["muted"],
        font=F["body"],
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Ghost.TButton",
        background=[("active", C["panel"]), ("pressed", C["panel_alt"])],
        foreground=[("active", C["cyan"])],
    )

    # --- TEntry ---
    style.configure(
        "TEntry",
        fieldbackground=C["panel_alt"],
        foreground=C["text"],
        insertcolor=C["text"],
        bordercolor=C["border"],
        selectbackground=C["select"],
        font=F["body"],
        padding=(6, 4),
    )
    style.map("TEntry", bordercolor=[("focus", C["cyan"])])

    # --- TScrollbar ---
    style.configure(
        "TScrollbar",
        background=C["panel"],
        troughcolor=C["bg"],
        bordercolor=C["border"],
        arrowcolor=C["muted"],
        relief="flat",
        borderwidth=0,
    )
    style.map("TScrollbar", background=[("active", C["border"])])

    # --- TSeparator ---
    style.configure("TSeparator", background=C["border"])

    # --- Treeview ---
    style.configure(
        "Treeview",
        background=C["panel"],
        fieldbackground=C["panel"],
        foreground=C["text"],
        font=F["body"],
        rowheight=24,
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "Treeview.Heading",
        background=C["panel_alt"],
        foreground=C["muted"],
        font=F["caption"],
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Treeview", background=[("selected", C["select"])], foreground=[("selected", C["cyan"])]
    )

    # --- TCheckbutton ---
    style.configure("TCheckbutton", background=C["panel"], foreground=C["text"], font=F["body"])

    # --- TCombobox ---
    style.configure(
        "TCombobox",
        fieldbackground=C["panel_alt"],
        background=C["panel_alt"],
        foreground=C["text"],
        selectbackground=C["select"],
        font=F["body"],
    )
