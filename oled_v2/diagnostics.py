"""Safe, copyable diagnostics for the v2 desktop shell."""

from __future__ import annotations

from datetime import datetime, timezone
import platform
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from oled_app.constants import APP_VERSION

from .config import API_SCHEMA_VERSION


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)([\"']?(?:x-oled-session|authorization|session(?:_id)?|client_id|"
        r"token|password|api[_-]?key|secret)[\"']?\s*[:=]\s*[\"']?)"
        r"([^\s\"',;&}]+)"
    ),
    re.compile(r"(?i)\bbearer\s+[^\s,;&]+"),
    re.compile(r"(?i)([#?&]session=)[^&#\s]+"),
)


def redact_diagnostic_text(value: Any) -> str:
    """Remove desktop-session secrets from user-copyable diagnostic text."""

    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        if "bearer" in pattern.pattern.lower():
            text = pattern.sub("Bearer [скрыто]", text)
        elif "session=" in pattern.pattern.lower():
            text = pattern.sub(r"\1[скрыто]", text)
        else:
            text = pattern.sub(r"\1[скрыто]", text)
    return text


def recent_log_errors(log_file: Path, limit: int = 12) -> list[str]:
    """Return a bounded, sanitized tail of warnings and errors from the local log."""

    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    selected = [
        redact_diagnostic_text(line)[:800]
        for line in lines
        if re.search(r"\b(?:WARNING|ERROR|CRITICAL)\b", line, re.IGNORECASE)
    ]
    return selected[-max(1, int(limit)) :]


def _operation(name: str, state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "status": str(state.get("status") or "idle"),
        "active": bool(state.get("active")),
        "error": redact_diagnostic_text(state.get("error")) or None,
    }


def _camera_model(camera: Mapping[str, Any]) -> Optional[str]:
    status = camera.get("camera_status")
    health = camera.get("health")
    for source in (status, health):
        if isinstance(source, Mapping):
            for key in ("model", "camera_model", "name"):
                if source.get(key):
                    return redact_diagnostic_text(source[key])
    return None


def create_diagnostics_snapshot(
    *,
    settings: Mapping[str, Any],
    hardware: Mapping[str, Any],
    series: Mapping[str, Any],
    operations: Mapping[str, Mapping[str, Any]],
    camera: Mapping[str, Any],
    settings_path: Path,
    log_dir: Path,
    backend_ready: bool,
    backend_started_at: str,
) -> dict[str, Any]:
    """Build a secret-free diagnostic payload from already available state."""

    timestamp = datetime.now(timezone.utc).isoformat()
    log_file = Path(log_dir) / "oled-v2.log"
    operation_rows = [_operation(name, state) for name, state in operations.items()]
    controller_errors = [
        f"{item['name']}: {item['error']}"
        for item in operation_rows
        if item["error"]
    ]
    camera_error = redact_diagnostic_text(camera.get("error"))
    if camera_error:
        controller_errors.append(f"Камера: {camera_error}")
    log_errors = recent_log_errors(log_file)
    recent_errors = (controller_errors + log_errors)[-12:]

    paths = {
        "settings": str(Path(settings_path)),
        "logs": str(Path(log_dir)),
        "series_root": str(series.get("root") or settings.get("default_root") or ""),
        "active_series": str(series.get("path") or "") or None,
        "simulator_ivl": str(Path(log_dir).parent / "simulator_ivl"),
        "simulator_spectrum": str(Path(log_dir).parent / "simulator_spectrum"),
        "simulator_stability": str(Path(log_dir).parent / "simulator_stability"),
    }
    camera_summary = {
        "connected": bool(camera.get("connected")),
        "initialized": bool(camera.get("initialized")),
        "model": _camera_model(camera),
        "liveview_active": bool(camera.get("liveview_active")),
        "recording_active": bool(camera.get("recording_active")),
        "message": redact_diagnostic_text(camera.get("message")),
        "error": camera_error or None,
    }
    runtime = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "backend_ready": bool(backend_ready),
        "backend_started_at": backend_started_at,
    }
    application = {
        "name": "OLED Measurement App",
        "version": APP_VERSION,
        "channel": "alpha",
        "schema_version": API_SCHEMA_VERSION,
        "generated_at": timestamp,
    }
    safe_hardware = {
        "mode": redact_diagnostic_text(hardware.get("mode")),
        "smu": redact_diagnostic_text(hardware.get("smu")),
        "spectrometer": redact_diagnostic_text(hardware.get("spectrometer")),
        "camera": "ready" if camera_summary["connected"] else "not_probed",
    }

    lines = [
        f"{application['name']} {application['version']} ({application['channel']})",
        f"Сформировано: {timestamp}",
        f"API: схема {API_SCHEMA_VERSION}; backend: {'готов' if backend_ready else 'не готов'}",
        f"Среда: Python {runtime['python']}; {runtime['platform']}; packaged={runtime['frozen']}",
        "",
        "Оборудование",
        f"Режим: {safe_hardware['mode']}",
        f"SMU: {safe_hardware['smu']}; спектрометр: {safe_hardware['spectrometer']}",
        (
            "Камера: "
            + ("подключена" if camera_summary["connected"] else "не подключена")
            + (f"; модель: {camera_summary['model']}" if camera_summary["model"] else "")
            + f"; LiveView={camera_summary['liveview_active']}; запись={camera_summary['recording_active']}"
        ),
        "",
        "Пути",
        f"Настройки: {paths['settings']}",
        f"Журналы: {paths['logs']}",
        f"Корень серий: {paths['series_root']}",
        f"Активная серия: {paths['active_series'] or 'не открыта'}",
        "",
        "Операции",
    ]
    lines.extend(
        f"{item['name']}: {item['status']}; active={item['active']}"
        for item in operation_rows
    )
    lines.extend(["", "Последние ошибки"])
    lines.extend(recent_errors or ["Ошибок не обнаружено."])
    copy_text = redact_diagnostic_text("\n".join(lines))

    return {
        "application": application,
        "runtime": runtime,
        "hardware": safe_hardware,
        "camera": camera_summary,
        "series": {
            "active": bool(series.get("active")),
            "path": paths["active_series"],
            "root": paths["series_root"],
        },
        "paths": paths,
        "operations": operation_rows,
        "recent_errors": recent_errors,
        "copy_text": copy_text,
    }
