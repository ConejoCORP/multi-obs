"""Connexion à Twitch pour modérer le chat depuis Multi OBS : supprimer un message, exclure, bannir.

- Connexion par code (« Device Code Grant Flow » de Twitch, application publique, sans secret) : Multi OBS demande
  un code à Twitch, on l'accepte sur twitch.tv/activate dans son navigateur (où on est déjà connecté). Multi OBS ne
  voit jamais le mot de passe.
- Jetons chiffrés par Windows (DPAPI : lisibles seulement par cette session Windows) dans
  %LOCALAPPDATA%\\Multi OBS\\twitch.bin ; jamais dans multiobs.json, les préconfigs ou le journal.
- Jeton d'accès (4 h) renouvelé tout seul avec le jeton de renouvellement (à usage unique, valable 30 jours sans
  servir) ; vérifié par /validate à la connexion puis toutes les heures, comme Twitch le demande.
- API Helix : supprimer un message, exclure (durée) ou bannir, lever un bannissement, chaînes modérées.
Fonctions bloquantes (réseau) : à appeler hors du thread de l'interface.
"""
import ctypes
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

from .livechat import short_error

# Application « Multi OBS » enregistrée sur dev.twitch.tv (type de client : Public). Un Client ID est public par
# nature (il est dans l'adresse de la page d'autorisation) : rien de secret ici.
CLIENT_ID = "i3ts8rrzexs0xgm3blou86xv63h3k5"
SCOPES = ("moderator:manage:chat_messages", "moderator:manage:banned_users", "user:read:moderated_channels")
ID_URL = "https://id.twitch.tv/oauth2"
API_URL = "https://api.twitch.tv/helix"
TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Multi OBS" / "twitch.bin"
UA = "MultiOBS (+https://github.com/ConejoCORP/multi-obs)"
VALIDATE_EVERY_S = 3600      # Twitch : valider le jeton au démarrage puis toutes les heures
MODS_CACHE_S = 300           # chaînes modérées : redemandées au plus toutes les 5 min
TIMEOUT_MAX_S = 1_209_600    # exclusion de 2 semaines au plus
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class TwitchError(Exception):
    """Erreur expliquée en français (str(e)) ; status : code HTTP de Twitch (0 : pas de réponse)."""

    def __init__(self, text: str, status: int = 0):
        super().__init__(text)
        self.status = status


class NeedLogin(TwitchError):
    """Pas connecté, ou connexion expirée / révoquée : il faut se (re)connecter à Twitch."""


class Cancelled(Exception):
    """Connexion abandonnée (fenêtre fermée)."""


# ------------------------------------------------------------------ HTTP
def _request(method: str, url: str, form: dict | None = None, body: dict | None = None,
             headers: dict | None = None, timeout: float = 10) -> tuple[int, dict]:
    """(code HTTP, réponse JSON) ; TwitchError si Twitch ne répond pas. Les jetons ne vont que dans les en-têtes
    ou le corps de la requête, jamais dans l'adresse."""
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(100_000)
        except OSError:
            raw = b""
    except (OSError, ValueError) as e:
        raise TwitchError(f"Twitch injoignable ({short_error(e)})") from None
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        payload = {}
    return status, payload if isinstance(payload, dict) else {"data": payload}


def explain(status: int, d: dict) -> str:
    """Erreur de Twitch en français (les messages de Twitch sont en anglais)."""
    msg = str(d.get("message") or d.get("error") or "").strip()
    low = msg.lower()
    if "invalid client" in low or ("client_id" in low and "invalid" in low):
        return "Client ID inconnu de Twitch : vérifie-le sur dev.twitch.tv/console"
    if "secret" in low:
        return ("ton application Twitch doit être de type « Public » (dev.twitch.tv/console) : crée-la à "
                "nouveau en choisissant Public")
    if status == 403:
        return "ton compte n'est pas modérateur de cette chaîne"
    if status == 429:
        return "trop d'actions d'affilée, réessaie dans un instant"
    if status == 409:
        return "quelqu'un d'autre modifie déjà le bannissement de cette personne, réessaie"
    if "already banned" in low:
        return "cette personne est déjà bannie"
    if "is not banned" in low:
        return "cette personne n'est ni bannie ni exclue"
    if "may not be banned" in low or "may not be put in a timeout" in low:
        return "cette personne ne peut être ni bannie ni exclue (modérateur, diffuseur ou staff de Twitch)"
    if "another moderator" in low:
        return "impossible de supprimer le message d'un modérateur"
    if "broadcaster's messages" in low:
        return "impossible de supprimer un message du diffuseur"
    if "6 hours" in low:
        return "message trop ancien (plus de 6 heures)"
    if "message_id was not found" in low:
        return "message introuvable (déjà supprimé ?)"
    if "reason" in low and "standards" in low:
        return "Twitch refuse la raison indiquée"
    if "scope" in low:
        return "autorisation manquante : déconnecte-toi puis reconnecte-toi à Twitch"
    if status == 401:
        return "connexion à Twitch refusée : reconnecte-toi"
    if msg:
        return f"{msg} (erreur {status})" if status else msg
    return f"erreur {status} de Twitch" if status else "erreur de Twitch"


