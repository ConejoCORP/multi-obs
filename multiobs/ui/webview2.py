"""Page web dans une fenêtre de Multi OBS, avec Edge WebView2 (le moteur d'Edge, déjà dans Windows 11). Sert à la
connexion à Twitch. Appels COM directs avec ctypes et WebView2Loader.dll (Microsoft, redistribuable, 160 Ko) :
ni .NET ni QtWebEngine (+100 Mo). Profil gardé dans un dossier à soi (on reste connecté à Twitch d'une fois sur
l'autre). Si WebView2 manque ou échoue : signal failed(texte), et l'appelant ouvre le navigateur à la place.

Ordre des méthodes et IID : ceux de WebView2.h (vérifiés sur Microsoft.Web.WebView2.Core 1.0.3856).
"""
import ctypes
import uuid
from collections import deque
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_int64, c_ulong, c_void_p, wintypes
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QWidget

from .. import applog, paths

LOADER = paths.ASSETS / "webview2" / "WebView2Loader.dll"
HRESULT = ctypes.c_long
S_OK, E_NOINTERFACE = 0, -2147467262
RPC_E_CHANGED_MODE = -2147417850
COINIT_APARTMENTTHREADED = 2

# Index des méthodes dans la table virtuelle (0 à 2 : QueryInterface, AddRef, Release).
ENV_CREATE_CONTROLLER = 3
CTRL_PUT_IS_VISIBLE, CTRL_PUT_BOUNDS, CTRL_MOVE_FOCUS = 4, 6, 12
CTRL_NOTIFY_PARENT_MOVED, CTRL_CLOSE, CTRL_GET_COREWEBVIEW2 = 23, 24, 25
WV_GET_SETTINGS, WV_NAVIGATE, WV_ADD_NAVIGATION_COMPLETED = 3, 5, 15
WV_EXECUTE_SCRIPT, WV_ADD_NEW_WINDOW_REQUESTED = 29, 44
SETTINGS_PUT_DEVTOOLS = 12
NAVARGS_GET_IS_SUCCESS, NAVARGS_GET_WEB_ERROR = 3, 4
NEWWIN_GET_URI, NEWWIN_PUT_HANDLED = 3, 6
IID_IUNKNOWN = "00000000-0000-0000-C000-000000000046"
IID_ENV_COMPLETED = "4e8a3389-c9d8-4bd2-b6b5-124fee6cc14d"
IID_CONTROLLER_COMPLETED = "6c4819f3-c9b7-4260-8127-c9f5bde7f68c"
IID_NAVIGATION_COMPLETED = "d33a35bf-1c49-4f98-93ab-006e0533fe1c"
IID_EXECUTE_SCRIPT_COMPLETED = "49511172-cc67-4bca-9923-137112f4c4cc"
IID_NEW_WINDOW_REQUESTED = "d4c185fe-c81c-4989-97af-2d3fa7ab5651"


class GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


def _iid_bytes(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


_QI = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
_REF = WINFUNCTYPE(c_ulong, c_void_p)
_KEEP: list = []   # rappels COM gardés pour toujours : WebView2 peut encore les appeler après la fermeture


class _Callback:
    """Petit objet COM (IUnknown + Invoke) pour un rappel de WebView2 ; fn(*arguments d'Invoke)."""

    def __init__(self, iid: str, argtypes: tuple, fn):
        self._iids = (_iid_bytes(iid), _iid_bytes(IID_IUNKNOWN))
        self._fn = fn
        self._refs = 1
        invoke = WINFUNCTYPE(HRESULT, c_void_p, *argtypes)

        class VTable(Structure):
            _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("Invoke", invoke)]
        self._vtable = VTable(_QI(self._query), _REF(self._addref), _REF(self._release), invoke(self._invoke))
        self._object = (c_void_p * 1)(ctypes.addressof(self._vtable))
        self.ptr = ctypes.addressof(self._object)
        _KEEP.append(self)

    def _query(self, this, riid, ppv):
        if bytes(riid.contents) in self._iids:
            ppv[0] = this
            self._refs += 1
            return S_OK
        ppv[0] = None
        return E_NOINTERFACE

    def _addref(self, _this):
        self._refs += 1
        return self._refs

    def _release(self, _this):
        self._refs = max(0, self._refs - 1)
        return self._refs

    def _invoke(self, _this, *args):
        try:
            self._fn(*args)
        except Exception as e:   # jamais d'exception vers WebView2
            applog.log(f"WebView2 : {type(e).__name__}: {e}")
        return S_OK


def _vcall(ptr: int, index: int, argtypes: tuple = (), *args, restype=HRESULT):
    """Méthode index de l'objet COM ptr."""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    return WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])(ptr, *args)


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} : erreur 0x{hr & 0xFFFFFFFF:08X}")


