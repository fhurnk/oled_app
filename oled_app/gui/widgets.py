"""Reusable Tk widgets and layout helpers for the modular GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable, Tuple


def mousewheel_units(event) -> int:
    if getattr(event, "num", None) == 4:
        return -1
    if getattr(event, "num", None) == 5:
        return 1
    delta = getattr(event, "delta", 0)
    return -1 * int(delta / 120) if delta else 0


def bind_mousewheel_to_yview(widget, target, bind_all_on_enter: bool = False) -> None:
    def unbind_global_mousewheel() -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                widget.unbind_all(sequence)
            except tk.TclError:
                pass

    def target_exists() -> bool:
        try:
            return bool(target.winfo_exists())
        except tk.TclError:
            return False

    def on_mousewheel(event):
        if not target_exists():
            if bind_all_on_enter:
                unbind_global_mousewheel()
            return "break"
        units = mousewheel_units(event)
        if units:
            try:
                target.yview_scroll(units, "units")
            except tk.TclError:
                if bind_all_on_enter:
                    unbind_global_mousewheel()
            return "break"
        return None

    if bind_all_on_enter:
        widget.bind("<Enter>", lambda _event: widget.bind_all("<MouseWheel>", on_mousewheel))
        widget.bind("<Leave>", lambda _event: unbind_global_mousewheel())
        widget.bind("<Enter>", lambda _event: widget.bind_all("<Button-4>", on_mousewheel), add="+")
        widget.bind("<Enter>", lambda _event: widget.bind_all("<Button-5>", on_mousewheel), add="+")
        widget.bind("<Destroy>", lambda _event: unbind_global_mousewheel(), add="+")
        return

    widget.bind("<MouseWheel>", on_mousewheel)
    widget.bind("<Button-4>", on_mousewheel)
    widget.bind("<Button-5>", on_mousewheel)


def fit_toplevel_to_content(win: tk.Toplevel, min_width: int, min_height: int, padding: int = 36) -> None:
    try:
        win.update_idletasks()
        screen_w = int(win.winfo_screenwidth())
        screen_h = int(win.winfo_screenheight())
        req_w = int(win.winfo_reqwidth()) + padding
        req_h = int(win.winfo_reqheight()) + padding
        max_w = max(420, screen_w - 80)
        max_h = max(320, screen_h - 100)
        width = min(max(min_width, req_w), max_w)
        height = min(max(min_height, req_h), max_h)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(min(width, max(min_width, min(req_w, max_w))), min(height, max(min_height, min(req_h, max_h))))
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
    bind_mousewheel_to_yview(tree, tree)
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

    frame.bind("<Configure>", configure_frame)
    canvas.bind("<Configure>", configure_canvas)
    bind_mousewheel_to_yview(canvas, canvas, bind_all_on_enter=True)
    return outer, frame
