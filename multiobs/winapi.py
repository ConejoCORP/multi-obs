"""Petites fonctions Win32 pour piloter les fenêtres des instances OBS."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]

GW_OWNER = 4
SW_RESTORE = 9
SW_SHOWMINNOACTIVE = 7
WM_CLOSE = 0x0010
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def main_window(pid: int, visible_only: bool = True):
    """Fenêtre principale d'OBS pour un PID (titre « OBS 32.x - Profil ... »)."""
    found = []

    def cb(hwnd, _lp):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        visible = user32.IsWindowVisible(hwnd) or not visible_only
        if p.value == pid and visible and not user32.GetWindow(hwnd, GW_OWNER):
            if _title(hwnd).startswith("OBS"):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else None


def pending_dialogs(pid: int, ignore: frozenset = frozenset()) -> list[str]:
    """Fenêtres de dialogue d'OBS ouvertes alors que sa fenêtre principale est réduite :
    OBS attend une réponse (erreur, confirmation…) que personne ne voit. `ignore` : titres de
    fenêtres qui n'attendent rien (conversion en cours…)."""
    main, owned = None, []

    def cb(hwnd, _lp):
        nonlocal main
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            title = _title(hwnd)
            if user32.GetWindow(hwnd, GW_OWNER):
                if title and title not in ignore:
                    owned.append(title)
            elif title.startswith("OBS"):
                main = hwnd
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    if main is None or not user32.IsIconic(main):
        return []
    return owned


def has_window(pid: int, titles: frozenset) -> bool:
    """Le processus a-t-il une fenêtre visible portant l'un de ces titres ?"""
    found = False

    def cb(hwnd, _lp):
        nonlocal found
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd) and _title(hwnd) in titles:
            found = True
            return False
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return found


# Fenêtres d'un autre processus : versions « Async », sinon un OBS figé figerait Multi OBS.
def bring_to_front(hwnd) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindowAsync(hwnd, SW_RESTORE)
    # Windows refuse SetForegroundWindow sans entrée clavier récente : on simule Alt.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


def minimize(hwnd) -> None:
    user32.ShowWindowAsync(hwnd, SW_SHOWMINNOACTIVE)


def request_close(hwnd) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def window_pid(hwnd) -> int:
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def is_owned(hwnd) -> bool:
    """Fenêtre secondaire (boîte de dialogue…) rattachée à une fenêtre principale."""
    return bool(user32.GetWindow(hwnd, GW_OWNER))


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


user32.FlashWindowEx.argtypes = [ctypes.POINTER(_FLASHWINFO)]


def stop_flash(hwnd) -> None:
    """Éteint le clignotement d'une fenêtre (bouton orange dans la barre des tâches)."""
    FLASHW_STOP = 0
    info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), hwnd, FLASHW_STOP, 0, 0)
    user32.FlashWindowEx(ctypes.byref(info))


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


def unthrottle(pid: int, on: bool = True) -> bool:
    """Empêche Windows 11 de brider un OBS en arrière-plan ou réduit. Bridé, il ignore la minuterie
    de 1 ms qu'OBS demande : chaque image est rendue jusqu'à 15 ms après son heure (saccades dans le
    rec), et le processeur peut être ralenti (mode « efficacité »). on=False : Windows décide de nouveau."""
    PROCESS_SET_INFORMATION, PROCESS_POWER_THROTTLING = 0x0200, 4
    EXECUTION_SPEED, IGNORE_TIMER_RESOLUTION = 0x1, 0x4
    handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        # Les deux bits dans ControlMask, aucun dans StateMask : « jamais bridé » pour les deux.
        # ControlMask vide : comportement par défaut de Windows.
        state = _PowerThrottling(1, EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION if on else 0, 0)
        return bool(kernel32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING, ctypes.byref(state),
                                                   ctypes.sizeof(state)))
    except (AttributeError, OSError):   # Windows trop ancien
        return False
    finally:
        kernel32.CloseHandle(handle)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def send_to_recycle_bin(path: str) -> bool:
    """Met un fichier à la corbeille Windows (récupérable)."""
    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0", None,
                          FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
