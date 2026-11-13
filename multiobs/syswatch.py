"""Événements Windows : session verrouillée et écran éteint (personne ne regarde les aperçus
-> ils se mettent en pause), bouton d'une fenêtre qui clignote dans la barre des tâches."""
import ctypes
import uuid
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8
WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
HSHELL_FLASH = 0x8006      # message du shell : le bouton de la fenêtre lParam clignote
GUID_CONSOLE_DISPLAY_STATE = uuid.UUID("6FE69556-704A-47A0-8F24-C28D936FDA47")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", _GUID), ("DataLength", wintypes.DWORD), ("Data", wintypes.DWORD)]


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: "SystemEvents"):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_WTSSESSION_CHANGE:
            if msg.wParam == WTS_SESSION_LOCK:
                self.owner._set(locked=True)
            elif msg.wParam == WTS_SESSION_UNLOCK:
                self.owner._set(locked=False)
        elif msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE and msg.lParam:
            setting = _POWERBROADCAST_SETTING.from_address(msg.lParam)
            if bytes(setting.PowerSetting) == GUID_CONSOLE_DISPLAY_STATE.bytes_le:
                self.owner._set(display_off=(setting.Data == 0))  # 0 éteint, 1 allumé, 2 atténué
        elif self.owner.shell_message and msg.message == self.owner.shell_message \
                and msg.wParam == HSHELL_FLASH and msg.lParam:
            self.owner.windowFlashed.emit(int(msg.lParam))
        return False, 0


class SystemEvents(QObject):
    awayChanged = Signal(bool)
    windowFlashed = Signal(object)   # hwnd dont le bouton clignote dans la barre des tâches

    def __init__(self, hwnd: int):
        super().__init__()
        self.locked = False
        self.display_off = False
        self.filter = _Filter(self)
        # Messages que le shell envoie à la barre des tâches (fenêtre créée, activée, qui clignote…).
        try:
            self.shell_message = ctypes.windll.user32.RegisterWindowMessageW("SHELLHOOK")
            ctypes.windll.user32.RegisterShellHookWindow(wintypes.HWND(hwnd))
        except (OSError, AttributeError):
            self.shell_message = 0
        try:
            ctypes.windll.wtsapi32.WTSRegisterSessionNotification(wintypes.HWND(hwnd), 0)
        except (OSError, AttributeError):
            pass
        try:
            guid = _GUID.from_buffer_copy(GUID_CONSOLE_DISPLAY_STATE.bytes_le)
            fn = ctypes.windll.user32.RegisterPowerSettingNotification
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_GUID), wintypes.DWORD]
            fn.restype = wintypes.HANDLE
            self._power_handle = fn(wintypes.HANDLE(hwnd), ctypes.byref(guid), 0)
        except (OSError, AttributeError):
            self._power_handle = None

    @property
    def away(self) -> bool:
        return self.locked or self.display_off

    def _set(self, locked: bool | None = None, display_off: bool | None = None) -> None:
        before = self.away
        if locked is not None:
            self.locked = locked
        if display_off is not None:
            self.display_off = display_off
        if self.away != before:
            self.awayChanged.emit(self.away)
