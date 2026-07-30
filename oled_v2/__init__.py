"""Isolated v2 desktop prototype.

The stable Tkinter application remains in :mod:`oled_app` and is not routed
through this package while the v2 parity checklist is incomplete.
"""

from oled_app.constants import APP_VERSION

__all__ = ["APP_VERSION"]