# ------------------------------------------------------------------ connexion par code
def device_start(client_id: str) -> dict:
    """Demande un code à Twitch : {device_code, user_code, verification_uri, expires_in, interval}."""
    if not client_id:
        raise TwitchError("Client ID de l'application Twitch manquant")
    status, d = _request("POST", f"{ID_URL}/device", form={"client_id": client_id, "scopes": " ".join(SCOPES)})
    if status != 200 or not d.get("device_code") or not d.get("user_code"):
        raise TwitchError(explain(status, d), status)
    return d


def device_wait(client_id: str, start: dict, stop: threading.Event) -> dict:
    """Attend que la personne accepte sur twitch.tv/activate ; renvoie les jetons. Cancelled si stop est levé,
    TwitchError si elle refuse ou si le code expire (30 min)."""
    interval = max(1, int(start.get("interval") or 5))
    deadline = time.monotonic() + int(start.get("expires_in") or 1800)
    scopes = " ".join(SCOPES)
    form = {"client_id": client_id, "scopes": scopes, "scope": scopes, "device_code": start["device_code"],
            "grant_type": DEVICE_GRANT}
    while True:
        if stop.wait(interval):
            raise Cancelled()
        if time.monotonic() > deadline:
            raise TwitchError("le code a expiré : recommence la connexion")
        try:
            status, d = _request("POST", f"{ID_URL}/token", form=form)
        except TwitchError:
            continue   # coupure réseau : nouvel essai au tour suivant
        if status == 200 and d.get("access_token"):
            return d
        low = str(d.get("message") or d.get("error") or "").lower()
        if "pending" in low:
            continue
        if "slow_down" in low or "slow down" in low:
            interval += 5
            continue
        if "invalid device code" in low or "expired" in low:
            raise TwitchError("le code a expiré ou a déjà servi : recommence la connexion", status)
        if "denied" in low or "declined" in low:
            raise TwitchError("connexion refusée sur Twitch", status)
        raise TwitchError(explain(status, d), status)


def validate(access_token: str) -> dict | None:
    """/validate : {client_id, login, user_id, scopes, expires_in}, ou None si le jeton n'est plus bon."""
    status, d = _request("GET", f"{ID_URL}/validate", headers={"Authorization": f"OAuth {access_token}"})
    if status == 200 and d.get("user_id"):
        return d
    if status == 401:
        return None
    raise TwitchError(explain(status, d), status)


def refresh(client_id: str, refresh_token: str) -> dict:
    """Nouveaux jetons (le jeton de renouvellement ne sert qu'une fois) ; NeedLogin s'il n'est plus bon."""
    status, d = _request("POST", f"{ID_URL}/token", form={"client_id": client_id, "grant_type": "refresh_token",
                                                          "refresh_token": refresh_token})
    if status == 200 and d.get("access_token"):
        return d
    if status in (400, 401, 403):
        raise NeedLogin("connexion à Twitch expirée : reconnecte-toi", status)
    raise TwitchError(explain(status, d), status)


def revoke(client_id: str, access_token: str) -> None:
    """Déconnexion : le jeton est annulé chez Twitch (au mieux : sans réseau, il expire de lui-même)."""
    if client_id and access_token:
        try:
            _request("POST", f"{ID_URL}/revoke", form={"client_id": client_id, "token": access_token}, timeout=6)
        except TwitchError:
            pass


# ------------------------------------------------------------------ jetons chiffrés (DPAPI)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_ENTROPY = b"Multi OBS / connexion Twitch"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_dll: tuple | None = None


def _dpapi():
    global _dll
    if _dll is None:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob = ctypes.POINTER(_Blob)
        crypt32.CryptProtectData.argtypes = [blob, wintypes.LPCWSTR, blob, ctypes.c_void_p, ctypes.c_void_p,
                                             wintypes.DWORD, blob]
        crypt32.CryptUnprotectData.argtypes = [blob, ctypes.c_void_p, blob, ctypes.c_void_p, ctypes.c_void_p,
                                               wintypes.DWORD, blob]
        crypt32.CryptProtectData.restype = crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        _dll = (crypt32, kernel32)
    return _dll


