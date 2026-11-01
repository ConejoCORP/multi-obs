"""Chat Twitch dans une fenêtre à part, en un clic, depuis la carte de l'OBS du live.

Le lien de la chaîne est dans les paramètres. Le chat s'ouvre dans une fenêtre « appli » de Chrome ou d'Edge
(sans onglets ni barre d'adresse, avec le compte Twitch déjà connecté dans le navigateur), sinon dans le
navigateur par défaut.
"""
import re
import subprocess
import winreg
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def channel(link: str) -> str:
    """« https://www.twitch.tv/StundZow », « twitch.tv/stundzow/ », « StundZow » → « stundzow » ("" si rien)."""
    s = (link or "").strip()
    m = re.search(r"twitch\.tv/(?:popout/)?([A-Za-z0-9_]{2,25})", s, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"@?[A-Za-z0-9_]{2,25}", s):
        return s.lstrip("@").lower()
    return ""


def chat_url(name: str) -> str:
    return f"https://www.twitch.tv/popout/{name}/chat?popout=&darkpopout"   # chat en thème sombre


def is_live_instance(inst) -> bool:
    """Le bouton du chat est sur l'OBS du live : type « live » ou « live » dans le nom."""
    return inst.rec_type == "live" or "live" in inst.name.lower()


def _app_browser() -> str | None:
    """Chrome, sinon Edge (toujours là sous Windows 11) : ils savent ouvrir une page en fenêtre « appli »."""
    for exe in ("chrome.exe", "msedge.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe) as k:
                    path = winreg.QueryValue(k, None)
            except OSError:
                continue
            if path and Path(path.strip('"')).exists():
                return path.strip('"')
    return None


def open_chat(link: str) -> bool:
    """Ouvre le chat de la chaîne dans une fenêtre à part. False si le lien n'est pas une chaîne Twitch."""
    name = channel(link)
    if not name:
        return False
    url = chat_url(name)
    exe = _app_browser()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=420,820"], close_fds=True)
            return True
        except OSError:
            pass
    return QDesktopServices.openUrl(QUrl(url))