def _addref(ptr: int) -> None:
    _vcall(ptr, 1, restype=c_ulong)


def _release(ptr: int | None) -> None:
    if ptr:
        _vcall(ptr, 2, restype=c_ulong)


_loader = None


def _dll():
    global _loader
    if _loader is None:
        dll = ctypes.WinDLL(str(LOADER))
        dll.CreateCoreWebView2EnvironmentWithOptions.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, c_void_p,
                                                                 c_void_p]
        dll.CreateCoreWebView2EnvironmentWithOptions.restype = HRESULT
        dll.GetAvailableCoreWebView2BrowserVersionString.argtypes = [wintypes.LPCWSTR, POINTER(c_void_p)]
        dll.GetAvailableCoreWebView2BrowserVersionString.restype = HRESULT
        _loader = dll
    return _loader


def runtime_version() -> str:
    """Version de WebView2 installée ("" : absent, ou chargeur manquant)."""
    try:
        out = c_void_p()
        if _dll().GetAvailableCoreWebView2BrowserVersionString(None, byref(out)) < 0 or not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except OSError:
        return ""


def _take_string(p: c_void_p) -> str:
    if not p.value:
        return ""
    try:
        return ctypes.wstring_at(p.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(p)


class WebView(QWidget):
    """Page web (WebView2) dans un widget. start(url) ; ready : page affichée ; failed(texte) : WebView2
    indisponible ; loaded(réussi, erreur) à chaque page chargée. Lien qui ouvre une fenêtre : open_external(url)
    renvoie True s'il l'a pris (navigateur), sinon WebView2 ouvre sa propre fenêtre (connexion Google…)."""
    ready = Signal()
    failed = Signal(str)
    loaded = Signal(bool, int)

    def __init__(self, user_data: Path, open_external=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setMinimumSize(320, 320)
        self.user_data = Path(user_data)
        self._open_external = open_external
        self._env = self._controller = self._webview = None
        self._url = ""
        self._closed = False
        self._focused = False
        self._filtered = None
        self._script_cb = None           # un seul rappel COM pour tous les scripts (réponses dans l'ordre)
        self._script_waiting: deque = deque()

    # -------- création (asynchrone : rappels de WebView2 dans la boucle d'événements de Qt)
    def start(self, url: str) -> None:
        self._url = url
        try:
            if not runtime_version():
                raise OSError("WebView2 n'est pas installé")
            hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            if hr == RPC_E_CHANGED_MODE:
                raise OSError("thread COM incompatible")
            self.user_data.mkdir(parents=True, exist_ok=True)
            done = _Callback(IID_ENV_COMPLETED, (HRESULT, c_void_p), self._env_created)
            _check(_dll().CreateCoreWebView2EnvironmentWithOptions(None, str(self.user_data), None, done.ptr),
                   "environnement WebView2")
        except OSError as e:
            self._fail(str(e))

    def _fail(self, text: str) -> None:
        applog.log(f"WebView2 indisponible : {text}")
        self.close_view()
        self.failed.emit(text)

    def _env_created(self, hr: int, env: int) -> None:
        if self._closed:
            return
        if hr < 0 or not env:
            self._fail(f"environnement WebView2 : erreur 0x{hr & 0xFFFFFFFF:08X}")
            return
        _addref(env)
        self._env = env
        try:
            hwnd = int(self.winId())
            done = _Callback(IID_CONTROLLER_COMPLETED, (HRESULT, c_void_p), self._controller_created)
            _check(_vcall(env, ENV_CREATE_CONTROLLER, (wintypes.HWND, c_void_p), hwnd, done.ptr),
                   "fenêtre WebView2")
        except (OSError, RuntimeError) as e:
            self._fail(str(e))

    def _controller_created(self, hr: int, controller: int) -> None:
        if hr < 0 or not controller:
            if not self._closed:
                self._fail(f"fenêtre WebView2 : erreur 0x{hr & 0xFFFFFFFF:08X}")
            return
        _addref(controller)
        self._controller = controller
        if self._closed:   # fermé pendant la création
            self.close_view()
            return
        try:
            webview = c_void_p()
            _check(_vcall(controller, CTRL_GET_COREWEBVIEW2, (POINTER(c_void_p),), byref(webview)), "page WebView2")
            self._webview = webview.value
            settings = c_void_p()
            if _vcall(self._webview, WV_GET_SETTINGS, (POINTER(c_void_p),), byref(settings)) >= 0 and settings.value:
                _vcall(settings.value, SETTINGS_PUT_DEVTOOLS, (wintypes.BOOL,), False)
                _release(settings.value)
            token = c_int64()
            nav = _Callback(IID_NAVIGATION_COMPLETED, (c_void_p, c_void_p), self._navigation_completed)
            _vcall(self._webview, WV_ADD_NAVIGATION_COMPLETED, (c_void_p, POINTER(c_int64)), nav.ptr, byref(token))
            popup = _Callback(IID_NEW_WINDOW_REQUESTED, (c_void_p, c_void_p), self._new_window)
            _vcall(self._webview, WV_ADD_NEW_WINDOW_REQUESTED, (c_void_p, POINTER(c_int64)), popup.ptr, byref(token))
            self._sync_bounds()
            _vcall(controller, CTRL_PUT_IS_VISIBLE, (wintypes.BOOL,), self.isVisible())
            if self._url:
                self.navigate(self._url)
            win = self.window()
            if win is not None and win is not self:   # fenêtre déplacée : WebView2 doit le savoir (menus…)
                win.installEventFilter(self)
                self._filtered = win
        except (OSError, RuntimeError) as e:
            self._fail(str(e))
            return
        self.ready.emit()

    # -------- page
    def navigate(self, url: str) -> None:
        self._url = url
        if self._webview:
            _check(_vcall(self._webview, WV_NAVIGATE, (wintypes.LPCWSTR,), url), "navigation")

    def run_script(self, js: str, callback=None) -> bool:
        """Exécute du JavaScript dans la page ; callback(résultat JSON, None si erreur)."""
        if not self._webview or self._closed:
            return False
        if self._script_cb is None:
            self._script_cb = _Callback(IID_EXECUTE_SCRIPT_COMPLETED, (HRESULT, wintypes.LPCWSTR), self._script_done)
        self._script_waiting.append(callback)
        if _vcall(self._webview, WV_EXECUTE_SCRIPT, (wintypes.LPCWSTR, c_void_p), js, self._script_cb.ptr) < 0:
            self._script_waiting.pop()
            return False
        return True

    def _script_done(self, hr: int, result) -> None:
        callback = self._script_waiting.popleft() if self._script_waiting else None
        if callback is not None:
            callback(result if hr >= 0 else None)

    def _navigation_completed(self, _sender: int, args: int) -> None:
        ok, error = wintypes.BOOL(), ctypes.c_int()
        _vcall(args, NAVARGS_GET_IS_SUCCESS, (POINTER(wintypes.BOOL),), byref(ok))
        _vcall(args, NAVARGS_GET_WEB_ERROR, (POINTER(ctypes.c_int),), byref(error))
        if self._closed:
            return
        if not self._focused and self._controller:   # le clavier va dans la page (champ « Nom d'utilisateur »)
            self._focused = True
            _vcall(self._controller, CTRL_MOVE_FOCUS, (ctypes.c_int,), 0)
        self.loaded.emit(bool(ok.value), int(error.value))

    def _new_window(self, _sender: int, args: int) -> None:
        """Lien qui ouvre une fenêtre (aide, conditions…) : dans le navigateur, pas dans une fenêtre à part."""
        uri = c_void_p()
        _vcall(args, NEWWIN_GET_URI, (POINTER(c_void_p),), byref(uri))
        url = _take_string(uri)
        if self._open_external and url.startswith(("https://", "http://")) and self._open_external(url):
            _vcall(args, NEWWIN_PUT_HANDLED, (wintypes.BOOL,), True)   # sinon : fenêtre de WebView2 (Google…)

    # -------- taille, visibilité, fermeture
    def _sync_bounds(self) -> None:
        if self._controller and not self._closed:
            r = self.devicePixelRatioF()
            rect = wintypes.RECT(0, 0, round(self.width() * r), round(self.height() * r))
            _vcall(self._controller, CTRL_PUT_BOUNDS, (wintypes.RECT,), rect)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_bounds()

    def showEvent(self, e):
        super().showEvent(e)
        if self._controller and not self._closed:
            _vcall(self._controller, CTRL_PUT_IS_VISIBLE, (wintypes.BOOL,), True)
            self._sync_bounds()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._controller and not self._closed:
            _vcall(self._controller, CTRL_PUT_IS_VISIBLE, (wintypes.BOOL,), False)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Move and self._controller and not self._closed:
            _vcall(self._controller, CTRL_NOTIFY_PARENT_MOVED)
        return False

    def close_view(self) -> None:
        """Ferme la page (WebView2 arrête ses processus) ; à appeler avant de fermer la fenêtre."""
        if self._closed:
            return
        self._closed = True
        if self._filtered is not None:
            try:
                self._filtered.removeEventFilter(self)
            except RuntimeError:
                pass
            self._filtered = None
        if self._controller:
            _vcall(self._controller, CTRL_CLOSE)
        for ptr in (self._webview, self._controller, self._env):
            _release(ptr)
        self._webview = self._controller = self._env = None