def _dpapi_call(protect: bool, data: bytes) -> bytes:
    crypt32, kernel32 = _dpapi()
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    ent_buf = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    ent = _Blob(len(_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "Multi OBS", ctypes.byref(ent), None, None,
                                      _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                                        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    return _dpapi_call(True, data)


def unprotect(data: bytes) -> bytes:
    return _dpapi_call(False, data)


def load_tokens() -> dict | None:
    """Connexion enregistrée (None : aucune, ou illisible : autre session Windows, fichier abîmé…)."""
    try:
        tok = json.loads(unprotect(TOKEN_FILE.read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(tok, dict) and all(tok.get(k) for k in ("client_id", "access_token", "refresh_token", "user_id"))
    return tok if ok else None


def save_tokens(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_name(TOKEN_FILE.name + ".tmp")
    tmp.write_bytes(protect(json.dumps(tok).encode("utf-8")))
    os.replace(tmp, TOKEN_FILE)


def delete_tokens() -> None:
    for p in (TOKEN_FILE, TOKEN_FILE.with_name(TOKEN_FILE.name + ".tmp")):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


# ------------------------------------------------------------------ compte connecté
def _expires_at(seconds) -> float:
    try:
        return time.time() + max(60, int(seconds)) - 60
    except (TypeError, ValueError):
        return time.time() + 3600


class Session:
    """Le compte Twitch connecté (un seul pour tout Multi OBS), utilisable depuis plusieurs threads.
    client_id : application Twitch utilisée pour une nouvelle connexion (une connexion garde la sienne)."""

    def __init__(self):
        self._lock = threading.Lock()            # état (court, jamais pendant une requête)
        self._refresh_lock = threading.Lock()    # un seul renouvellement à la fois (jeton à usage unique)
        self._tok: dict | None = None
        self._loaded = False
        self._checked: float | None = None       # dernier /validate réussi (time.monotonic)
        self._mods: tuple = (0.0, "", frozenset())   # (quand, pour quel compte, ids des chaînes modérées)
        self.client_id = CLIENT_ID

    # -------- état
    def _get(self) -> dict | None:
        with self._lock:
            if not self._loaded:
                self._loaded = True
                self._tok = load_tokens()
            return self._tok

    def _set(self, tok: dict | None, expected=...) -> bool:
        """Remplace la connexion (si elle est toujours expected) et l'enregistre (ou l'efface)."""
        with self._lock:
            if expected is not ... and self._tok is not expected:
                return False
            if tok is None or (self._tok or {}).get("user_id") != tok.get("user_id"):   # autre compte
                self._mods = (0.0, "", frozenset())
            self._tok, self._loaded = tok, True
            if tok is None:
                self._checked = None
                delete_tokens()
            else:
                save_tokens(tok)
            return True

    def account(self) -> dict | None:
        """{"login", "user_id"} du compte connecté, None sinon (sans réseau)."""
        tok = self._get()
        return {"login": tok.get("login", ""), "user_id": tok["user_id"]} if tok else None

    # -------- connexion, déconnexion
    def start_login(self) -> dict:
        return device_start(self.client_id)

    def finish_login(self, start: dict, stop: threading.Event) -> dict:
        """Attend l'accord sur twitch.tv/activate, vérifie et enregistre la connexion ; renvoie account()."""
        client_id = self.client_id
        tokens = device_wait(client_id, start, stop)
        info = validate(tokens["access_token"])
        if not info:
            raise TwitchError("Twitch n'a pas validé la connexion : recommence")
        old = self._get()
        tok = {"v": 1, "client_id": client_id, "access_token": tokens["access_token"],
               "refresh_token": tokens.get("refresh_token", ""), "expires_at": _expires_at(info.get("expires_in")),
               "user_id": str(info["user_id"]), "login": info.get("login", ""),
               "scopes": list(info.get("scopes") or tokens.get("scope") or [])}
        if not tok["refresh_token"]:
            raise TwitchError("Twitch n'a pas donné de jeton de renouvellement : recommence")
        self._set(tok)
        with self._lock:
            self._checked = time.monotonic()
        if old and old.get("access_token") != tok["access_token"]:   # autre compte avant : déconnecté chez Twitch
            revoke(old.get("client_id", ""), old.get("access_token", ""))
        return self.account()

    def logout(self) -> None:
        tok = self._get()
        self._set(None)
        if tok:
            revoke(tok.get("client_id", ""), tok.get("access_token", ""))

    # -------- jetons
    def _refresh(self, used: dict) -> dict:
        """Nouveau jeton d'accès ; un seul renouvellement même si plusieurs threads le demandent en même temps."""
        with self._refresh_lock:
            tok = self._get()
            if tok is None:
                raise NeedLogin("pas connecté à Twitch")
            if tok["access_token"] != used["access_token"]:   # déjà renouvelé par un autre thread
                return tok
            try:
                new = refresh(tok["client_id"], tok["refresh_token"])
            except NeedLogin:
                self._set(None, tok)
                raise
            fresh = {**tok, "access_token": new["access_token"],
                     "refresh_token": new.get("refresh_token") or tok["refresh_token"],
                     "expires_at": _expires_at(new.get("expires_in"))}
            if new.get("scope"):
                fresh["scopes"] = list(new["scope"])
            self._set(fresh, tok)
            return fresh

    def ensure(self, force_validate: bool = False) -> dict:
        """Jeton utilisable : renouvelé s'il a expiré, vérifié par /validate si ça fait une heure (ou force)."""
        tok = self._get()
        if tok is None:
            raise NeedLogin("pas connecté à Twitch")
        if time.time() >= float(tok.get("expires_at") or 0):
            tok = self._refresh(tok)
        with self._lock:
            due = force_validate or self._checked is None or time.monotonic() - self._checked >= VALIDATE_EVERY_S
        if due:
            info = validate(tok["access_token"])
            if info is None:   # jeton refusé (révoqué, expiré plus tôt que prévu…) : un renouvellement
                tok = self._refresh(tok)
                info = validate(tok["access_token"])
                if info is None:
                    self._set(None, tok)
                    raise NeedLogin("connexion à Twitch refusée : reconnecte-toi")
            with self._lock:
                self._checked = time.monotonic()
        return tok

    # -------- API Helix
    def api(self, method: str, path: str, params: dict, body: dict | None = None) -> dict:
        tok = self.ensure()
        url = f"{API_URL}{path}?{urllib.parse.urlencode(params)}"
        renewed = False
        for attempt in range(3):
            status, d = _request(method, url, body=body, headers={"Client-Id": tok["client_id"],
                                                                 "Authorization": f"Bearer {tok['access_token']}"})
            low = str(d.get("message", "")).lower()
            if status == 401 and not renewed and "scope" not in low and "moderator_id" not in low:
                tok, renewed = self._refresh(tok), True   # jeton expiré entre-temps : renouvelé, une fois
                continue
            if status == 409 and attempt < 2:             # quelqu'un d'autre modifie le bannissement : on réessaie
                time.sleep(0.6)
                continue
            break
        if 200 <= status < 300:
            return d
        raise TwitchError(explain(status, d), status)

    def _me(self) -> str:
        tok = self._get()
        if tok is None:
            raise NeedLogin("pas connecté à Twitch")
        return tok["user_id"]

    def delete_message(self, broadcaster_id: str, message_id: str) -> None:
        self.api("DELETE", "/moderation/chat", {"broadcaster_id": broadcaster_id, "moderator_id": self._me(),
                                                "message_id": message_id})

    def ban(self, broadcaster_id: str, user_id: str, duration: int = 0, reason: str = "") -> dict:
        """Bannir (duration 0) ou exclure pendant duration secondes (1 s à 2 semaines)."""
        data = {"user_id": user_id}
        if duration:
            data["duration"] = max(1, min(TIMEOUT_MAX_S, int(duration)))
        if reason:
            data["reason"] = reason[:500]
        return self.api("POST", "/moderation/bans", {"broadcaster_id": broadcaster_id, "moderator_id": self._me()},
                        {"data": data})

    def unban(self, broadcaster_id: str, user_id: str) -> None:
        self.api("DELETE", "/moderation/bans", {"broadcaster_id": broadcaster_id, "moderator_id": self._me(),
                                                "user_id": user_id})

    def moderated_channels(self, fresh: bool = False) -> frozenset:
        """Ids des chaînes dont le compte est modérateur (gardés 5 min)."""
        me = self._me()
        with self._lock:
            when, who, ids = self._mods
        if not fresh and who == me and time.monotonic() - when < MODS_CACHE_S:
            return ids
        found, cursor = set(), ""
        for _ in range(50):   # 100 par page
            params = {"user_id": me, "first": 100}
            if cursor:
                params["after"] = cursor
            d = self.api("GET", "/moderation/channels", params)
            found.update(str(c.get("broadcaster_id", "")) for c in d.get("data") or [] if isinstance(c, dict))
            cursor = (d.get("pagination") or {}).get("cursor") or ""
            if not cursor:
                break
        ids = frozenset(found)
        with self._lock:
            self._mods = (time.monotonic(), me, ids)
        return ids

    def role_in(self, broadcaster_id: str, fresh: bool = False) -> str:
        """« broadcaster » (sa propre chaîne), « mod » (modérateur) ou "" pour la chaîne broadcaster_id."""
        tok = self.ensure()
        if tok["user_id"] == str(broadcaster_id):
            return "broadcaster"
        return "mod" if str(broadcaster_id) in self.moderated_channels(fresh) else ""
