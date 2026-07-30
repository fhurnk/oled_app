"""Windows Wi-Fi profile switching for the Raspberry Pi camera workflow."""

from __future__ import annotations

import locale
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence


class WifiConnectionError(RuntimeError):
    """A user-facing Windows Wi-Fi connection error."""


@dataclass(frozen=True)
class WifiInterfaceState:
    """Current state of one interface reported by ``netsh wlan``."""

    name: str
    connected: bool
    profile: str = ""
    ssid: str = ""


@dataclass(frozen=True)
class WifiConnectionSession:
    """Information required to restore the network used before the camera."""

    target_profile: str
    interface_name: str
    previous_profile: str
    switched: bool


def _decode_netsh_output(payload: bytes | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    encodings = (
        "utf-8",
        "oem",
        locale.getpreferredencoding(False),
        "cp866",
        "cp1251",
    )
    for encoding in encodings:
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def parse_netsh_wlan_interfaces(output: str) -> List[WifiInterfaceState]:
    """Parse English or Russian ``netsh wlan show interfaces`` output."""

    name_labels = {"name", "имя"}
    state_labels = {"state", "состояние"}
    profile_labels = {"profile", "профиль"}
    connected_values = {"connected", "подключено"}
    sections: List[dict[str, str]] = []
    current: dict[str, str] = {}

    for raw_line in str(output or "").splitlines():
        if ":" not in raw_line:
            continue
        label, value = (part.strip() for part in raw_line.split(":", 1))
        normalized = label.casefold()
        if normalized in name_labels:
            if current.get("name"):
                sections.append(current)
                current = {}
            current["name"] = value
        elif normalized in state_labels:
            current["state"] = value
        elif normalized == "ssid":
            current["ssid"] = value
        elif normalized in profile_labels:
            current["profile"] = value
    if current.get("name"):
        sections.append(current)

    return [
        WifiInterfaceState(
            name=section.get("name", ""),
            connected=section.get("state", "").casefold() in connected_values,
            profile=section.get("profile", ""),
            ssid=section.get("ssid", ""),
        )
        for section in sections
    ]


class WindowsWifiController:
    """Connect to saved Windows WLAN profiles without storing their passwords."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        platform_name: Optional[str] = None,
    ):
        self._runner = runner
        self._sleep = sleep
        self._monotonic = monotonic
        self._platform_name = os.name if platform_name is None else platform_name

    def _run(self, arguments: Sequence[str], timeout_s: float = 10.0) -> subprocess.CompletedProcess:
        if self._platform_name != "nt":
            raise WifiConnectionError(
                "Автоподключение Wi-Fi камеры поддерживается только в Windows."
            )
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            completed = self._runner(
                list(arguments),
                capture_output=True,
                check=False,
                timeout=max(float(timeout_s), 1.0),
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise WifiConnectionError("Windows-команда netsh не найдена.") from exc
        except subprocess.TimeoutExpired as exc:
            raise WifiConnectionError("Windows не ответила на запрос управления Wi-Fi.") from exc
        return completed

    def interfaces(self) -> List[WifiInterfaceState]:
        completed = self._run(("netsh", "wlan", "show", "interfaces"))
        if completed.returncode != 0:
            details = _decode_netsh_output(completed.stderr or completed.stdout).strip()
            raise WifiConnectionError(
                "Не удалось получить состояние Wi-Fi Windows."
                + (f"\n{details}" if details else "")
            )
        return parse_netsh_wlan_interfaces(_decode_netsh_output(completed.stdout))

    @staticmethod
    def _select_interface(
        interfaces: Iterable[WifiInterfaceState],
        requested_name: str = "",
    ) -> WifiInterfaceState:
        available = list(interfaces)
        requested = str(requested_name or "").strip().casefold()
        if requested:
            for interface in available:
                if interface.name.casefold() == requested:
                    return interface
            raise WifiConnectionError(
                f"Wi-Fi-адаптер «{requested_name}» не найден. "
                "Оставьте поле адаптера пустым для автоматического выбора."
            )
        for interface in available:
            if interface.connected:
                return interface
        if available:
            return available[0]
        raise WifiConnectionError("Windows не обнаружила доступный Wi-Fi-адаптер.")

    def current(self, interface_name: str = "") -> WifiInterfaceState:
        return self._select_interface(self.interfaces(), interface_name)

    def connect_saved_profile(
        self,
        profile_name: str,
        *,
        interface_name: str = "",
        timeout_s: float = 25.0,
    ) -> WifiConnectionSession:
        """Connect to an existing Windows profile and wait for completion."""

        profile = str(profile_name or "").strip()
        if not profile:
            raise WifiConnectionError(
                "В настройках камеры не задан сохранённый Wi-Fi-профиль Raspberry Pi."
            )
        if any(character in profile for character in "\r\n\0"):
            raise WifiConnectionError("Имя Wi-Fi-профиля содержит недопустимые символы.")

        initial = self.current(interface_name)
        if initial.connected and initial.profile.casefold() == profile.casefold():
            return WifiConnectionSession(
                target_profile=profile,
                interface_name=initial.name,
                previous_profile=initial.profile,
                switched=False,
            )

        arguments = [
            "netsh",
            "wlan",
            "connect",
            f"name={profile}",
            f"interface={initial.name}",
        ]
        completed = self._run(arguments)
        if completed.returncode != 0:
            details = _decode_netsh_output(completed.stderr or completed.stdout).strip()
            raise WifiConnectionError(
                f"Windows не удалось подключить к Wi-Fi-профилю «{profile}»."
                + (f"\n{details}" if details else "")
            )

        deadline = self._monotonic() + max(float(timeout_s), 1.0)
        while self._monotonic() < deadline:
            current = self.current(initial.name)
            if current.connected and current.profile.casefold() == profile.casefold():
                return WifiConnectionSession(
                    target_profile=profile,
                    interface_name=initial.name,
                    previous_profile=initial.profile if initial.connected else "",
                    switched=True,
                )
            self._sleep(0.4)

        raise WifiConnectionError(
            f"Windows не подключилась к Wi-Fi-профилю «{profile}» "
            f"за {float(timeout_s):g} с."
        )

    def restore(
        self,
        session: Optional[WifiConnectionSession],
        *,
        timeout_s: float = 25.0,
    ) -> bool:
        """Restore the profile active before the camera connection."""

        if session is None or not session.switched or not session.previous_profile:
            return False
        self.connect_saved_profile(
            session.previous_profile,
            interface_name=session.interface_name,
            timeout_s=timeout_s,
        )
        return True
