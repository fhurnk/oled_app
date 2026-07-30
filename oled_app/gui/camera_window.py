"""Free and series-bound camera workflows."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, Optional

try:
    from PIL import Image, ImageTk
except ImportError:  # Keep the rest of the OLED application launchable before dependencies are updated.
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

from oled_app.camera import (
    CameraClient,
    CameraClientError,
    RemoteFile,
    WifiConnectionSession,
    WindowsWifiController,
    build_camera_service_url,
    normalize_center_crop,
    safe_capture_stem,
)
from oled_app.camera.telemetry_video import create_stability_telemetry_video
from oled_app.constants import APP_VERSION, SCRIPT_DIR
from oled_app.series import ensure_camera_session_folder
from oled_app.settings import DEFAULT_APP_SETTINGS, save_app_settings
from oled_app.utils import safe_filename, timestamp_for_file

from .ivl_window import open_ivl_window
from .stability_window import open_stability_window
from .widgets import create_scrollable_frame, fit_window_to_screen


SERIES_CAMERA_STATIONS = {
    "ivl": {"label": "ВАЯХ", "measurement_type": "IVL", "journal_type": "CAMERA_IVL"},
    "stability": {
        "label": "Стабильность",
        "measurement_type": "STABILITY",
        "journal_type": "CAMERA_STABILITY",
    },
}


def camera_error_dialog_text(exc: Exception) -> str:
    message = str(exc)
    details = str(exc.details or "").strip() if isinstance(exc, CameraClientError) else ""
    return f"{message}\n\n{details}" if details and details not in message else message


def free_camera_date_folder(download_root: Path | str, capture_date: Optional[date] = None) -> Path:
    """Keep free-camera downloads grouped by their local capture date."""

    return Path(download_root).expanduser() / (capture_date or date.today()).isoformat()

STABILITY_CURRENT_LIMIT_POSTROLL_S = 5.0
PHOTO_PREVIEW_READ_ATTEMPTS = 4


def camera_station_key(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized in SERIES_CAMERA_STATIONS:
        return normalized
    for key, station in SERIES_CAMERA_STATIONS.items():
        if station["label"] == normalized:
            return key
    return "ivl"


def first_available_video_control(controls: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the first camera video control that has both a path and selectable choices."""

    for control in controls:
        if str(control.get("path") or "") and [str(value) for value in control.get("choices") or []]:
            return control
    return {}


def connect_camera_service_with_wifi(
    client: CameraClient,
    camera_settings: Dict[str, Any],
    *,
    existing_session: Optional[WifiConnectionSession] = None,
    wifi_controller: Optional[WindowsWifiController] = None,
    progress: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Optional[WifiConnectionSession]:
    """Reach the camera directly or switch to its saved Windows Wi-Fi profile."""

    direct_error: Optional[Exception] = None
    try:
        client.health()
        return existing_session
    except Exception as exc:
        direct_error = exc
        if not bool(camera_settings.get("auto_connect_wifi", False)):
            raise

    profile = str(camera_settings.get("wifi_profile") or "").strip()
    interface_name = str(camera_settings.get("wifi_interface") or "").strip()
    timeout_s = float(camera_settings.get("wifi_connect_timeout_s", 25.0))
    controller = wifi_controller or WindowsWifiController()
    if progress is not None:
        progress(f"Подключение Windows к Wi-Fi-профилю «{profile}»…")
    switched_session = controller.connect_saved_profile(
        profile,
        interface_name=interface_name,
        timeout_s=timeout_s,
    )
    session = existing_session or switched_session
    if existing_session is not None and not existing_session.previous_profile:
        session = switched_session

    if progress is not None:
        progress("Wi-Fi подключён. Ожидание сервиса Raspberry Pi…")
    deadline = monotonic() + max(timeout_s, 1.0)
    last_error: Exception = direct_error
    while monotonic() < deadline:
        try:
            client.health()
            return session
        except Exception as exc:
            last_error = exc
            sleep(0.5)

    restore_error = ""
    try:
        controller.restore(session, timeout_s=timeout_s)
    except Exception as exc:
        restore_error = f"\nНе удалось вернуть прежнюю Wi-Fi-сеть: {exc}"
    raise CameraClientError(
        "Wi-Fi Raspberry Pi подключён, но сервис камеры не ответил.",
        "CAMERA_WIFI_SERVICE_UNAVAILABLE",
        f"{last_error}{restore_error}",
    ) from last_error


def stability_current_limit_reached(result: Optional[Dict[str, Any]]) -> bool:
    """Return whether stability stopped because the measured current crossed its limit."""

    return any(
        str(event.get("event") or "") == "current_limit_or_breakdown"
        for event in (result or {}).get("events", [])
    )


def stability_postroll_remaining_s(
    result: Optional[Dict[str, Any]],
    measurement_session: Optional[Dict[str, Any]],
    now_monotonic: Optional[float] = None,
) -> float:
    """Return camera post-roll still needed five seconds after the current-limit event."""

    event = next(
        (
            item
            for item in (result or {}).get("events", [])
            if str(item.get("event") or "") == "current_limit_or_breakdown"
        ),
        None,
    )
    if event is None:
        return 0.0
    if not measurement_session or measurement_session.get("started_monotonic") is None:
        return STABILITY_CURRENT_LIMIT_POSTROLL_S
    event_monotonic = float(measurement_session["started_monotonic"]) + float(
        event.get("measurement_time_s") or 0.0
    )
    elapsed_after_event = (time.monotonic() if now_monotonic is None else now_monotonic) - event_monotonic
    return max(0.0, STABILITY_CURRENT_LIMIT_POSTROLL_S - elapsed_after_event)


def read_local_file_snapshot(path: Path | str) -> bytes:
    """Read a newly downloaded file, tolerating brief OneDrive filter races."""

    local_path = Path(path).expanduser()
    for attempt in range(PHOTO_PREVIEW_READ_ATTEMPTS):
        try:
            return local_path.read_bytes()
        except OSError as exc:
            retryable = exc.errno in {13, 22} or getattr(exc, "winerror", None) in {32, 33}
            if not retryable or attempt + 1 >= PHOTO_PREVIEW_READ_ATTEMPTS:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise RuntimeError("Не удалось прочитать сохранённую фотографию.")


def load_local_photo_preview(path: Path | str, max_size: tuple[int, int]):
    """Load and resize a downloaded photo for Tk previews without cropping it."""

    if Image is None:
        raise RuntimeError("Для показа фотографии требуется Pillow.")
    width = max(1, min(int(max_size[0]), 8192))
    height = max(1, min(int(max_size[1]), 8192))
    # OneDrive may reject Pillow's deferred reads from a newly replaced file on
    # Windows with OSError(22).  Take an independent in-memory snapshot first,
    # then decode and resize without keeping the synchronized file open.
    encoded = read_local_file_snapshot(path)
    with Image.open(io.BytesIO(encoded)) as source:
        source.thumbnail((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)
        return source.convert("RGB").copy()


def ask_workflow_continue(
    parent,
    title: str,
    message: str,
    image_path: Optional[Path | str] = None,
) -> bool:
    """Show the guided camera workflow gate with explicit Next and Cancel actions."""

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    result = {"continue": False}
    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill="both", expand=True)

    if image_path is not None:
        try:
            image = load_local_photo_preview(image_path, (840, 500))
            preview = ImageTk.PhotoImage(image=image, master=dialog)
            dialog._captured_photo_preview = preview
            ttk.Label(frame, image=preview, anchor="center").pack(fill="both", expand=True, pady=(0, 14))
        except Exception as exc:
            ttk.Label(
                frame,
                text=f"Фото сохранено, но предпросмотр не загрузился: {exc}",
                foreground="#B00020",
                wraplength=780,
                justify="left",
            ).pack(anchor="w", pady=(0, 14))
    ttk.Label(frame, text=message, wraplength=460, justify="left").pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(16, 0))

    def close(should_continue: bool) -> None:
        result["continue"] = should_continue
        dialog.destroy()

    ttk.Button(buttons, text="Отмена", command=lambda: close(False)).pack(side="right")
    ttk.Button(buttons, text="Далее", command=lambda: close(True)).pack(side="right", padx=(0, 8))
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    dialog.grab_set()
    requested_width = 900 if image_path is not None else 520
    requested_height = 720 if image_path is not None else 190
    fit_window_to_screen(
        dialog,
        requested_width,
        requested_height,
        420,
        150,
        horizontal_margin=40,
        vertical_margin=70,
    )
    parent.wait_window(dialog)
    return bool(result["continue"])


def build_series_capture_stem(
    pixel_id: str,
    station: str,
    kind: str,
    suffix: str = "",
    timestamp: Optional[str] = None,
) -> str:
    """Build a predictable series-camera filename starting with the pixel ID."""

    station_key = camera_station_key(station)
    parts = [safe_filename(pixel_id, fallback="pixel"), station_key, safe_filename(kind, fallback="capture")]
    safe_suffix = safe_capture_stem(suffix)
    if safe_suffix:
        parts.append(safe_suffix)
    parts.append(timestamp or timestamp_for_file())
    return safe_capture_stem("_".join(parts))


def center_crop_dimensions(width: int, height: int, crop: Optional[Dict[str, Any]] = None) -> tuple[int, int]:
    """Return even centered output dimensions matching the Raspberry Pi FFmpeg filter."""

    normalized = normalize_center_crop(crop)
    crop_width = int(width)
    crop_height = int(height)
    if normalized["width_percent"] < 100.0:
        crop_width = max(2, int(crop_width * normalized["width_percent"] / 100.0))
        crop_width -= crop_width % 2
    if normalized["height_percent"] < 100.0:
        crop_height = max(2, int(crop_height * normalized["height_percent"] / 100.0))
        crop_height -= crop_height % 2
    return min(crop_width, int(width)), min(crop_height, int(height))


