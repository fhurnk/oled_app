"""Alpha camera test window; intentionally independent from OLED measurements."""

from __future__ import annotations

import io
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Dict, Optional

try:
    from PIL import Image, ImageTk
except ImportError:  # Keep the rest of the OLED application launchable before dependencies are updated.
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

from oled_app.camera import CameraClient, CameraClientError, RemoteFile, build_camera_service_url
from oled_app.constants import SCRIPT_DIR
from oled_app.settings import DEFAULT_APP_SETTINGS, save_app_settings


def decode_liveview_frame(frame: bytes, max_size: tuple[int, int]):
    """Fully decode and resize one JPEG before handing it to Tk."""

    if Image is None:
        raise RuntimeError("Для LiveView требуется Pillow.")
    width = max(1, min(int(max_size[0]), 8192))
    height = max(1, min(int(max_size[1]), 8192))
    with io.BytesIO(frame) as stream:
        with Image.open(stream) as source:
            source.load()
            image = source.convert("RGB")
    if image.width <= width and image.height <= height:
        return image
    original = image.copy()
    try:
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
    except OSError:
        image = original
        image.thumbnail((width, height), Image.Resampling.BILINEAR)
    return image


class CameraTestWindow(tk.Toplevel):
    """Manual validation UI for the Raspberry Pi camera service."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Тест камеры Canon — v1.8 alpha")
        self.geometry("1180x860")
        self.minsize(900, 620)
        self.transient(app)

        settings = app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        self.host_var = tk.StringVar(value=str(settings.get("host", "192.168.4.1")))
        self.port_var = tk.StringVar(value=str(settings.get("port", 8765)))
        self.state_var = tk.StringVar(value="Не подключено")
        self.model_var = tk.StringVar(value="—")
        self.fps_var = tk.StringVar(value="0.0 кадр/с")
        self.disk_var = tk.StringVar(value="—")
        self.file_var = tk.StringVar(value="—")
        self.error_var = tk.StringVar(value="—")
        self.video_source_var = tk.StringVar(value="LiveView: разрешение появится после первого кадра")
        self.video_quality_var = tk.StringVar(value="Камера не подключена")
        self.video_fps_var = tk.StringVar(value="Камера не подключена")
        self.keep_remote_var = tk.BooleanVar(value=bool(settings.get("keep_remote_files_after_download", True)))

        self.client: Optional[CameraClient] = None
        self.status: Dict[str, Any] = {}
        self.capability_data: Dict[str, Any] = {}
        self.remote_files: Dict[str, RemoteFile] = {}
        self.photo_quality_vars: Dict[str, tk.StringVar] = {}
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
        self._last_render_error = ""
        self._render_error_count = 0
        self._poll_after_id = None
        self._frame_after_id = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._frame_after_id = self.after(50, self._consume_frames)
        self._update_buttons()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Тест камеры Canon", font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(
            header,
            text="ALPHA · без связи с измерениями",
            foreground="#9A6700",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(12, 0))

        connection = ttk.LabelFrame(outer, text="Сервис на Raspberry Pi", padding=8)
        connection.pack(fill="x", pady=(10, 10))
        ttk.Label(connection, text="IP / имя:").grid(row=0, column=0, sticky="w")
        ttk.Entry(connection, textvariable=self.host_var, width=28).grid(row=0, column=1, padx=(6, 12), sticky="we")
        ttk.Label(connection, text="Порт:").grid(row=0, column=2, sticky="w")
        ttk.Entry(connection, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=(6, 12))
        self.connect_button = ttk.Button(connection, text="Подключиться", command=self.connect)
        self.connect_button.grid(row=0, column=4, padx=(0, 8))
        self.initialize_button = ttk.Button(connection, text="Переинициализировать камеру", command=self.initialize_camera)
        self.initialize_button.grid(row=0, column=5)
        connection.columnconfigure(1, weight=1)

        content = ttk.Panedwindow(outer, orient="horizontal")
        content.pack(fill="both", expand=True)

        preview_frame = ttk.LabelFrame(content, text="LiveView", padding=6)
        controls_frame = ttk.Frame(content, padding=(10, 0, 0, 0))
        content.add(preview_frame, weight=3)
        content.add(controls_frame, weight=2)

        self.preview_canvas = tk.Canvas(preview_frame, background="#111111", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.create_text(
            320,
            220,
            text="Подключитесь к сервису и запустите LiveView",
            fill="#DDDDDD",
            font=("Segoe UI", 12),
            tags=("placeholder",),
        )

        status_box = ttk.LabelFrame(controls_frame, text="Состояние", padding=8)
        status_box.pack(fill="x")
        rows = [
            ("Состояние:", self.state_var),
            ("Модель:", self.model_var),
            ("LiveView FPS:", self.fps_var),
            ("Свободно на Pi:", self.disk_var),
            ("Текущий файл:", self.file_var),
            ("Последняя ошибка:", self.error_var),
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
        video_row = ttk.Frame(quality_box)
        video_row.pack(fill="x", pady=(6, 0))
        ttk.Label(video_row, text="Качество/размер камеры:").pack(side="left")
        self.video_quality_combo = ttk.Combobox(
            video_row,
            textvariable=self.video_quality_var,
            state="readonly",
            width=43,
        )
        self.video_quality_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.video_quality_combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        fps_row = ttk.Frame(quality_box)
        fps_row.pack(fill="x", pady=(6, 0))
        ttk.Label(fps_row, text="Кадров в секунду камеры:").pack(side="left")
        self.video_fps_combo = ttk.Combobox(
            fps_row,
            textvariable=self.video_fps_var,
            state="readonly",
            width=43,
        )
        self.video_fps_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.video_fps_combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        ttk.Label(quality_box, textvariable=self.video_source_var, foreground="#555555", wraplength=360).pack(
            anchor="w", pady=(5, 0)
        )
        ttk.Checkbutton(
            quality_box,
            text="Оставлять файл на Raspberry Pi после скачивания",
            variable=self.keep_remote_var,
            command=self._save_quality_preferences,
        ).pack(anchor="w", pady=(6, 0))

        actions = ttk.LabelFrame(controls_frame, text="Ручная проверка функций", padding=8)
        actions.pack(fill="x", pady=(10, 0))
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
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        files_box = ttk.LabelFrame(controls_frame, text="Файлы на Raspberry Pi", padding=8)
        files_box.pack(fill="both", expand=True, pady=(10, 0))
        self.files_tree = ttk.Treeview(files_box, columns=("type", "size", "date"), show="tree headings", height=7)
        self.files_tree.heading("#0", text="Имя")
        self.files_tree.heading("type", text="Тип")
        self.files_tree.heading("size", text="Размер")
        self.files_tree.heading("date", text="Создан")
        self.files_tree.column("#0", width=190, stretch=True)
        self.files_tree.column("type", width=65, stretch=False)
        self.files_tree.column("size", width=80, stretch=False)
        self.files_tree.column("date", width=135, stretch=False)
        self.files_tree.pack(fill="both", expand=True)
        self.files_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())
        file_buttons = ttk.Frame(files_box)
        file_buttons.pack(fill="x", pady=(6, 0))
        self.refresh_files_button = ttk.Button(file_buttons, text="Обновить список", command=self.refresh_files)
        self.refresh_files_button.pack(side="left")
        self.download_button = ttk.Button(file_buttons, text="Скачать выбранный", command=self.download_selected)
        self.download_button.pack(side="left", padx=(6, 0))
        ttk.Button(file_buttons, text="Открыть папку", command=self.open_download_folder).pack(side="right")

        self.log_widget = ScrolledText(outer, height=6, state="disabled")
        self.log_widget.pack(fill="x", pady=(10, 0))
        self._log("Окно камеры работает отдельно от измерений OLED.")

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
            self.client.health()
            status = self.client.status()
            if not status.get("camera_connected"):
                status = self.client.initialize()
            return status, self._capabilities_or_legacy(self.client)

        self._run_async("Подключение к сервису", work, self._connected)

    def _connected(self, result) -> None:
        status, capabilities = result
        self._apply_status(status)
        self._apply_capabilities(capabilities)
        if capabilities.get("legacy_service"):
            self._log("Сервис Raspberry Pi работает по старому API: обновите его для выбора качества и удаления файлов.")
        self._log(f"Сервис камеры доступен: {self.client.base_url if self.client else ''}")
        self.refresh_files(silent=True)
        self._schedule_status_poll()

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

    def _create_and_download(self, operation_label: str, success_label: str, kind: str) -> None:
        client = self._require_client()
        if not client:
            return
        keep_remote = bool(self.keep_remote_var.get())
        photo_settings = self._selected_photo_settings()

        def work():
            remote = client.save_liveview_snapshot() if kind == "snapshot" else client.capture_photo(photo_settings)
            local = client.download_file(remote, self._download_dir())
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return remote, local, deleted, delete_error, client.status()

        def complete(result) -> None:
            _remote, local, deleted, delete_error, status = result
            self._apply_status(status)
            if status.get("liveview_active"):
                self._start_local_stream()
            self._log(f"{success_label}: {local}")
            self._log_remote_cleanup(deleted, delete_error)
            self.refresh_files(silent=True)

        self._run_async(operation_label, work, complete)

    def start_recording(self) -> None:
        client = self._require_client()
        if not client:
            return
        video_settings = self._selected_video_settings()
        self._run_async("Запуск записи", lambda: client.start_recording(video_settings), self._recording_started)

    def _recording_started(self, status: Dict[str, Any]) -> None:
        self._apply_status(status)
        self._start_local_stream()
        self._log("Запись видео началась; LiveView продолжает работать из того же потока.")

    def stop_recording(self) -> None:
        client = self._require_client()
        if not client:
            return
        keep_remote = bool(self.keep_remote_var.get())

        def work():
            remote = client.stop_recording()
            local = client.download_file(remote, self._download_dir())
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return remote, local, deleted, delete_error, client.status()

        def complete(result) -> None:
            _remote, local, deleted, delete_error, status = result
            self._apply_status(status)
            self._log(f"Видео корректно завершено и скачано: {local}")
            self._log_remote_cleanup(deleted, delete_error)
            self.refresh_files(silent=True)

        self._run_async("Остановка записи", work, complete)

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

        def work():
            local = client.download_file(remote, self._download_dir())
            deleted, delete_error = self._delete_remote_after_download(client, remote, keep_remote)
            return local, deleted, delete_error

        def complete(result) -> None:
            local, deleted, delete_error = result
            self._log(f"Файл скачан: {local}")
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
        for child in self.photo_quality_frame.winfo_children():
            child.destroy()
        self.photo_quality_vars = {}
        settings = self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        saved_photo = dict(settings.get("photo_quality_settings") or {})
        controls = list(capabilities.get("photo_controls") or [])
        if not controls:
            ttk.Label(
                self.photo_quality_frame,
                text="Камера не сообщила переключаемые JPEG-параметры; используется её текущее качество.",
                wraplength=360,
                justify="left",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
        for row, control in enumerate(controls):
            path = str(control.get("path") or "")
            choices = [str(value) for value in control.get("choices") or []]
            if not path or not choices:
                continue
            selected = str(saved_photo.get(path) or control.get("current") or choices[0])
            if selected not in choices:
                selected = str(control.get("current") or choices[0])
            variable = tk.StringVar(value=selected)
            self.photo_quality_vars[path] = variable
            ttk.Label(self.photo_quality_frame, text=str(control.get("label") or "Качество фото") + ":").grid(
                row=row, column=0, sticky="w", pady=2
            )
            combo = ttk.Combobox(
                self.photo_quality_frame,
                textvariable=variable,
                values=choices,
                state="readonly",
                width=30,
            )
            combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            combo.bind("<<ComboboxSelected>>", self._save_quality_preferences)
        self.photo_quality_frame.columnconfigure(1, weight=1)

        saved_video = dict(settings.get("video_camera_settings") or {})
        self.video_quality_control_path, self.video_quality_by_label = self._configure_video_control(
            self.video_quality_combo,
            self.video_quality_var,
            list(capabilities.get("video_quality_controls") or []),
            saved_video,
            "Камера не предоставляет выбор качества",
        )
        self.video_fps_control_path, self.video_fps_by_label = self._configure_video_control(
            self.video_fps_combo,
            self.video_fps_var,
            list(capabilities.get("video_fps_controls") or []),
            saved_video,
            "Камера не предоставляет выбор FPS",
        )
        self._update_video_source_text()
        self._save_quality_preferences()

    @staticmethod
    def _configure_video_control(
        combo: ttk.Combobox,
        variable: tk.StringVar,
        controls: list[Dict[str, Any]],
        saved: Dict[str, str],
        unavailable_text: str,
    ) -> tuple[str, Dict[str, str]]:
        control = controls[0] if controls else {}
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

    def _selected_photo_settings(self) -> Dict[str, str]:
        return {path: variable.get() for path, variable in self.photo_quality_vars.items() if variable.get()}

    def _selected_video_settings(self) -> Dict[str, str]:
        selected: Dict[str, str] = {}
        quality = self.video_quality_by_label.get(self.video_quality_var.get())
        fps = self.video_fps_by_label.get(self.video_fps_var.get())
        if self.video_quality_control_path and quality:
            selected[self.video_quality_control_path] = quality
        if self.video_fps_control_path and fps:
            selected[self.video_fps_control_path] = fps
        return selected

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
        settings["video_camera_settings"] = self._selected_video_settings()
        if self.photo_quality_vars:
            settings["photo_quality_settings"] = self._selected_photo_settings()
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
            self.video_source_var.set(
                f"Источник: LiveView {int(width)}×{int(height)}, сейчас {fps:.1f} кадр/с. "
                "Доступны только варианты, которые сообщила камера; FFmpeg не подменяет её FPS."
            )
        else:
            self.video_source_var.set(
                "Источник: LiveView; разрешение и фактический FPS появятся после первого кадра. "
                "Выбор доступен только для параметров, которые сообщила камера."
            )

    def _start_local_stream(self) -> None:
        if not self.client:
            return
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
        if frame:
            self._last_frame = frame
            self._render_frame(frame)
        self._frame_after_id = self.after(50, self._consume_frames)

    def _render_frame(self, frame: bytes) -> None:
        try:
            width = max(self.preview_canvas.winfo_width() - 8, 200)
            height = max(self.preview_canvas.winfo_height() - 8, 160)
            image = decode_liveview_frame(frame, (width, height))
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
        if self._closed:
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
                    self.after(0, lambda: self._apply_status(status))
            except Exception as exc:
                if not self._closed:
                    self.after(0, lambda: self.error_var.set(str(exc)))
            finally:
                if not self._closed:
                    self.after(0, self._status_poll_finished)

        threading.Thread(target=work, name="camera-status", daemon=True).start()

    def _status_poll_finished(self) -> None:
        self._status_request_running = False
        self._schedule_status_poll()

    def _run_async(
        self,
        label: str,
        work: Callable[[], Any],
        complete: Callable[[Any], None],
        quiet: bool = False,
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
                    self.after(0, lambda error=exc: self._operation_failed(label, error, quiet))
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

    def _operation_failed(self, label: str, exc: Exception, quiet: bool) -> None:
        self._busy = False
        details = exc.details if isinstance(exc, CameraClientError) else ""
        self.error_var.set(str(exc))
        self._log(f"{label}: {exc}" + (f" | {details}" if details else ""))
        if not quiet:
            messagebox.showerror("Камера", str(exc), parent=self)
        self._update_buttons()

    def _update_buttons(self) -> None:
        service_connected = self.client is not None and bool(self.status)
        camera_connected = bool(self.status.get("camera_connected"))
        live = bool(self.status.get("liveview_active"))
        recording = bool(self.status.get("recording_active"))
        normal = "normal" if not self._busy else "disabled"
        self.connect_button.configure(state=normal)
        self.initialize_button.configure(state="normal" if service_connected and not recording and not self._busy else "disabled")
        self.start_live_button.configure(state="normal" if camera_connected and not live and not self._busy else "disabled")
        self.stop_live_button.configure(state="normal" if live and not recording and not self._busy else "disabled")
        self.snapshot_button.configure(state="normal" if live and not self._busy else "disabled")
        self.photo_button.configure(state="normal" if camera_connected and not recording and not self._busy else "disabled")
        self.start_video_button.configure(state="normal" if camera_connected and not recording and not self._busy else "disabled")
        self.stop_video_button.configure(state="normal" if recording and not self._busy else "disabled")
        self.refresh_files_button.configure(state="normal" if service_connected and not self._busy else "disabled")
        self.download_button.configure(
            state="normal" if service_connected and bool(self.files_tree.selection()) and not self._busy else "disabled"
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
        settings["video_camera_settings"] = self._selected_video_settings()
        if self.photo_quality_vars:
            settings["photo_quality_settings"] = self._selected_photo_settings()
        self.app.app_settings["camera"] = settings
        save_app_settings(self.app.app_settings)

    def _download_dir(self) -> Path:
        settings = self.app.app_settings.get("camera", DEFAULT_APP_SETTINGS["camera"])
        return Path(str(settings.get("download_dir") or SCRIPT_DIR / "camera_downloads")).expanduser()

    def _require_client(self, show_error: bool = True) -> Optional[CameraClient]:
        if self.client:
            return self.client
        if show_error:
            messagebox.showwarning("Камера", "Сначала подключитесь к сервису Raspberry Pi.", parent=self)
        return None

    def _close(self) -> None:
        if self.status.get("recording_active"):
            messagebox.showwarning(
                "Камера",
                "Сначала остановите запись видео, чтобы MP4 был корректно закрыт.",
                parent=self,
            )
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
        client = self.client
        if client and self.status.get("liveview_active"):
            threading.Thread(target=self._stop_remote_quietly, args=(client,), daemon=True).start()
        self.destroy()

    @staticmethod
    def _stop_remote_quietly(client: CameraClient) -> None:
        try:
            client.stop_liveview()
        except Exception:
            pass

    def _log(self, text: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", str(text) + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if value < 1024 or unit == "ГБ":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} Б"


def open_camera_test_window(app) -> Optional[CameraTestWindow]:
    """Open one non-modal camera test window."""

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
        existing.lift()
        existing.focus_force()
        return existing
    window = CameraTestWindow(app)
    app._camera_test_window = window
    return window
