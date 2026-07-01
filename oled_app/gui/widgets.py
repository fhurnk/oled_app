"""Reusable Tk widgets and layout helpers for the modular GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable, Tuple


def fit_toplevel_to_content(win: tk.Toplevel, min_width: int, min_height: int, padding: int = 36) -> None:
    try:
        win.update_idletasks()
        screen_w = int(win.winfo_screenwidth())
        screen_h = int(win.winfo_screenheight())
        req_w = int(win.winfo_reqwidth()) + padding
        req_h = int(win.winfo_reqheight()) + padding
        width = min(max(min_width, req_w), max(320, screen_w - 80))
        height = min(max(min_height, req_h), max(260, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(min(width, min_width), min(height, min_height))
    except Exception:
        pass


def create_tree_with_scrollbars(
    parent,
    columns: Iterable[str],
    height: int = 12,
    selectmode: str = "browse",
) -> Tuple[ttk.Frame, ttk.Treeview]:
    container = ttk.Frame(parent)
    tree = ttk.Treeview(container, columns=tuple(columns), show="headings", height=height, selectmode=selectmode)
    yscroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    xscroll = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll.grid(row=1, column=0, sticky="ew")
    container.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)
    return container, tree


def create_scrollable_frame(parent, padding: int = 16) -> Tuple[ttk.Frame, ttk.Frame]:
    outer = ttk.Frame(parent)
    canvas = tk.Canvas(outer, highlightthickness=0)
    yscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    xscroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll.grid(row=1, column=0, sticky="ew")
    outer.rowconfigure(0, weight=1)
    outer.columnconfigure(0, weight=1)
    frame = ttk.Frame(canvas, padding=padding)
    window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

    def configure_frame(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def configure_canvas(event) -> None:
        canvas.itemconfigure(window_id, width=max(event.width, frame.winfo_reqwidth()))

    def on_mousewheel(event) -> None:
        delta = -1 * int(event.delta / 120) if event.delta else 0
        if delta:
            canvas.yview_scroll(delta, "units")

    frame.bind("<Configure>", configure_frame)
    canvas.bind("<Configure>", configure_canvas)
    canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", on_mousewheel))
    canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))
    return outer, frame