def decode_liveview_frame(
    frame: bytes,
    max_size: tuple[int, int],
    crop: Optional[Dict[str, Any]] = None,
):
    """Fully decode and resize one JPEG before handing it to Tk."""

    if Image is None:
        raise RuntimeError("Для LiveView требуется Pillow.")
    width = max(1, min(int(max_size[0]), 8192))
    height = max(1, min(int(max_size[1]), 8192))
    with io.BytesIO(frame) as stream:
        with Image.open(stream) as source:
            source.load()
            image = source.convert("RGB")
    crop_width, crop_height = center_crop_dimensions(image.width, image.height, crop)
    if (crop_width, crop_height) != image.size:
        left = (image.width - crop_width) // 2
        top = (image.height - crop_height) // 2
        image = image.crop((left, top, left + crop_width, top + crop_height))
    scale = min(width / image.width, height / image.height)
    target_width = max(1, min(width, int(image.width * scale + 0.5)))
    target_height = max(1, min(height, int(image.height * scale + 0.5)))
    if (target_width, target_height) == image.size:
        return image
    try:
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    except OSError:
        image = image.resize((target_width, target_height), Image.Resampling.BILINEAR)
    return image


class CameraTestWindow(tk.Toplevel):
    """Camera UI in either free mode or a series/pixel-bound mode."""

    def __init__(self, app, context: str = "free"):
        super().__init__(app)
        self.app = app
        self.camera_context = "series" if context == "series" else "free"
        self.series_bound = self.camera_context == "series"
        self.title(
            f"Камера серии — v{APP_VERSION}" if self.series_bound else f"Свободная камера — v{APP_VERSION}"
        )
        fit_window_to_screen(self, 1280, 720, 800, 520, horizontal_margin=40, vertical_margin=70)
        self.transient(app)

        settings = app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        self.host_var = tk.StringVar(value=str(settings.get("host", "192.168.4.1")))
        self.port_var = tk.StringVar(value=str(settings.get("port", 8765)))
        self.state_var = tk.StringVar(value="Не подключено")
        self.model_var = tk.StringVar(value="—")
        self.fps_var = tk.StringVar(value="0.0 кадр/с")
        self.clients_var = tk.StringVar(value="0")
        self.viewfinder_var = tk.StringVar(value="контроль недоступен")
        self.disk_var = tk.StringVar(value="—")
        self.file_var = tk.StringVar(value="—")
        self.error_var = tk.StringVar(value="—")
        self.activity_var = tk.StringVar(value="Готово к подключению")
        self.video_source_var = tk.StringVar(value="LiveView: ожидание первого кадра")
        self.video_quality_var = tk.StringVar(value="Камера не подключена")
        self.video_fps_var = tk.StringVar(value="Камера не подключена")
        self.wifi_status_var = tk.StringVar(value=self._initial_wifi_status(settings))
        self.snapshot_name_var = tk.StringVar()
        self.station_var = tk.StringVar(value=SERIES_CAMERA_STATIONS["ivl"]["label"])
        series_pixels = app.pixel_ids() if self.series_bound and app.series is not None else []
        self.pixel_var = tk.StringVar(value=series_pixels[0] if series_pixels else "")
        self.crop_width_var = tk.StringVar(value=str(settings.get("crop_width_percent", 100.0)))
        self.crop_height_var = tk.StringVar(value=str(settings.get("crop_height_percent", 100.0)))
        try:
            self._active_crop = normalize_center_crop(
                {
                    "width_percent": self.crop_width_var.get(),
                    "height_percent": self.crop_height_var.get(),
                }
            )
        except ValueError:
            self._active_crop = normalize_center_crop()
            self.crop_width_var.set("100")
            self.crop_height_var.set("100")
        self.keep_remote_var = tk.BooleanVar(value=bool(settings.get("keep_remote_files_after_download", True)))
        self.combine_stability_telemetry_var = tk.BooleanVar(
            value=bool(settings.get("combine_stability_telemetry_video", True))
        )

        self.client: Optional[CameraClient] = None
        self.status: Dict[str, Any] = {}
        self.capability_data: Dict[str, Any] = {}
        self.remote_files: Dict[str, RemoteFile] = {}
        self.photo_quality_vars: Dict[str, tk.StringVar] = {}
        self.photo_exposure_vars: Dict[str, tk.StringVar] = {}
        self.video_quality_control_path = ""
        self.video_fps_control_path = ""
        self.video_quality_by_label: Dict[str, str] = {}
        self.video_fps_by_label: Dict[str, str] = {}
        self._busy = False
        self._closed = False
        self._status_request_running = False
        self._stream_stop = threading.Event()
        self._stream_thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self._canvas_image = None
        self._last_frame: Optional[bytes] = None
        self._photo_preview_active = False
        self._last_render_error = ""
        self._render_error_count = 0
        self._poll_after_id = None
        self._frame_after_id = None
        self._recording_capture_stem = ""
        self._recording_started_monotonic: Optional[float] = None
        self._recording_stop_requested_monotonic: Optional[float] = None
        self._recording_context: Dict[str, Any] = {}
        self._guided_measurement_active = False
        self._guided_create_telemetry = False
        self._wifi_session: Optional[WifiConnectionSession] = None
        self._series_capture_dir: Optional[Path] = None
        self._series_capture_context: Optional[tuple[str, str, str]] = None
        self.station_combo = None
        self.pixel_combo = None
        self.measurement_launch_button = None
        self.combine_stability_telemetry_checkbutton = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._frame_after_id = self.after(50, self._consume_frames)
        self._update_buttons()
        if bool(settings.get("auto_connect_wifi", False)):
            self.after(300, self.connect)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Камера серии" if self.series_bound else "Свободная камера Canon",
            font=("Segoe UI", 17, "bold"),
        ).pack(side="left")
        ttk.Label(
            header,
            text="Привязка к станции и пикселю" if self.series_bound else "Свободная съёмка и файлы",
            foreground="#0969DA" if self.series_bound else "#57606A",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(12, 0))

        connection = ttk.LabelFrame(outer, text="Сервис на Raspberry Pi", padding=8)
        connection.pack(fill="x", pady=(10, 10))
        ttk.Label(connection, text="IP / имя:").grid(row=0, column=0, sticky="w")
        ttk.Entry(connection, textvariable=self.host_var, width=28).grid(row=0, column=1, padx=(6, 12), sticky="we")
        ttk.Label(connection, text="Порт:").grid(row=0, column=2, sticky="w")
        ttk.Entry(connection, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=(6, 0), sticky="w")
        self.connect_button = ttk.Button(connection, text="Подключиться", command=self.connect)
        self.connect_button.grid(row=1, column=0, columnspan=2, padx=(0, 4), pady=(8, 0), sticky="ew")
        self.initialize_button = ttk.Button(connection, text="Переинициализировать камеру", command=self.initialize_camera)
        self.initialize_button.grid(row=1, column=2, columnspan=2, padx=(4, 0), pady=(8, 0), sticky="ew")
        ttk.Label(
            connection,
            textvariable=self.wifi_status_var,
            foreground="#555555",
            wraplength=1020,
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(7, 0))
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)

        if self.series_bound:
            context_box = ttk.LabelFrame(outer, text="Привязка к серии", padding=8)
            context_box.pack(fill="x", pady=(0, 10))
            ttk.Label(context_box, text="Станция:").grid(row=0, column=0, sticky="w")
            station_combo = ttk.Combobox(
                context_box,
                textvariable=self.station_var,
                values=tuple(station["label"] for station in SERIES_CAMERA_STATIONS.values()),
                state="readonly",
                width=16,
            )
            station_combo.grid(row=0, column=1, sticky="w", padx=(6, 18))
            station_combo.bind("<<ComboboxSelected>>", self._series_context_changed)
            self.station_combo = station_combo
            ttk.Label(context_box, text="Пиксель:").grid(row=0, column=2, sticky="w")
            pixel_values = self.app.pixel_ids() if self.app.series is not None else []
            pixel_combo = ttk.Combobox(
                context_box,
                textvariable=self.pixel_var,
                values=pixel_values,
                state="readonly",
                width=24,
            )
            pixel_combo.grid(row=0, column=3, sticky="w", padx=(6, 0))
            pixel_combo.bind("<<ComboboxSelected>>", self._series_context_changed)
            self.pixel_combo = pixel_combo
            self.series_target_var = tk.StringVar()
            ttk.Label(context_box, textvariable=self.series_target_var, foreground="#555555", wraplength=980).grid(
                row=1, column=0, columnspan=4, sticky="w", pady=(6, 0)
            )
            self.measurement_launch_button = ttk.Button(
                context_box,
                command=self.open_selected_measurement,
            )
            self.measurement_launch_button.grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
            )
            ttk.Label(
                context_box,
                text=(
                    "Пиксель фиксируется камерой. После параметров приложение выполнит "
                    "фото до, видео с измерением и фото после."
                ),
                foreground="#555555",
                wraplength=700,
                justify="left",
            ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=(8, 0))
            context_box.columnconfigure(3, weight=1)
            self._series_context_changed()

        content = ttk.Panedwindow(outer, orient="horizontal")
        content.pack(fill="both", expand=True)

        left_pane = ttk.Panedwindow(content, orient="vertical")
        preview_frame = ttk.LabelFrame(left_pane, text="LiveView", padding=4)
        controls_outer, controls_frame = create_scrollable_frame(content, padding=8)
        content.add(left_pane, weight=3)
        content.add(controls_outer, weight=2)
        left_pane.add(preview_frame, weight=4)

        self.preview_canvas = tk.Canvas(
            preview_frame,
            background="#111111",
            highlightthickness=0,
            width=640,
            height=360,
        )
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.create_text(
            320,
            220,
            text="Подключитесь к сервису и запустите LiveView",
            fill="#DDDDDD",
            font=("Segoe UI", 12),
            tags=("placeholder",),
        )

        files_box = ttk.LabelFrame(left_pane, text="Файлы на Raspberry Pi", padding=6)
        left_pane.add(files_box, weight=1)
        self.files_tree = ttk.Treeview(files_box, columns=("type", "size", "date"), show="tree headings", height=4)
        self.files_tree.heading("#0", text="Имя")
        self.files_tree.heading("type", text="Тип")
        self.files_tree.heading("size", text="Размер")
        self.files_tree.heading("date", text="Создан")
        self.files_tree.column("#0", width=260, stretch=True)
        self.files_tree.column("type", width=65, stretch=False)
        self.files_tree.column("size", width=80, stretch=False)
        self.files_tree.column("date", width=135, stretch=False)
        self.files_tree.pack(fill="both", expand=True)
        self.files_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())
        file_buttons = ttk.Frame(files_box)
        file_buttons.pack(fill="x", pady=(5, 0))
        self.refresh_files_button = ttk.Button(file_buttons, text="Обновить список", command=self.refresh_files)
        self.refresh_files_button.pack(side="left")
        self.download_button = ttk.Button(file_buttons, text="Скачать выбранный", command=self.download_selected)
        self.download_button.pack(side="left", padx=(6, 0))
        ttk.Button(file_buttons, text="Открыть папку", command=self.open_download_folder).pack(side="right")

        status_box = ttk.LabelFrame(controls_frame, text="Состояние", padding=8)
        status_box.pack(fill="x")
        rows = [
            ("Состояние:", self.state_var),
            ("Модель:", self.model_var),
            ("Клиенты LiveView:", self.clients_var),
            ("Viewfinder Canon:", self.viewfinder_var),
            ("Свободно на Pi:", self.disk_var),
            ("Текущий файл:", self.file_var),
            ("Последняя ошибка:", self.error_var),
            ("Операция:", self.activity_var),
        ]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(status_box, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
            ttk.Label(status_box, textvariable=variable, wraplength=310, justify="left").grid(row=row, column=1, sticky="nw", pady=2)
        status_box.columnconfigure(1, weight=1)

        quality_box = ttk.LabelFrame(controls_frame, text="Качество фото и видео", padding=8)
        quality_box.pack(fill="x", pady=(10, 0))
        self.photo_quality_frame = ttk.Frame(quality_box)
        self.photo_quality_frame.pack(fill="x")
        ttk.Label(self.photo_quality_frame, text="Параметры фото появятся после подключения камеры.").grid(
            row=0, column=0, sticky="w"
        )
        self.video_quality_row = ttk.Frame(quality_box)
        ttk.Label(self.video_quality_row, text="Качество/размер камеры:").pack(side="left")
        self.video_quality_combo = ttk.Combobox(
            self.video_quality_row,
            textvariable=self.video_quality_var,
            state="readonly",
            width=43,
        )
        self.video_quality_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.video_quality_combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        self.video_fps_row = ttk.Frame(quality_box)
        ttk.Label(self.video_fps_row, text="Кадров в секунду камеры:").pack(side="left")
        self.video_fps_combo = ttk.Combobox(
            self.video_fps_row,
            textvariable=self.video_fps_var,
            state="readonly",
            width=43,
        )
        self.video_fps_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.video_fps_combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        self.video_source_label = ttk.Label(
            quality_box,
            textvariable=self.video_source_var,
            foreground="#333333",
            wraplength=360,
        )
        self.video_source_label.pack(
            anchor="w", pady=(5, 0)
        )
        ttk.Checkbutton(
            quality_box,
            text="Оставлять файл на Raspberry Pi после скачивания",
            variable=self.keep_remote_var,
            command=self._save_quality_preferences,
        ).pack(anchor="w", pady=(6, 0))
        if self.series_bound:
            self.combine_stability_telemetry_checkbutton = ttk.Checkbutton(
                quality_box,
                text="Создавать отдельное видео с показаниями стабильности",
                variable=self.combine_stability_telemetry_var,
                command=self._save_quality_preferences,
            )
            self.combine_stability_telemetry_checkbutton.pack(anchor="w", pady=(6, 0))

        exposure_box = ttk.LabelFrame(controls_frame, text="Экспозиция полноразмерного фото", padding=8)
        exposure_box.pack(fill="x", pady=(10, 0))
        self.photo_exposure_frame = ttk.Frame(exposure_box)
        self.photo_exposure_frame.pack(fill="x")
        ttk.Label(
            self.photo_exposure_frame,
            text="ISO, выдержка и другие параметры появятся после опроса камеры.",
            wraplength=360,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        crop_box = ttk.LabelFrame(controls_frame, text="Центральный кроп", padding=8)
        crop_box.pack(fill="x", pady=(10, 0))
        ttk.Label(crop_box, text="Оставить ширину:").grid(row=0, column=0, sticky="w")
        self.crop_width_spinbox = ttk.Spinbox(
            crop_box,
            from_=1,
            to=100,
            increment=1,
            textvariable=self.crop_width_var,
            width=8,
            command=self._crop_changed,
        )
        self.crop_width_spinbox.grid(row=0, column=1, sticky="w", padx=(8, 2))
        ttk.Label(crop_box, text="%").grid(row=0, column=2, sticky="w")
        ttk.Label(crop_box, text="Оставить высоту:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.crop_height_spinbox = ttk.Spinbox(
            crop_box,
            from_=1,
            to=100,
            increment=1,
            textvariable=self.crop_height_var,
            width=8,
            command=self._crop_changed,
        )
        self.crop_height_spinbox.grid(row=1, column=1, sticky="w", padx=(8, 2), pady=(5, 0))
        ttk.Label(crop_box, text="%").grid(row=1, column=2, sticky="w", pady=(5, 0))
        self.reset_crop_button = ttk.Button(crop_box, text="Сбросить 100 × 100%", command=self._reset_crop)
        self.reset_crop_button.grid(
            row=0, column=3, rowspan=2, sticky="e", padx=(12, 0)
        )
        crop_box.columnconfigure(3, weight=1)
        for widget in (self.crop_width_spinbox, self.crop_height_spinbox):
            widget.bind("<Return>", self._crop_changed)
            widget.bind("<FocusOut>", self._crop_changed)

        actions = ttk.LabelFrame(
            controls_frame,
            text="Съёмка выбранного пикселя" if self.series_bound else "Ручная проверка функций",
            padding=8,
        )
        actions.pack(fill="x", pady=(10, 0))
        ttk.Label(actions, text="Суффикс файла:" if self.series_bound else "Название снимка:").grid(
            row=0, column=0, sticky="w", padx=4, pady=(0, 2)
        )
        ttk.Entry(actions, textvariable=self.snapshot_name_var).grid(
            row=0, column=1, sticky="ew", padx=4, pady=(0, 2)
        )
        ttk.Label(
            actions,
            text=(
                "необязательно; ID пикселя, станция и время добавляются автоматически"
                if self.series_bound
                else "без расширения; пусто — автоматическое имя"
            ),
            foreground="#555555",
        ).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 4)
        )
        self.start_live_button = ttk.Button(actions, text="Запустить LiveView", command=self.start_liveview)
        self.stop_live_button = ttk.Button(actions, text="Остановить LiveView", command=self.stop_liveview)
        self.snapshot_button = ttk.Button(actions, text="Сохранить кадр LiveView", command=self.save_snapshot)
        self.photo_button = ttk.Button(actions, text="Сделать фото камерой", command=self.capture_photo)
        self.start_video_button = ttk.Button(actions, text="Начать запись видео", command=self.start_recording)
        self.stop_video_button = ttk.Button(actions, text="Остановить и скачать видео", command=self.stop_recording)
        buttons = [
            self.start_live_button,
            self.stop_live_button,
            self.snapshot_button,
            self.photo_button,
            self.start_video_button,
            self.stop_video_button,
        ]
        for index, button in enumerate(buttons):
            button.grid(row=index // 2 + 2, column=index % 2, sticky="ew", padx=4, pady=4)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        self._log("Ожидание подключения")

    @staticmethod
    def _initial_wifi_status(settings: Dict[str, Any]) -> str:
        if not bool(settings.get("auto_connect_wifi", False)):
            return "Wi-Fi: автоматическое переключение выключено"
        profile = str(settings.get("wifi_profile") or "").strip()
        return (
            f"Wi-Fi: при подключении будет выбран профиль «{profile}»"
            if profile
            else "Wi-Fi: задайте профиль Raspberry Pi в настройках"
        )

    def _set_wifi_status_from_worker(self, text: str) -> None:
        try:
            self.after(0, lambda value=str(text): self.wifi_status_var.set(value))
        except tk.TclError:
            pass

    def connect(self) -> None:
        try:
            self._save_camera_settings()
            self._stop_local_stream()
            self.client = self._make_client()
        except Exception as exc:
            messagebox.showerror("Камера", str(exc), parent=self)
            return

        def work():
            assert self.client is not None
            camera_settings = dict(
                self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
            )
            wifi_controller = WindowsWifiController()
            wifi_session = connect_camera_service_with_wifi(
                self.client,
                camera_settings,
                existing_session=self._wifi_session,
                wifi_controller=wifi_controller,
                progress=self._set_wifi_status_from_worker,
            )
            try:
                status = self.client.status()
                if not status.get("camera_connected"):
                    status = self.client.initialize()
                capabilities = self._capabilities_or_legacy(self.client)
            except Exception:
                if self._wifi_session is None and wifi_session is not None:
                    try:
                        wifi_controller.restore(
                            wifi_session,
                            timeout_s=float(
                                camera_settings.get("wifi_connect_timeout_s", 25.0)
                            ),
                        )
                    except Exception:
                        pass
                raise
            return status, capabilities, wifi_session

        self._run_async(
            "Подключение к сервису",
            work,
            self._connected,
            failed=self._camera_connect_failed,
        )

    def _connected(self, result) -> None:
        status, capabilities, wifi_session = result
        if wifi_session is not None:
            self._wifi_session = wifi_session
            if wifi_session.switched:
                self.wifi_status_var.set(
                    f"Wi-Fi: подключён профиль «{wifi_session.target_profile}»"
                )
            else:
                self.wifi_status_var.set(
                    f"Wi-Fi: профиль «{wifi_session.target_profile}» уже был подключён"
                )
        else:
            self.wifi_status_var.set("Сервис Raspberry Pi доступен через текущую сеть")
        self._apply_status(status)
        self._apply_capabilities(capabilities)
        if capabilities.get("legacy_service"):
            self._log("Сервис Raspberry Pi работает по старому API: обновите его для выбора качества и удаления файлов.")
        self._log(f"Сервис камеры доступен: {self.client.base_url if self.client else ''}")
        self.refresh_files(silent=True)
        self._schedule_status_poll()

    def _camera_connect_failed(self, exc: Exception) -> None:
        self.client = None
        self.status = {}
        self.wifi_status_var.set(f"Wi-Fi/сервис камеры: {exc}")
        self._update_buttons()

    def initialize_camera(self) -> None:
        client = self._require_client()
        if not client:
            return
        self._run_async(
            "Инициализация камеры",
            lambda: (client.initialize(), self._capabilities_or_legacy(client)),
            self._camera_initialized,
        )

    def _camera_initialized(self, result) -> None:
        status, capabilities = result
        self._apply_status(status)
        self._apply_capabilities(capabilities)
        if capabilities.get("legacy_service"):
            self._log("Сервис Raspberry Pi работает по старому API: обновите его для выбора качества и удаления файлов.")
        self._log(f"Камера готова: {status.get('camera_model') or 'модель не указана'}")

    def start_liveview(self) -> None:
        client = self._require_client()
        if not client:
            return
        video_settings = self._selected_video_settings()
        self._run_async("Запуск LiveView", lambda: client.start_liveview(video_settings), self._liveview_started)

    def _liveview_started(self, status: Dict[str, Any]) -> None:
        self._apply_status(status)
        self._start_local_stream()
        self._log("LiveView запущен.")

    def stop_liveview(self) -> None:
        client = self._require_client()
        if not client:
            return
        self._stop_local_stream()
        self._run_async("Остановка LiveView", client.stop_liveview, self._liveview_stopped)

    def _liveview_stopped(self, status: Dict[str, Any]) -> None:
        self._apply_status(status)
        self._show_placeholder("LiveView остановлен")
        self._log("LiveView остановлен.")

    def save_snapshot(self) -> None:
        self._create_and_download("Сохранение preview-кадра", "Кадр LiveView сохранён", "snapshot")

    def capture_photo(self) -> None:
        self._stop_local_stream()
        self._create_and_download("Полноразмерная фотография", "Фото камеры сохранено", "photo")

    def _create_and_download(
        self,
        operation_label: str,
        success_label: str,
        kind: str,
        after_complete: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        client = self._require_client()
        if not client:
            if on_error is not None:
                on_error(RuntimeError("Нет подключения к камере."))
            return
        entered_name = self.snapshot_name_var.get().strip()
        requested_name = self._capture_stem(kind) if self.series_bound else safe_capture_stem(entered_name)
        if entered_name and not safe_capture_stem(entered_name):
            messagebox.showwarning(
                "Название снимка",
                "Введите название, содержащее буквы или цифры.",
                parent=self,
            )
            if on_error is not None:
                on_error(ValueError("Некорректный суффикс имени файла."))
            return
        keep_remote = bool(self.keep_remote_var.get())
        photo_settings = self._selected_photo_settings()
        crop = self._selected_crop(show_error=True)
        if crop is None:
            if on_error is not None:
                on_error(ValueError("Некорректные параметры кадрирования."))
            return
        download_dir = self._download_dir()

        def work():
            remote = (
                client.save_liveview_snapshot(requested_name, crop)
                if kind == "snapshot"
                else client.capture_photo(photo_settings, requested_name, crop)
            )
            preferred_name = f"{requested_name}.jpg" if requested_name else ""
            local = client.download_file(remote, download_dir, preferred_name=preferred_name)
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return remote, local, deleted, delete_error, client.status()

        def complete(result) -> None:
            remote, local, deleted, delete_error, status = result
            self._apply_status(status)
            hold_captured_photo = after_complete is not None and kind.startswith("photo")
            if status.get("liveview_active") and not hold_captured_photo:
                self._start_local_stream()
            self._log(f"{success_label}: {local}")
            self._record_series_camera_file(local, kind, remote)
            self._log_remote_cleanup(deleted, delete_error)
            if kind != "snapshot":
                self._show_local_photo(Path(local))
            if after_complete is None:
                self.refresh_files(silent=True)
            else:
                after_complete(Path(local))

        self._run_async(operation_label, work, complete, failed=on_error)

    def start_recording(
        self,
        after_started: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        client = self._require_client()
        if not client:
            if on_error is not None:
                on_error(RuntimeError("Нет подключения к камере."))
            return
        video_settings = self._selected_video_settings()
        crop = self._selected_crop(show_error=True)
        if crop is None:
            if on_error is not None:
                on_error(ValueError("Некорректные параметры кадрирования."))
            return
        self._recording_capture_stem = self._capture_stem("video") if self.series_bound else ""
        if self.series_bound:
            self._recording_context = {
                "pixel_id": self.pixel_var.get().strip(),
                "station_key": camera_station_key(self.station_var.get()),
                "download_dir": self._download_dir(),
            }
        else:
            self._recording_context = {"download_dir": self._download_dir()}
        self._run_async(
            "Запуск записи",
            lambda: client.start_recording(video_settings, crop),
            lambda status: self._recording_started(status, after_started),
            failed=on_error,
        )

    def _recording_started(
        self,
        status: Dict[str, Any],
        after_started: Optional[Callable[[], None]] = None,
    ) -> None:
        self._recording_started_monotonic = time.monotonic()
        self._recording_stop_requested_monotonic = None
        self._apply_status(status)
        self._start_local_stream()
        self._log("Запись видео началась; LiveView продолжает работать из того же потока.")
        if after_started is not None:
            after_started()

    def stop_recording(
        self,
        after_stopped: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        client = self._require_client()
        if not client:
            if on_error is not None:
                on_error(RuntimeError("Нет подключения к камере."))
            return
        keep_remote = bool(self.keep_remote_var.get())
        self._recording_stop_requested_monotonic = time.monotonic()

        def work():
            remote = client.stop_recording()
            preferred_name = f"{self._recording_capture_stem}.mp4" if self._recording_capture_stem else ""
            download_dir = Path(self._recording_context.get("download_dir") or self._download_dir())
            local = client.download_file(remote, download_dir, preferred_name=preferred_name)
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return remote, local, deleted, delete_error, client.status()

        def complete(result) -> None:
            remote, local, deleted, delete_error, status = result
            self._apply_status(status)
            self._log(f"Видео корректно завершено и скачано: {local}")
            sync_metadata = self._write_video_measurement_timeline(Path(local))
            self._record_series_camera_file(
                local,
                "video",
                remote,
                extra_params=sync_metadata,
                capture_context=self._recording_context,
            )
            self._recording_capture_stem = ""
            self._recording_started_monotonic = None
            self._recording_stop_requested_monotonic = None
            self._recording_context = {}
            self._log_remote_cleanup(deleted, delete_error)
            if after_stopped is None:
                self.refresh_files(silent=True)
            else:
                after_stopped(Path(local))

        self._run_async("Остановка записи", work, complete, failed=on_error)

    def refresh_files(self, silent: bool = False) -> None:
        client = self._require_client(show_error=not silent)
        if not client or (self._busy and not silent):
            return

        def complete(files: list[RemoteFile]) -> None:
            self.remote_files = {item.file_id: item for item in files}
            for item_id in self.files_tree.get_children():
                self.files_tree.delete(item_id)
            for item in files:
                self.files_tree.insert(
                    "",
                    "end",
                    iid=item.file_id,
                    text=item.name,
                    values=(item.kind, self._format_size(item.size), item.created_at[:19].replace("T", " ")),
                )
            self._update_buttons()

        self._run_async("Обновление списка файлов", client.list_files, complete, quiet=silent)

    def download_selected(self) -> None:
        client = self._require_client()
        if not client:
            return
        selection = self.files_tree.selection()
        if not selection:
            messagebox.showwarning("Камера", "Выберите файл в списке.", parent=self)
            return
        remote = self.remote_files.get(selection[0])
        if not remote:
            return
        keep_remote = bool(self.keep_remote_var.get())
        download_dir = self._download_dir()

        def work():
            preferred_name = ""
            if self.series_bound:
                extension = Path(remote.name).suffix or (".mp4" if remote.kind == "video" else ".jpg")
                preferred_name = f"{self._capture_stem('import')}{extension}"
            local = client.download_file(remote, download_dir, preferred_name=preferred_name)
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return local, deleted, delete_error

        def complete(result) -> None:
            local, deleted, delete_error = result
            self._log(f"Файл скачан: {local}")
            self._record_series_camera_file(local, "import", remote)
            self._log_remote_cleanup(deleted, delete_error)
            self.refresh_files(silent=True)

        self._run_async("Скачивание файла", work, complete)

    def open_download_folder(self) -> None:
        folder = self._download_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Папка камеры", str(exc), parent=self)

    def _apply_capabilities(self, capabilities: Dict[str, Any]) -> None:
        self.capability_data = capabilities or {}
        settings = self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        saved_photo = dict(settings.get("photo_quality_settings") or {})
        saved_exposure = dict(settings.get("photo_exposure_settings") or {})
        self.photo_quality_vars = self._configure_photo_control_group(
            self.photo_quality_frame,
            list(capabilities.get("photo_controls") or []),
            saved_photo,
            "Камера не сообщила переключаемые JPEG-параметры; используется её текущее качество.",
            "Качество фото",
        )
        self.photo_exposure_vars = self._configure_photo_control_group(
            self.photo_exposure_frame,
            list(capabilities.get("exposure_controls") or []),
            saved_exposure,
            (
                "Камера не сообщила изменяемые параметры экспозиции. "
                "Переведите диск режимов в M и нажмите «Переинициализировать камеру»."
            ),
            "Экспозиция",
            note=str(capabilities.get("exposure_note") or ""),
        )

        saved_video = dict(settings.get("video_camera_settings") or {})
        self.video_quality_control_path, self.video_quality_by_label = self._configure_video_control(
            self.video_quality_combo,
            self.video_quality_var,
            list(capabilities.get("video_quality_controls") or []),
            saved_video,
            "Камера не предоставляет выбор качества",
        )
        self._set_video_control_row_visible(
            self.video_quality_row,
            bool(self.video_quality_control_path),
        )
        self.video_fps_control_path, self.video_fps_by_label = self._configure_video_control(
            self.video_fps_combo,
            self.video_fps_var,
            list(capabilities.get("video_fps_controls") or []),
            saved_video,
            "Камера не предоставляет выбор FPS",
        )
        self._set_video_control_row_visible(
            self.video_fps_row,
            bool(self.video_fps_control_path),
        )
        self._update_video_source_text()
        self._save_quality_preferences()

    def _configure_photo_control_group(
        self,
        frame: ttk.Frame,
        controls: list[Dict[str, Any]],
        saved: Dict[str, str],
        unavailable_text: str,
        default_label: str,
        note: str = "",
    ) -> Dict[str, tk.StringVar]:
        for child in frame.winfo_children():
            child.destroy()
        variables: Dict[str, tk.StringVar] = {}
        if not controls:
            ttk.Label(frame, text=unavailable_text, wraplength=360, justify="left").grid(
                row=0, column=0, columnspan=2, sticky="w"
            )
        for row, control in enumerate(controls):
            path = str(control.get("path") or "")
            choices = [str(value) for value in control.get("choices") or []]
            if not path or not choices:
                continue
            selected = str(saved.get(path) or control.get("current") or choices[0])
            if selected not in choices:
                selected = str(control.get("current") or choices[0])
            variable = tk.StringVar(value=selected)
            variables[path] = variable
            ttk.Label(frame, text=str(control.get("label") or default_label) + ":").grid(
                row=row, column=0, sticky="w", pady=2
            )
            combo = ttk.Combobox(
                frame,
                textvariable=variable,
                values=choices,
                state="readonly",
                width=30,
            )
            combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        if note and controls:
            ttk.Label(frame, text=note, foreground="#555555", wraplength=360, justify="left").grid(
                row=len(controls), column=0, columnspan=2, sticky="w", pady=(5, 0)
            )
        frame.columnconfigure(1, weight=1)
        return variables

    @staticmethod
    def _configure_video_control(
        combo: ttk.Combobox,
        variable: tk.StringVar,
        controls: list[Dict[str, Any]],
        saved: Dict[str, str],
        unavailable_text: str,
    ) -> tuple[str, Dict[str, str]]:
        control = first_available_video_control(controls)
        path = str(control.get("path") or "")
        choices = [str(value) for value in control.get("choices") or []]
        if not path or not choices:
            combo.configure(values=(), state="disabled")
            variable.set(unavailable_text)
            return "", {}
        selected = str(saved.get(path) or control.get("current") or choices[0])
        if selected not in choices:
            selected = str(control.get("current") or choices[0])
        labels = {value: value for value in choices}
        combo.configure(values=list(labels), state="readonly")
        variable.set(selected)
        return path, labels

    def _set_video_control_row_visible(self, row: ttk.Frame, visible: bool) -> None:
        if visible:
            row.pack(fill="x", pady=(6, 0), before=self.video_source_label)
        else:
            row.pack_forget()

    def _selected_photo_settings(self) -> Dict[str, str]:
        selected = {
            path: variable.get()
            for variables in (self.photo_quality_vars, self.photo_exposure_vars)
            for path, variable in variables.items()
            if variable.get()
        }
        return selected

    def _selected_video_settings(self) -> Dict[str, str]:
        selected: Dict[str, str] = {}
        quality = self.video_quality_by_label.get(self.video_quality_var.get())
        fps = self.video_fps_by_label.get(self.video_fps_var.get())
        if self.video_quality_control_path and quality:
            selected[self.video_quality_control_path] = quality
        if self.video_fps_control_path and fps:
            selected[self.video_fps_control_path] = fps
        return selected

    def _selected_crop(self, show_error: bool = False) -> Optional[Dict[str, float]]:
        try:
            return normalize_center_crop(
                {
                    "width_percent": self.crop_width_var.get(),
                    "height_percent": self.crop_height_var.get(),
                }
            )
        except ValueError as exc:
            if show_error:
                messagebox.showwarning("Кадрирование", str(exc), parent=self)
            return None

    def _crop_changed(self, _event=None) -> None:
        crop = self._selected_crop(show_error=False)
        if crop is None:
            return
        self._active_crop = crop
        self._save_quality_preferences()
        self._update_video_source_text()
        if self._last_frame is not None:
            self._render_frame(self._last_frame)

    def _reset_crop(self) -> None:
        self.crop_width_var.set("100")
        self.crop_height_var.set("100")
        self._crop_changed()

    @staticmethod
    def _capabilities_or_legacy(client: CameraClient) -> Dict[str, Any]:
        try:
            return client.capabilities()
        except CameraClientError as exc:
            if exc.error_code != "HTTP_404":
                raise
            return {
                "success": True,
                "legacy_service": True,
                "photo_controls": [],
                "video_quality_controls": [],
                "video_fps_controls": [],
            }

    def _save_quality_preferences(self, _event=None) -> None:
        settings = dict(self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"]))
        settings["keep_remote_files_after_download"] = bool(self.keep_remote_var.get())
        settings["combine_stability_telemetry_video"] = bool(self.combine_stability_telemetry_var.get())
        crop = self._selected_crop(show_error=False)
        if crop is not None:
            settings["crop_width_percent"] = crop["width_percent"]
            settings["crop_height_percent"] = crop["height_percent"]
        settings["video_camera_settings"] = self._selected_video_settings()
        if self.photo_quality_vars:
            settings["photo_quality_settings"] = {
                path: variable.get() for path, variable in self.photo_quality_vars.items() if variable.get()
            }
        if self.photo_exposure_vars:
            settings["photo_exposure_settings"] = {
                path: variable.get() for path, variable in self.photo_exposure_vars.items() if variable.get()
            }
        self.app.app_settings["camera"] = settings
        save_app_settings(self.app.app_settings)

    @staticmethod
    def _delete_remote_after_download(
        client: CameraClient,
        remote: RemoteFile,
        keep_remote: bool,
    ) -> tuple[bool, str]:
        if keep_remote:
            return False, ""
        try:
            client.delete_file(remote)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _log_remote_cleanup(self, deleted: bool, delete_error: str) -> None:
        if deleted:
            self._log("Проверенное скачивание завершено; исходный файл удалён с Raspberry Pi.")
        elif delete_error:
            self._log(f"Локальный файл сохранён, но удалить исходник с Raspberry Pi не удалось: {delete_error}")

    def _update_video_source_text(self) -> None:
        width = self.status.get("frame_width")
        height = self.status.get("frame_height")
        fps = float(self.status.get("fps") or 0.0)
        if width and height:
            output_width, output_height = center_crop_dimensions(int(width), int(height), self._active_crop)
            self.video_source_var.set(
                f"LiveView: {int(width)}×{int(height)} · {fps:.1f} кадр/с\n"
                f"После кропа: {output_width}×{output_height} "
                f"({self._active_crop['width_percent']:g} × {self._active_crop['height_percent']:g}%)"
            )
        else:
            self.video_source_var.set("LiveView: ожидание первого кадра · 0.0 кадр/с")

    def _start_local_stream(self) -> None:
        if not self.client:
            return
        self._photo_preview_active = False
        if self._stream_thread and self._stream_thread.is_alive():
            return
        stop_event = threading.Event()
        self._stream_stop = stop_event
        client = self.client

        def receive() -> None:
            try:
                client.iter_liveview_frames(stop_event, self._queue_frame)
            except Exception as exc:
                if not stop_event.is_set() and not self._closed:
                    try:
                        self.after(0, lambda error=exc: self._stream_failed(error))
                    except tk.TclError:
                        pass

        self._stream_thread = threading.Thread(target=receive, name="camera-liveview-client", daemon=True)
        self._stream_thread.start()

    def _stop_local_stream(self) -> None:
        stop_event = self._stream_stop
        thread = self._stream_thread
        client = self.client
        stop_event.set()
        if client is not None:
            client.close_liveview_stream()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break
        if self._stream_thread is thread and (thread is None or not thread.is_alive()):
            self._stream_thread = None

    def _queue_frame(self, frame: bytes) -> None:
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(frame)
            except queue.Full:
                pass

    def _consume_frames(self) -> None:
        if self._closed:
            return
        frame = None
        while True:
            try:
                frame = self._frame_queue.get_nowait()
            except queue.Empty:
                break
        if frame and not self._photo_preview_active:
            self._last_frame = frame
            self._render_frame(frame)
        self._frame_after_id = self.after(50, self._consume_frames)

    def _render_frame(self, frame: bytes) -> None:
        self._photo_preview_active = False
        try:
            width = max(self.preview_canvas.winfo_width(), 200)
            height = max(self.preview_canvas.winfo_height(), 160)
            image = decode_liveview_frame(frame, (width, height), self._active_crop)
        except Exception as exc:
            self._report_render_error("декодирование JPEG", exc)
            return

        try:
            # Bind the image to this window's Tcl interpreter explicitly.
            photo = ImageTk.PhotoImage(image=image, master=self.preview_canvas)
            self._canvas_image = photo
            self.preview_canvas.delete("all")
            canvas_w = self.preview_canvas.winfo_width()
            canvas_h = self.preview_canvas.winfo_height()
            center_x, center_y = canvas_w // 2, canvas_h // 2
            self.preview_canvas.create_image(center_x, center_y, image=photo, anchor="center")
            arm = max(12, min(image.width, image.height) // 16)
            self.preview_canvas.create_line(center_x - arm, center_y, center_x + arm, center_y, fill="#00FF66", width=1)
            self.preview_canvas.create_line(center_x, center_y - arm, center_x, center_y + arm, fill="#00FF66", width=1)
            self._last_render_error = ""
            self._render_error_count = 0
        except Exception as exc:
            self._report_render_error("отрисовка Tk", exc)

    def _show_local_photo(self, path: Path) -> None:
        """Show the downloaded full-resolution photo while the workflow gate is open."""

        if Image is None or ImageTk is None:
            return
        try:
            width = max(self.preview_canvas.winfo_width(), 200)
            height = max(self.preview_canvas.winfo_height(), 160)
            image = load_local_photo_preview(path, (width, height))
            photo = ImageTk.PhotoImage(image=image, master=self.preview_canvas)
            self._canvas_image = photo
            self._last_frame = None
            self._photo_preview_active = True
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(
                self.preview_canvas.winfo_width() // 2,
                self.preview_canvas.winfo_height() // 2,
                image=photo,
                anchor="center",
            )
        except Exception as exc:
            self._log(f"Фото сохранено, но не удалось показать его в окне: {exc}")

    def _report_render_error(self, stage: str, exc: Exception) -> None:
        signature = f"{stage}: {type(exc).__name__}: {exc}"
        if signature == self._last_render_error:
            self._render_error_count += 1
        else:
            self._last_render_error = signature
            self._render_error_count = 1
        if self._render_error_count == 1 or self._render_error_count % 50 == 0:
            repeats = f" (повторено {self._render_error_count} раз)" if self._render_error_count > 1 else ""
            self._log(f"Ошибка LiveView, этап «{stage}»: {exc}{repeats}")

    def _stream_failed(self, exc: Exception) -> None:
        self._stop_local_stream()
        self._show_placeholder("Поток LiveView потерян")
        self.error_var.set(str(exc))
        self._log(f"Ошибка LiveView: {exc}")
        self._update_buttons()

    def _show_placeholder(self, text: str) -> None:
        self._photo_preview_active = False
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            max(self.preview_canvas.winfo_width() // 2, 160),
            max(self.preview_canvas.winfo_height() // 2, 120),
            text=text,
            fill="#DDDDDD",
            font=("Segoe UI", 12),
        )
        self._canvas_image = None

    def _apply_status(self, status: Dict[str, Any]) -> None:
        self.status = status or {}
        self.state_var.set(str(status.get("state") or "UNKNOWN"))
        self.model_var.set(str(status.get("camera_model") or "—"))
        self.fps_var.set(f"{float(status.get('fps') or 0.0):.1f} кадр/с")
        self.clients_var.set(str(int(status.get("liveview_clients") or 0)))
        if status.get("liveview_active"):
            self.viewfinder_var.set("активен")
        elif status.get("viewfinder_off_verified") is True:
            self.viewfinder_var.set("выключен (проверено)")
        elif status.get("viewfinder_control"):
            self.viewfinder_var.set("выключение не подтверждено")
        else:
            self.viewfinder_var.set("контроль недоступен")
        self.disk_var.set(f"{int(status.get('free_disk_mb') or 0)} МБ")
        self.file_var.set(str(status.get("current_file") or "—"))
        self.error_var.set(str(status.get("last_error") or "—"))
        self._update_video_source_text()
        if status.get("liveview_active"):
            self._start_local_stream()
        elif not status.get("recording_active"):
            self._stop_local_stream()
        self._update_buttons()

    def _schedule_status_poll(self) -> None:
        if self._closed or not self.client:
            return
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
        self._poll_after_id = self.after(2000, self._poll_status)

    def _poll_status(self) -> None:
        if self._closed or not self.client:
            return
        if self._status_request_running or self._busy:
            self._schedule_status_poll()
            return
        self._status_request_running = True
        client = self.client

        def work() -> None:
            try:
                status = client.status()
                if not self._closed:
                    self.after(0, lambda value=status, expected=client: self._status_received(expected, value))
            except Exception as exc:
                if not self._closed:
                    self.after(0, lambda error=exc, expected=client: self._connection_lost(expected, error))
            finally:
                if not self._closed:
                    self.after(0, lambda expected=client: self._status_poll_finished(expected))

        threading.Thread(target=work, name="camera-status", daemon=True).start()

    def _status_received(self, client: CameraClient, status: Dict[str, Any]) -> None:
        if client is self.client:
            self._apply_status(status)

    def _connection_lost(self, client: CameraClient, exc: Exception) -> None:
        if client is not self.client:
            return
        self._stop_local_stream()
        self.client = None
        self.status = {}
        self.capability_data = {}
        self.state_var.set("Связь потеряна")
        self.model_var.set("—")
        self.fps_var.set("0.0 кадр/с")
        self.clients_var.set("0")
        self.viewfinder_var.set("контроль недоступен")
        self.disk_var.set("—")
        self.file_var.set("—")
        self.error_var.set(str(exc))
        self.video_quality_combo.configure(values=(), state="disabled")
        self.video_fps_combo.configure(values=(), state="disabled")
        self._set_video_control_row_visible(self.video_quality_row, False)
        self._set_video_control_row_visible(self.video_fps_row, False)
        self.video_quality_var.set("Нет связи с Raspberry Pi")
        self.video_fps_var.set("Нет связи с Raspberry Pi")
        self._show_placeholder("Связь с Raspberry Pi потеряна\nНажмите «Подключиться» для повтора")
        self._log(f"Связь с Raspberry Pi потеряна: {exc}")
        self._update_buttons()

    def _status_poll_finished(self, _client: CameraClient) -> None:
        self._status_request_running = False
        self._schedule_status_poll()

    def _run_async(
        self,
        label: str,
        work: Callable[[], Any],
        complete: Callable[[Any], None],
        quiet: bool = False,
        failed: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        if self._busy:
            if not quiet:
                messagebox.showwarning("Камера", "Дождитесь завершения текущей операции.", parent=self)
            return
        self._busy = True
        self._update_buttons()
        if not quiet:
            self._log(f"{label}…")

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                if not self._closed:
                    self.after(0, lambda error=exc: self._operation_failed(label, error, quiet, failed))
            else:
                if not self._closed:
                    self.after(0, lambda value=result: self._operation_completed(complete, value))

        threading.Thread(target=runner, name=f"camera-{label}", daemon=True).start()

    def _operation_completed(self, complete: Callable[[Any], None], value: Any) -> None:
        self._busy = False
        try:
            complete(value)
        finally:
            self._update_buttons()

    def _operation_failed(
        self,
        label: str,
        exc: Exception,
        quiet: bool,
        failed: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self._busy = False
        display_text = camera_error_dialog_text(exc)
        self.error_var.set(display_text)
        self._log(f"{label}: {display_text.replace(chr(10), ' | ')}")
        if not quiet:
            messagebox.showerror("Камера", display_text, parent=self)
        if failed is not None:
            failed(exc)
        self._update_buttons()

    def _update_buttons(self) -> None:
        service_connected = self.client is not None and bool(self.status)
        camera_connected = bool(self.status.get("camera_connected"))
        live = bool(self.status.get("liveview_active"))
        recording = bool(self.status.get("recording_active"))
        guided = bool(self._guided_measurement_active)
        normal = "normal" if not self._busy and not guided else "disabled"
        self.connect_button.configure(state=normal)
        self.initialize_button.configure(
            state="normal" if service_connected and not recording and not self._busy and not guided else "disabled"
        )
        self.start_live_button.configure(
            state="normal" if camera_connected and not live and not self._busy and not guided else "disabled"
        )
        self.stop_live_button.configure(
            state="normal" if live and not recording and not self._busy and not guided else "disabled"
        )
        self.snapshot_button.configure(state="normal" if live and not self._busy and not guided else "disabled")
        self.photo_button.configure(
            state="normal" if camera_connected and not recording and not self._busy and not guided else "disabled"
        )
        self.start_video_button.configure(
            state="normal" if camera_connected and not recording and not self._busy and not guided else "disabled"
        )
        self.stop_video_button.configure(state="normal" if recording and not self._busy and not guided else "disabled")
        crop_state = "disabled" if recording or self._busy or guided else "normal"
        self.crop_width_spinbox.configure(state=crop_state)
        self.crop_height_spinbox.configure(state=crop_state)
        self.reset_crop_button.configure(state=crop_state)
        self.refresh_files_button.configure(
            state="normal" if service_connected and not self._busy and not guided else "disabled"
        )
        self.download_button.configure(
            state=(
                "normal"
                if service_connected and bool(self.files_tree.selection()) and not self._busy and not guided
                else "disabled"
            )
        )
        if self.series_bound:
            context_state = "disabled" if recording or self._busy or guided else "readonly"
            if self.station_combo is not None:
                self.station_combo.configure(state=context_state)
            if self.pixel_combo is not None:
                self.pixel_combo.configure(state=context_state)
            if self.measurement_launch_button is not None:
                self.measurement_launch_button.configure(
                    state=(
                        "normal"
                        if bool(self.pixel_var.get().strip())
                        and camera_connected
                        and not recording
                        and not self._busy
                        and not guided
                        else "disabled"
                    )
                )
            if self.combine_stability_telemetry_checkbutton is not None:
                self.combine_stability_telemetry_checkbutton.configure(
                    state="disabled" if recording or self._busy or guided else "normal"
                )

    def _make_client(self) -> CameraClient:
        settings = self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        try:
            port = int(self.port_var.get().strip())
        except ValueError as exc:
            raise ValueError("Порт сервиса камеры должен быть целым числом.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Порт сервиса камеры должен быть от 1 до 65535.")
        base_url = build_camera_service_url(self.host_var.get(), port)
        return CameraClient(
            base_url,
            timeout_s=float(settings.get("request_timeout_s", 8.0)),
            stream_timeout_s=float(settings.get("stream_timeout_s", 12.0)),
        )

    def _save_camera_settings(self) -> None:
        settings = dict(self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"]))
        settings["host"] = self.host_var.get().strip() or "192.168.4.1"
        settings["port"] = int(self.port_var.get().strip())
        settings["keep_remote_files_after_download"] = bool(self.keep_remote_var.get())
        settings["combine_stability_telemetry_video"] = bool(self.combine_stability_telemetry_var.get())
        crop = self._selected_crop(show_error=False)
        if crop is None:
            raise ValueError("Исправьте параметры кадрирования.")
        settings["crop_width_percent"] = crop["width_percent"]
        settings["crop_height_percent"] = crop["height_percent"]
        settings["video_camera_settings"] = self._selected_video_settings()
        if self.photo_quality_vars:
            settings["photo_quality_settings"] = {
                path: variable.get() for path, variable in self.photo_quality_vars.items() if variable.get()
            }
        if self.photo_exposure_vars:
            settings["photo_exposure_settings"] = {
                path: variable.get() for path, variable in self.photo_exposure_vars.items() if variable.get()
            }
        self.app.app_settings["camera"] = settings
        save_app_settings(self.app.app_settings)

    def _download_dir(self) -> Path:
        if self.series_bound:
            if self.app.series is None:
                raise ValueError("Серия закрыта. Вернитесь в меню и откройте её снова.")
            pixel_id = self.pixel_var.get().strip()
            if not pixel_id:
                raise ValueError("Выберите пиксель для съёмки.")
            context = (
                str(self.app.series.series_folder),
                pixel_id,
                camera_station_key(self.station_var.get()),
            )
            if self._series_capture_dir is None or self._series_capture_context != context:
                self._series_capture_dir = ensure_camera_session_folder(
                    self.app.series.series_folder,
                    pixel_id,
                    self.app.series.journal.get_pixel(pixel_id),
                )
                self._series_capture_context = context
            return self._series_capture_dir
        settings = self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        download_root = Path(str(settings.get("download_dir") or SCRIPT_DIR / "camera_downloads"))
        return free_camera_date_folder(download_root)

    def _capture_stem(self, kind: str) -> str:
        if not self.series_bound:
            return safe_capture_stem(self.snapshot_name_var.get())
        pixel_id = self.pixel_var.get().strip()
        if not pixel_id:
            raise ValueError("Выберите пиксель для съёмки.")
        return build_series_capture_stem(
            pixel_id,
            camera_station_key(self.station_var.get()),
            kind,
            self.snapshot_name_var.get().strip(),
        )

    def _series_context_changed(self, _event=None) -> None:
        if not self.series_bound or not hasattr(self, "series_target_var"):
            return
        station_key = camera_station_key(self.station_var.get())
        station = SERIES_CAMERA_STATIONS[station_key]
        pixel_id = self.pixel_var.get().strip() or "—"
        context = (
            str(self.app.series.series_folder),
            pixel_id,
            station_key,
        ) if self.app.series is not None else None
        if self._series_capture_context is not None and self._series_capture_context != context:
            self._series_capture_dir = None
            self._series_capture_context = None
        self.series_target_var.set(
            f"{station['label']} · пиксель {pixel_id}. "
            "Файлы будут записаны в отдельную нумерованную папку измерения камеры."
        )
        if self.measurement_launch_button is not None:
            self.measurement_launch_button.configure(text=f"Параметры и запуск: {station['label']}")

    def open_selected_measurement(self) -> None:
        """Open the selected station setup with the camera-bound pixel preselected."""

        if not self.series_bound or self.app.series is None:
            return
        if self._guided_measurement_active or self._busy:
            messagebox.showwarning("Камера серии", "Дождитесь завершения текущей операции.", parent=self)
            return
        if not self.client or not self.status.get("camera_connected"):
            messagebox.showwarning(
                "Камера серии",
                "Сначала подключите и инициализируйте камеру.",
                parent=self,
            )
            return
        if self.status.get("recording_active"):
            messagebox.showwarning(
                "Камера серии",
                "Перед автоматическим запуском остановите текущую запись видео.",
                parent=self,
            )
            return
        pixel_id = self.pixel_var.get().strip()
        if not pixel_id:
            messagebox.showwarning("Камера серии", "Выберите пиксель для измерения.", parent=self)
            return
        station_key = camera_station_key(self.station_var.get())
        if station_key == "stability":
            open_stability_window(
                self.app,
                initial_pixel=pixel_id,
                parent=self,
                locked_pixel=True,
                measurement_runner=self.run_guided_measurement,
            )
        else:
            open_ivl_window(
                self.app,
                initial_pixel=pixel_id,
                parent=self,
                locked_pixel=True,
                measurement_runner=self.run_guided_measurement,
            )

    def run_guided_measurement(
        self,
        station_key: str,
        pixel_id: str,
        measurement: Callable[[], Optional[Dict[str, Any]]],
    ) -> None:
        """Run before-photo, video+measurement, post-roll, and after-photo in order."""

        if self._guided_measurement_active:
            return
        self.station_var.set(SERIES_CAMERA_STATIONS[camera_station_key(station_key)]["label"])
        self.pixel_var.set(pixel_id)
        self._series_capture_dir = None
        self._series_capture_context = None
        camera_session_dir = self._download_dir()
        self._guided_measurement_active = True
        self._guided_create_telemetry = bool(self.combine_stability_telemetry_var.get())
        self.activity_var.set(
            f"Подготовка {self.station_var.get()} · {pixel_id}: "
            f"фото до измерения (камера {camera_session_dir.name})"
        )
        self._update_buttons()
        self._stop_local_stream()
        self._create_and_download(
            "Фото до измерения",
            "Фото до измерения сохранено",
            "photo_before",
            after_complete=lambda path: self._guided_before_photo_complete(
                path, station_key, pixel_id, measurement
            ),
            on_error=self._guided_measurement_failed,
        )

    def _guided_before_photo_complete(
        self,
        photo_path: Path,
        station_key: str,
        pixel_id: str,
        measurement: Callable[[], Optional[Dict[str, Any]]],
    ) -> None:
        station = SERIES_CAMERA_STATIONS[camera_station_key(station_key)]
        should_continue = ask_workflow_continue(
            self,
            f"{station['label']} · {pixel_id}",
            "Фото до измерения сохранено. Проверьте положение образца и изображение.\n\n"
            "Нажмите «Далее», чтобы автоматически начать видео и измерение, "
            "или «Отмена», чтобы завершить сценарий.",
            image_path=photo_path,
        )
        if not should_continue:
            self._finish_guided_measurement("Автоматический запуск отменён после начального фото.")
            return
        self.activity_var.set(f"Запуск видео · {station['label']} · {pixel_id}")
        self.start_recording(
            after_started=lambda: self._guided_recording_started(station_key, pixel_id, measurement),
            on_error=self._guided_measurement_failed,
        )

    def _guided_recording_started(
        self,
        station_key: str,
        pixel_id: str,
        measurement: Callable[[], Optional[Dict[str, Any]]],
    ) -> None:
        station = SERIES_CAMERA_STATIONS[camera_station_key(station_key)]
        self.activity_var.set(f"Идёт измерение {station['label']} · {pixel_id}")
        try:
            result = measurement()
        except Exception as exc:
            self.app.log(f"Ошибка автоматического измерения: {exc}")
            messagebox.showerror("Измерение", str(exc), parent=self)
            result = None
        if result is None:
            self._log("Измерение не завершено; автоматическая запись будет остановлена.")
        if camera_station_key(station_key) == "stability" and stability_current_limit_reached(result):
            now_monotonic = time.monotonic()
            session = self.app.measurement_session_for_interval(
                "STABILITY",
                pixel_id,
                self._recording_started_monotonic or now_monotonic,
                now_monotonic,
            )
            postroll_s = stability_postroll_remaining_s(result, session, now_monotonic)
            self.activity_var.set(
                f"Лимит тока: видеозапись ещё {postroll_s:.1f} с"
            )
            self._log(
                f"Стабильность достигла лимита тока; до пятисекундной отметки post-roll "
                f"осталось {postroll_s:.1f} с."
            )
            if postroll_s > 0:
                self.after(
                    max(1, int(round(postroll_s * 1000))),
                    lambda: self._guided_stop_recording(result),
                )
            else:
                self._guided_stop_recording(result)
        else:
            self._guided_stop_recording(result)

    def _guided_stop_recording(self, measurement_result: Optional[Dict[str, Any]]) -> None:
        if self._closed or not self._guided_measurement_active:
            return
        self.activity_var.set("Остановка и скачивание видео")
        self.stop_recording(
            after_stopped=lambda path: self._guided_video_complete(path, measurement_result),
            on_error=self._guided_measurement_failed,
        )

    def _guided_video_complete(
        self,
        video_path: Path,
        measurement_result: Optional[Dict[str, Any]],
    ) -> None:
        station_key = camera_station_key(self.station_var.get())
        workbook_path = (measurement_result or {}).get("file")
        if station_key == "stability" and workbook_path and self._guided_create_telemetry:
            self.activity_var.set("Добавление показаний стабильности в копию видео")
            self._run_async(
                "Создание видео с показаниями",
                lambda: create_stability_telemetry_video(video_path, Path(workbook_path)),
                lambda telemetry_path: self._guided_telemetry_complete(measurement_result, telemetry_path),
                quiet=True,
                failed=lambda exc: self._guided_telemetry_failed(measurement_result, exc),
            )
            return
        if station_key == "stability" and workbook_path:
            self._log("Объединение видео с показаниями отключено; сохранено исходное видео.")
        self._guided_video_prompt(measurement_result)

    def _guided_telemetry_complete(
        self,
        measurement_result: Optional[Dict[str, Any]],
        telemetry_path: Path,
    ) -> None:
        self._log(f"Видео с показаниями стабильности сохранено: {telemetry_path}")
        self._guided_video_prompt(measurement_result)

    def _guided_telemetry_failed(
        self,
        measurement_result: Optional[Dict[str, Any]],
        exc: Exception,
    ) -> None:
        messagebox.showwarning(
            "Видео с показаниями",
            "Исходное видео сохранено, но создать копию с показаниями не удалось.\n\n"
            f"{camera_error_dialog_text(exc)}",
            parent=self,
        )
        self._guided_video_prompt(measurement_result)

    def _guided_video_prompt(self, measurement_result: Optional[Dict[str, Any]]) -> None:
        station = SERIES_CAMERA_STATIONS[camera_station_key(self.station_var.get())]
        pixel_id = self.pixel_var.get().strip()
        should_continue = ask_workflow_continue(
            self,
            f"{station['label']} завершена · {pixel_id}",
            "Измерение завершено, видео остановлено и скачано.\n\n"
            "Нажмите «Далее», чтобы сделать контрольное фото после измерения, "
            "или «Отмена», чтобы завершить без финального фото.",
        )
        if not should_continue:
            self._finish_guided_measurement("Сценарий завершён без финального фото.")
            return
        self.activity_var.set(f"Контрольное фото после {station['label']} · {pixel_id}")
        self._stop_local_stream()
        self._create_and_download(
            "Фото после измерения",
            "Фото после измерения сохранено",
            "photo_after",
            after_complete=lambda _path: self._finish_guided_measurement(
                "Автоматический сценарий камеры и измерения завершён."
            ),
            on_error=self._guided_measurement_failed,
        )

    def _guided_measurement_failed(self, exc: Exception) -> None:
        self._finish_guided_measurement(f"Автоматический сценарий остановлен: {exc}")

    def _finish_guided_measurement(self, message: str) -> None:
        self._guided_measurement_active = False
        self._guided_create_telemetry = False
        self.activity_var.set("Готово")
        self._log(message)
        self._update_buttons()
        self.refresh_files(silent=True)
        self._series_capture_dir = None
        self._series_capture_context = None

    def _record_series_camera_file(
        self,
        local: Path,
        kind: str,
        remote: RemoteFile,
        extra_params: Optional[Dict[str, Any]] = None,
        capture_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.series_bound or self.app.series is None:
            return
        capture_context = capture_context or {}
        pixel_id = str(capture_context.get("pixel_id") or self.pixel_var.get().strip())
        if not pixel_id:
            return
        station_key = str(capture_context.get("station_key") or camera_station_key(self.station_var.get()))
        station = SERIES_CAMERA_STATIONS[station_key]
        params = {
            "station": station_key,
            "station_label": station["label"],
            "pixel_id": pixel_id,
            "media_kind": kind,
            "remote_file": remote.name,
        }
        if extra_params:
            params.update(extra_params)
        self.app.series.journal.update_after_measurement(
            station["journal_type"],
            pixel_id,
            "CAPTURED",
            Path(local),
            params,
            notes="Съёмка камеры, привязанная к станции и пикселю",
        )
        self.app.log(f"Камера: {local.name} привязан к {station['label']} / {pixel_id}")

    def _write_video_measurement_timeline(self, local: Path) -> Dict[str, Any]:
        if not self.series_bound or self.app.series is None:
            return {}
        video_start = self._recording_started_monotonic
        video_end = self._recording_stop_requested_monotonic or time.monotonic()
        if video_start is None:
            return {"measurement_sync": False, "sync_error": "Не зафиксировано время начала видео"}
        video_end = max(video_end, video_start)
        pixel_id = str(self._recording_context.get("pixel_id") or self.pixel_var.get().strip())
        station_key = str(
            self._recording_context.get("station_key") or camera_station_key(self.station_var.get())
        )
        station = SERIES_CAMERA_STATIONS[station_key]
        session = self.app.measurement_session_for_interval(
            station["measurement_type"], pixel_id, video_start, video_end
        )
        duration = video_end - video_start
        sync_path = local.with_name(f"{local.stem}_sync.json")
        timeline_path = local.with_name(f"{local.stem}_timeline.csv")
        metadata: Dict[str, Any] = {
            "measurement_sync": session is not None,
            "video_duration_clock_s": duration,
            "sync_file": sync_path.name,
            "timeline_file": timeline_path.name,
            "station": station_key,
            "pixel_id": pixel_id,
        }
        if session is not None:
            offset = video_start - float(session["started_monotonic"])
            synced_events = []
            for event in session.get("events") or []:
                measurement_time = float(event.get("measurement_time_s") or 0.0)
                video_time = measurement_time - offset
                if 0.0 <= video_time <= duration:
                    synced_event = dict(event)
                    synced_event["video_time_s"] = video_time
                    synced_events.append(synced_event)
            metadata.update(
                {
                    "measurement_type": session["measurement_type"],
                    "measurement_started_at": session["started_at"],
                    "measurement_time_at_video_start_s": offset,
                    "mapping": "measurement_time_s = video_time_s + measurement_time_at_video_start_s",
                    "events": synced_events,
                }
            )
        else:
            offset = None
            metadata["sync_error"] = "Пересекающийся сеанс измерения для станции и пикселя не найден"

        sync_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        with timeline_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["video_time_s", "measurement_time_s", "measurement_type", "pixel_id", "event"])
            timeline_points = {float(second): "" for second in range(int(duration) + 1)}
            if not timeline_points or not math.isclose(max(timeline_points), duration, abs_tol=1e-6):
                timeline_points[duration] = ""
            for event in metadata.get("events") or []:
                timeline_points[float(event["video_time_s"])] = str(event.get("label") or event.get("event") or "")
            for video_time in sorted(timeline_points):
                writer.writerow(
                    [
                        round(video_time, 3),
                        round(video_time + offset, 3) if offset is not None else "",
                        session["measurement_type"] if session is not None else "",
                        pixel_id,
                        timeline_points[video_time],
                    ]
                )
        self._log(
            f"Временная шкала видео сохранена: {timeline_path.name}"
            + ("" if session is not None else " (измерение для синхронизации не найдено)")
        )
        return metadata

    def _require_client(self, show_error: bool = True) -> Optional[CameraClient]:
        if self.client:
            return self.client
        if show_error:
            messagebox.showwarning("Камера", "Сначала подключитесь к сервису Raspberry Pi.", parent=self)
        return None

    def _close(self) -> None:
        self.shutdown_for_app_close()

    def shutdown_for_app_close(self) -> bool:
        """Stop local camera activity before either this window or the root closes."""

        if self._closed:
            return True
        if self.status.get("recording_active"):
            messagebox.showwarning(
                "Камера",
                "Сначала остановите запись видео, чтобы MP4 был корректно закрыт.",
                parent=self,
            )
            self.lift()
            return False
        self._shutdown_window(destroy_window=True)
        return True

    def _on_destroy(self, event) -> None:
        if event.widget is self and not self._closed:
            self._shutdown_window(destroy_window=False)

    def _shutdown_window(self, destroy_window: bool) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_local_stream()
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
        if self._frame_after_id is not None:
            try:
                self.after_cancel(self._frame_after_id)
            except tk.TclError:
                pass
        client = (
            self.client
            if bool(self.status.get("liveview_active"))
            else None
        )
        wifi_session = self._wifi_session
        self._wifi_session = None
        camera_settings = dict(
            self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        )
        should_restore_wifi = bool(
            wifi_session is not None
            and camera_settings.get("restore_previous_wifi", True)
        )
        if client or should_restore_wifi:
            threading.Thread(
                target=self._stop_remote_and_restore_quietly,
                args=(
                    client,
                    wifi_session if should_restore_wifi else None,
                    float(camera_settings.get("wifi_connect_timeout_s", 25.0)),
                ),
                name="camera-shutdown",
                daemon=False,
            ).start()
        if getattr(self.app, "_camera_test_window", None) is self:
            self.app._camera_test_window = None
        if destroy_window:
            try:
                self.destroy()
            except tk.TclError:
                pass

    @staticmethod
    def _stop_remote_and_restore_quietly(
        client: Optional[CameraClient],
        wifi_session: Optional[WifiConnectionSession],
        timeout_s: float,
    ) -> None:
        if client is not None:
            try:
                client.stop_liveview()
            except Exception:
                pass
        if wifi_session is not None:
            try:
                WindowsWifiController().restore(
                    wifi_session,
                    timeout_s=timeout_s,
                )
            except Exception:
                pass

    def _log(self, text: str) -> None:
        self.activity_var.set(str(text))

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if value < 1024 or unit == "ГБ":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} Б"


def open_camera_test_window(app, context: str = "free") -> Optional[CameraTestWindow]:
    """Open one non-modal camera window in free or series-bound mode."""

    if Image is None or ImageTk is None:
        messagebox.showerror(
            "Камера",
            "Для LiveView требуется Pillow. Обновите зависимости командой:\n"
            "python -m pip install -r requirements.txt",
            parent=app,
        )
        return None

    existing = getattr(app, "_camera_test_window", None)
    if existing is not None and existing.winfo_exists():
        if getattr(existing, "camera_context", "free") == context:
            existing.lift()
            existing.focus_force()
            return existing
        if not existing.shutdown_for_app_close():
            return existing
    if context == "series" and app.series is None:
        messagebox.showwarning("Камера серии", "Сначала откройте серию.", parent=app)
        return None
    window = CameraTestWindow(app, context=context)
    app._camera_test_window = window
    return window
