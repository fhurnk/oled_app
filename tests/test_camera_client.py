from __future__ import annotations

import queue
import threading
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from oled_app.camera.client import (
    CameraClient,
    CameraClientError,
    RemoteFile,
    available_path,
    build_camera_service_url,
    extract_jpeg_frames,
    normalize_center_crop,
    safe_local_filename,
    safe_capture_stem,
)
from oled_app.gui.camera_window import (
    CameraTestWindow,
    build_series_capture_stem,
    camera_error_dialog_text,
    center_crop_dimensions,
    decode_liveview_frame,
    first_available_video_control,
    free_camera_date_folder,
    load_local_photo_preview,
    stability_current_limit_reached,
    stability_postroll_remaining_s,
)
from oled_app.gui.widgets import calculate_window_geometry
from oled_app.series.paths import ensure_camera_session_folder, ensure_measurement_folder


class CameraClientHelpersTests(unittest.TestCase):
    def test_video_control_is_hidden_when_camera_has_no_selectable_choices(self) -> None:
        self.assertEqual(first_available_video_control([]), {})
        self.assertEqual(
            first_available_video_control([{"path": "/main/movie", "choices": []}]),
            {},
        )

    def test_video_control_uses_first_selectable_camera_setting(self) -> None:
        available = {
            "path": "/main/movie/fps",
            "choices": ["24", "25"],
            "current": "24",
        }

        selected = first_available_video_control(
            [
                {"path": "/main/movie/quality", "choices": []},
                available,
            ]
        )

        self.assertIs(selected, available)

    def test_camera_error_dialog_includes_service_details(self) -> None:
        error = CameraClientError(
            "Камера не сохранила параметр «ISO».",
            "CAMERA_SETTING_FAILED",
            "ISO: запрошено 1000; установлено 800",
        )

        self.assertEqual(
            camera_error_dialog_text(error),
            "Камера не сохранила параметр «ISO».\n\nISO: запрошено 1000; установлено 800",
        )

    def test_free_camera_downloads_are_grouped_by_capture_date(self) -> None:
        folder = free_camera_date_folder(Path("camera_downloads"), date(2026, 7, 21))
        self.assertEqual(folder, Path("camera_downloads") / "2026-07-21")

    def test_downloaded_photo_preview_is_loaded_and_fitted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "captured.jpg"
            Image.new("RGB", (1600, 1200), "navy").save(path)
            preview = load_local_photo_preview(path, (800, 500))

        self.assertEqual(preview.size, (667, 500))

    def test_downloaded_photo_preview_decodes_from_memory_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "full-size.jpg"
            Image.new("RGB", (2400, 1600), "navy").save(path)
            real_open = Image.open

            def fail_for_lazy_path(source, *args, **kwargs):
                if isinstance(source, (str, Path)):
                    raise OSError(22, "Invalid argument")
                return real_open(source, *args, **kwargs)

            with patch("oled_app.gui.camera_window.Image.open", side_effect=fail_for_lazy_path):
                preview = load_local_photo_preview(path, (840, 500))

        self.assertEqual(preview.size, (750, 500))

    def test_downloaded_photo_preview_retries_transient_onedrive_read_error(self) -> None:
        encoded = BytesIO()
        Image.new("RGB", (1600, 1200), "navy").save(encoded, format="JPEG")

        with (
            patch.object(
                Path,
                "read_bytes",
                side_effect=[OSError(22, "Invalid argument"), encoded.getvalue()],
            ),
            patch("oled_app.gui.camera_window.time.sleep") as sleep,
        ):
            preview = load_local_photo_preview(Path("captured.jpg"), (800, 500))

        self.assertEqual(preview.size, (667, 500))
        sleep.assert_called_once_with(0.05)

    def test_queued_liveview_frame_does_not_replace_captured_photo(self) -> None:
        frames = queue.Queue()
        frames.put(b"stale-liveview-frame")
        rendered = []
        scheduled = []
        camera = SimpleNamespace(
            _closed=False,
            _frame_queue=frames,
            _photo_preview_active=True,
            _last_frame=None,
            _render_frame=lambda frame: rendered.append(frame),
            after=lambda delay, callback: scheduled.append((delay, callback)),
            _frame_after_id=None,
            _consume_frames=lambda: None,
        )

        CameraTestWindow._consume_frames(camera)

        self.assertEqual(rendered, [])
        self.assertTrue(frames.empty())
        self.assertEqual(scheduled[0][0], 50)

    def test_guided_before_photo_passes_image_to_confirmation_dialog(self) -> None:
        finished = []
        camera = SimpleNamespace(_finish_guided_measurement=lambda message: finished.append(message))
        photo_path = Path("captured.jpg")

        with patch("oled_app.gui.camera_window.ask_workflow_continue", return_value=False) as ask:
            CameraTestWindow._guided_before_photo_complete(
                camera,
                photo_path,
                "ivl",
                "CG1_1_1",
                lambda: None,
            )

        self.assertEqual(ask.call_args.kwargs["image_path"], photo_path)
        self.assertEqual(len(finished), 1)

    def test_camera_sessions_use_separate_numbered_measurement_folder(self) -> None:
        pixel_row = {
            "Quarter number": 1,
            "Quarter code": "CG",
            "Substrate number": 1,
        }
        with tempfile.TemporaryDirectory() as folder:
            first = ensure_camera_session_folder(Path(folder), "CG1_1_1", pixel_row)
            second = ensure_camera_session_folder(Path(folder), "CG1_1_1", pixel_row)

        self.assertEqual(first.parts[-6], "04_CAMERA")
        self.assertEqual(first.parts[-4:], ("CG1", "CG1_1", "CG1_1_1", "1"))
        self.assertEqual(first.name, "1")
        self.assertEqual(second.name, "2")

    def test_camera_session_number_continues_after_legacy_camera_folder(self) -> None:
        pixel_row = {
            "Quarter number": 1,
            "Quarter code": "CG",
            "Substrate number": 1,
        }
        with tempfile.TemporaryDirectory() as folder:
            pixel_root = ensure_measurement_folder(
                Path(folder),
                "CAMERA",
                "CG1_1_1",
                pixel_row,
            )
            legacy_session = pixel_root / "camera" / "3"
            legacy_session.mkdir(parents=True)

            new_session = ensure_camera_session_folder(
                Path(folder),
                "CG1_1_1",
                pixel_row,
            )

            self.assertEqual(new_session, pixel_root / "4")
            self.assertTrue(legacy_session.is_dir())

    def test_series_camera_opens_selected_measurement_with_selected_pixel(self) -> None:
        class Value:
            def __init__(self, value: str):
                self.value = value

            def get(self) -> str:
                return self.value

        app = SimpleNamespace(series=object())
        camera = SimpleNamespace(
            series_bound=True,
            app=app,
            client=object(),
            status={"camera_connected": True, "recording_active": False},
            _busy=False,
            _guided_measurement_active=False,
            pixel_var=Value("CR1_2_3"),
            station_var=Value("ВАЯХ"),
            run_guided_measurement=lambda *_args: None,
        )

        with patch("oled_app.gui.camera_window.open_ivl_window") as open_ivl:
            CameraTestWindow.open_selected_measurement(camera)
        open_ivl.assert_called_once_with(
            app,
            initial_pixel="CR1_2_3",
            parent=camera,
            locked_pixel=True,
            measurement_runner=camera.run_guided_measurement,
        )

        camera.station_var = Value("Стабильность")
        with patch("oled_app.gui.camera_window.open_stability_window") as open_stability:
            CameraTestWindow.open_selected_measurement(camera)
        open_stability.assert_called_once_with(
            app,
            initial_pixel="CR1_2_3",
            parent=camera,
            locked_pixel=True,
            measurement_runner=camera.run_guided_measurement,
        )

    def test_stability_current_limit_event_enables_camera_postroll(self) -> None:
        self.assertTrue(
            stability_current_limit_reached(
                {"events": [{"event": "current_limit_or_breakdown", "measurement_time_s": 2.0}]}
            )
        )
        self.assertFalse(stability_current_limit_reached({"events": []}))

    def test_guided_stability_waits_five_seconds_before_stopping_video(self) -> None:
        scheduled = []
        stopped = []
        camera = SimpleNamespace(
            activity_var=SimpleNamespace(set=lambda value: None),
            app=SimpleNamespace(
                log=lambda _message: None,
                measurement_session_for_interval=lambda *_args: None,
            ),
            _recording_started_monotonic=100.0,
            _log=lambda _message: None,
            after=lambda delay, callback: scheduled.append((delay, callback)),
            _guided_stop_recording=lambda result: stopped.append(result),
        )
        result = {"events": [{"event": "current_limit_or_breakdown"}]}

        CameraTestWindow._guided_recording_started(
            camera,
            "stability",
            "CR1_2_3",
            lambda: result,
        )

        self.assertEqual(stopped, [])
        self.assertEqual(scheduled[0][0], 5000)
        scheduled[0][1]()
        self.assertEqual(stopped, [result])

    def test_stability_postroll_is_measured_from_limit_event(self) -> None:
        result = {
            "events": [
                {"event": "current_limit_or_breakdown", "measurement_time_s": 12.0}
            ]
        }
        session = {"started_monotonic": 100.0}

        self.assertEqual(stability_postroll_remaining_s(result, session, 114.0), 3.0)
        self.assertEqual(stability_postroll_remaining_s(result, session, 118.0), 0.0)

    def test_series_capture_name_starts_with_pixel_and_contains_station(self) -> None:
        stem = build_series_capture_stem("CR1_2_3", "stability", "video", "пробой", "20260717_120000")

        self.assertTrue(stem.startswith("CR1_2_3_stability_video_"))
        self.assertTrue(stem.endswith("20260717_120000"))

    def test_window_geometry_is_clamped_to_small_screen(self) -> None:
        width, height, x, y = calculate_window_geometry(1280, 720, 1600, 1000, 900, 620)

        self.assertEqual((width, height), (1220, 640))
        self.assertEqual((x, y), (30, 40))

    def test_extracts_multiple_jpegs_and_keeps_partial_tail(self) -> None:
        first = b"\xff\xd8" + b"a" * 128 + b"\xff\xd9"
        second = b"\xff\xd8" + b"b" * 128 + b"\xff\xd9"
        buffer = bytearray(b"multipart header\r\n" + first + second + b"\xff\xd8partial")

        self.assertEqual(list(extract_jpeg_frames(buffer)), [first, second])
        self.assertEqual(buffer, bytearray(b"\xff\xd8partial"))

    def test_url_builder_accepts_plain_host_and_existing_scheme(self) -> None:
        self.assertEqual(build_camera_service_url("192.168.4.1", 8765), "http://192.168.4.1:8765")
        self.assertEqual(build_camera_service_url("http://raspberrypi.local:9000/", 8765), "http://raspberrypi.local:9000")

    def test_filename_is_reduced_to_safe_basename(self) -> None:
        self.assertEqual(safe_local_filename("../bad:name?.jpg"), "bad_name_.jpg")

    def test_custom_capture_name_is_safe_and_extensionless(self) -> None:
        self.assertEqual(safe_capture_stem("  Образец: 12?.JPG  "), "Образец_ 12")
        self.assertEqual(safe_capture_stem(""), "")
        self.assertEqual(safe_capture_stem("/"), "")

    def test_liveview_frame_is_fully_loaded_and_resized(self) -> None:
        source = Image.new("RGB", (1024, 680), "red")
        encoded = BytesIO()
        source.save(encoded, format="JPEG")

        decoded = decode_liveview_frame(encoded.getvalue(), (320, 240))

        self.assertEqual(decoded.mode, "RGB")
        self.assertEqual(decoded.size, (320, 213))
        self.assertIsNone(getattr(decoded, "fp", None))

    def test_liveview_frame_is_center_cropped_before_resize(self) -> None:
        source = Image.new("RGB", (1024, 680), "red")
        encoded = BytesIO()
        source.save(encoded, format="JPEG")

        crop = {"width_percent": 50, "height_percent": 75}
        decoded = decode_liveview_frame(encoded.getvalue(), (1024, 680), crop)

        self.assertEqual(center_crop_dimensions(1024, 680, crop), (512, 510))
        self.assertEqual(decoded.size, (683, 680))
        self.assertEqual(normalize_center_crop(crop), {"width_percent": 50.0, "height_percent": 75.0})
        self.assertEqual(center_crop_dimensions(1023, 679), (1023, 679))
        self.assertEqual(
            normalize_center_crop({"width_percent": "70,5", "height_percent": "99,5"}),
            {"width_percent": 70.5, "height_percent": 99.5},
        )

    def test_liveview_frame_upscales_until_one_side_fills_canvas(self) -> None:
        source = Image.new("RGB", (818, 544), "red")
        encoded = BytesIO()
        source.save(encoded, format="JPEG")

        decoded = decode_liveview_frame(encoded.getvalue(), (1080, 900))

        self.assertEqual(decoded.size, (1080, 718))

    def test_active_liveview_response_can_be_closed_from_another_thread(self) -> None:
        response = BlockingResponse()
        client = CameraClient("http://camera.test:8765")
        stop_event = threading.Event()
        failures: list[Exception] = []

        def receive() -> None:
            try:
                client.iter_liveview_frames(stop_event, lambda _frame: None)
            except Exception as exc:  # pragma: no cover - assertion reports the actual failure
                failures.append(exc)

        with patch("urllib.request.urlopen", return_value=response):
            thread = threading.Thread(target=receive)
            thread.start()
            self.assertTrue(response.read_started.wait(1.0))
            stop_event.set()
            client.close_liveview_stream()
            thread.join(1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        client.close_liveview_stream()

    def test_quality_payloads_and_delete_endpoint(self) -> None:
        client = CameraClient("http://camera.test:8765")
        remote_payload = {
            "file": {"file_id": "abc 123", "name": "photo.jpg", "kind": "photo", "size": 10}
        }
        video_settings = {"/main/capturesettings/moviefps": "25"}
        photo_settings = {
            "/main/imgsettings/imageformat": "Large Fine JPEG",
            "/main/imgsettings/iso": "400",
            "/main/capturesettings/shutterspeed": "1/4",
        }
        with patch.object(
            client,
            "_json_request",
            side_effect=[remote_payload, {"success": True}, {"success": True}, {"success": True}],
        ) as request:
            remote = client.capture_photo(photo_settings)
            client.start_liveview(video_settings)
            client.start_recording(video_settings)
            client.delete_file(RemoteFile("abc 123", "photo.jpg", "photo", 10))

        self.assertEqual(remote.file_id, "abc 123")
        self.assertEqual(
            request.call_args_list[0].args,
            (
                "POST",
                "/api/photo/capture",
                {
                    "photo_settings": photo_settings,
                    "crop": {"width_percent": 100.0, "height_percent": 100.0},
                },
            ),
        )
        self.assertEqual(request.call_args_list[1].args, ("POST", "/api/liveview/start", {"video_settings": video_settings}))
        self.assertEqual(
            request.call_args_list[2].args,
            (
                "POST",
                "/api/video/start",
                {
                    "video_settings": video_settings,
                    "crop": {"width_percent": 100.0, "height_percent": 100.0},
                },
            ),
        )
        self.assertEqual(request.call_args_list[3].args, ("DELETE", "/api/files/abc%20123"))

    def test_selected_photo_settings_merge_quality_and_exposure(self) -> None:
        class Value:
            def __init__(self, value: str):
                self.value = value

            def get(self) -> str:
                return self.value

        camera = SimpleNamespace(
            photo_quality_vars={"/main/imgsettings/imageformat": Value("Large Fine JPEG")},
            photo_exposure_vars={
                "/main/imgsettings/iso": Value("800"),
                "/main/capturesettings/shutterspeed": Value("1/2"),
            },
        )

        selected = CameraTestWindow._selected_photo_settings(camera)

        self.assertEqual(
            selected,
            {
                "/main/imgsettings/imageformat": "Large Fine JPEG",
                "/main/imgsettings/iso": "800",
                "/main/capturesettings/shutterspeed": "1/2",
            },
        )

    def test_liveview_stop_uses_timeout_longer_than_server_shutdown(self) -> None:
        client = CameraClient("http://camera.test:8765", timeout_s=8.0, stream_timeout_s=12.0)
        with patch.object(client, "_json_request", return_value={"success": True}) as request:
            client.stop_liveview()

        self.assertEqual(request.call_args.args, ("POST", "/api/liveview/stop"))
        self.assertGreaterEqual(request.call_args.kwargs["timeout_s"], 30.0)

    def test_custom_name_is_sent_for_preview_and_photo(self) -> None:
        client = CameraClient("http://camera.test:8765")
        payload = {"file": {"file_id": "1", "name": "sample.jpg", "kind": "photo", "size": 10}}
        with patch.object(client, "_json_request", return_value=payload) as request:
            client.save_liveview_snapshot("sample")
            client.capture_photo({}, "sample")

        self.assertEqual(
            request.call_args_list[0].args,
            (
                "POST",
                "/api/liveview/snapshot",
                {
                    "file_name": "sample",
                    "crop": {"width_percent": 100.0, "height_percent": 100.0},
                },
            ),
        )
        self.assertEqual(
            request.call_args_list[1].args,
            (
                "POST",
                "/api/photo/capture",
                {
                    "photo_settings": {},
                    "file_name": "sample",
                    "crop": {"width_percent": 100.0, "height_percent": 100.0},
                },
            ),
        )


class BlockingResponse:
    def __init__(self) -> None:
        self.read_started = threading.Event()
        self.closed = threading.Event()

    def read(self, _size: int) -> bytes:
        self.read_started.set()
        self.closed.wait(2.0)
        raise OSError("stream closed")

    def close(self) -> None:
        self.closed.set()


if __name__ == "__main__":
    unittest.main()
