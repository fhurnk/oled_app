"""Report modules for the OLED measurement application."""

from importlib import import_module

_EXPORTS = {
    "build_origin_project": "build_origin_project",
    "build_workbook": "build_workbook",
    "collect_report_data": "collect_report_data",
    "main": "main",
}

__all__ = [
    "build_origin_project",
    "build_workbook",
    "collect_report_data",
    "main",
]


def __getattr__(name: str):
    if name in _EXPORTS:
        module = import_module(".origin_report", __name__)
        return getattr(module, _EXPORTS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
