"""Series-wide luminance recalculation UI."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from oled_app.processing.luminance_recalculation import recalculate_series_luminance


def start_series_luminance_recalculation(app) -> None:
    if app.series is None:
        return
    confirmed = messagebox.askyesno(
        "Пересчитать светимость",
        (
            "Все XLSX ВАЯХ, спектров и стабильности в открытой серии будут "
            "атомарно заменены версиями с текущими коэффициентами.\n\n"
            "Если raw CSV отсутствуют, приложение восстановит их из XLSX.\n\n"
            "Продолжить?"
        ),
        parent=app,
    )
    if not confirmed:
        return

    progress = tk.Toplevel(app)
    progress.title("Пересчёт светимости")
    progress.transient(app)
    progress.resizable(False, False)
    ttk.Label(
        progress,
        text="Пересчитываются файлы серии. Не закрывайте приложение.",
        padding=16,
    ).pack()
    bar = ttk.Progressbar(progress, mode="indeterminate", length=420)
    bar.pack(fill="x", padx=16, pady=(0, 16))
    bar.start(12)
    messages: list[str] = []

    def worker() -> None:
        try:
            report = recalculate_series_luminance(
                app.series,
                app.app_settings,
                log=messages.append,
            )
            error = None
        except Exception as exc:
            report = None
            error = exc
        app.after(0, lambda: finish(report, error))

    def finish(report, error) -> None:
        bar.stop()
        progress.destroy()
        for message in messages:
            app.log(message)
        if error is not None:
            messagebox.showerror(
                "Ошибка пересчёта",
                str(error),
                parent=app,
            )
            return
        app.refresh_pixel_table()
        messagebox.showinfo(
            "Пересчёт завершён",
            (
                f"XLSX обновлено: {report.workbooks_updated}\n"
                f"Raw CSV обновлено: {report.raw_files_updated}\n"
                f"Raw CSV восстановлено: {report.raw_files_restored}\n"
                f"Миниатюр ВАЯХ создано: {report.thumbnails_created}\n"
                f"Ошибок: {report.errors}"
            ),
            parent=app,
        )

    threading.Thread(target=worker, daemon=True).start()
