"""Surveillance du journal d'OBS : les problèmes qu'obs-websocket ne signale pas (son qui décroche,
périphérique perdu, encodeur en panne, écriture du fichier impossible, carte graphique perdue…).

Chaque instance écrit son journal dans config/obs-studio/logs ; on lit ce qui s'y ajoute, une fois par
seconde, et on traduit les lignes connues en alertes.
"""
import re
import time
from pathlib import Path

from PySide6.QtGui import QImage

COOLDOWN_S = 60   # une même alerte ne revient pas avant une minute (OBS répète certaines lignes)

# Écran noir : petite capture de la scène à l'antenne (assez large pour qu'un petit texte reste visible).
BLACK_SHOT_WIDTH = 256
BLACK_LEVEL = 30   # sur 255 : aucun pixel plus clair que ça = écran noir (bruit et noir « vidéo » à 16 compris)


def is_black(img: QImage, level: int = BLACK_LEVEL) -> bool:
    """Image sans aucune information : aucun pixel plus clair que `level` sur aucune couleur. Ce qui est
    transparent (rien de dessiné) compte comme noir. Une image illisible n'est pas jugée noire."""
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        return False
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    data = bytes(img.constBits())[:img.sizeInBytes()]
    # Octets B, V, R, A de chaque pixel : seules les couleurs comptent (prémultipliées par l'opacité).
    return max(max(data[0::4]), max(data[1::4]), max(data[2::4])) <= level

# (motif dans la ligne du journal, message ; {0} = premier groupe du motif)
PATTERNS = [
    (r"Max audio buffering reached", "Son : tampon audio d'OBS au maximum (décalage ou coupures possibles)"),
    (r"Source (.+?) audio is lagging", "Son : « {0} » décroche, OBS redémarre la source"),
    (r"Device '(.+?)' invalidated", "Son : périphérique « {0} » perdu, OBS essaie de le retrouver"),
    (r"\[WASAPISource::TryInitialize\]:\[(.+?)\]", "Son : impossible d'ouvrir « {0} »"),
    (r"Error encoding with encoder '(.+?)'", "Vidéo : l'encodeur « {0} » est en panne, rec interrompu"),
    (r"\[obs-nvenc[^\]]*\].*(?:failed|Encode failed)", "Vidéo : erreur de l'encodeur NVENC"),
    (r"Could not write (?:packet|headers) for file|os_process_pipe_write for packet data failed",
     "Fichier : écriture du rec impossible (disque plein ou débranché ?)"),
    (r"Device Remove/Reset|Device Removed Reason", "Carte graphique perdue : OBS reconstruit l'image"),
    (r": (.+?): Error decoding (?:video|audio)", "Capture : « {0} » envoie des images ou du son illisibles"),
    (r": (.+?): Video configuration failed", "Capture : « {0} » refuse sa configuration vidéo"),
    (r"Could not create device '(.+?)'", "Capture : impossible d'ouvrir « {0} »"),
    # Un seul OBS peut faire la capture de jeu d'un même jeu : les autres enregistrent du noir.
    (r"\[game-capture: '(.+?)'\] init_pipe: failed to start pipe",
     "Capture de jeu « {0} » impossible : un autre OBS capture déjà ce jeu (prends une capture de fenêtre)"),
]
_COMPILED = [(re.compile(p), m) for p, m in PATTERNS]


def match(line: str) -> str | None:
    """Message d'alerte pour une ligne du journal d'OBS, ou None."""
    for rx, message in _COMPILED:
        m = rx.search(line)
        if m:
            return message.format(*(g.strip() for g in m.groups()))
    return None


class LogWatcher:
    """Lit ce qui s'ajoute au journal le plus récent d'une instance."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.path: Path | None = None
        self.pos = 0
        self.partial = ""
        self.last: dict[str, float] = {}

    def reset(self) -> None:
        """OBS vient d'être lancé : le prochain journal est nouveau, on le lira depuis le début."""
        self.path, self.pos, self.partial = None, 0, ""

    def _current(self) -> Path | None:
        try:
            logs = [p for p in self.logs_dir.glob("*.txt") if p.is_file()]
        except OSError:
            return None
        return max(logs, key=lambda p: p.stat().st_mtime) if logs else None

    def poll(self) -> list[str]:
        """Nouvelles alertes depuis le dernier appel (au plus une fois par minute chacune)."""
        current = self._current()
        if current is None:
            return []
        if current != self.path:
            first = self.path is None
            self.path, self.partial = current, ""
            # Journal déjà là au premier passage (Multi OBS relancé, OBS ouvert) : on ne relit pas le passé.
            self.pos = current.stat().st_size if first else 0
        try:
            with open(current, "rb") as f:
                f.seek(self.pos)
                data = f.read(1 << 20)
                self.pos = f.tell()
        except OSError:
            return []
        text = self.partial + data.decode("utf-8", "replace")
        lines = text.split("\n")
        self.partial = lines.pop()   # ligne pas encore finie
        now, out = time.monotonic(), []
        for line in lines:
            message = match(line)
            if message and now - self.last.get(message, -COOLDOWN_S) >= COOLDOWN_S:
                self.last[message] = now
                out.append(message)
        return out
