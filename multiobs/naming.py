"""Nom des fichiers d'enregistrement : NOM-TYPE-VIDEO-DATE, appliqué à toutes les instances."""
import re
import time
import unicodedata

# Types d'enregistrement par défaut (modifiables dans les paramètres) : (clé du nom de fichier, libellé).
DEFAULT_REC_TYPES = [("cam", "Cam"), ("ecran", "Écran"), ("live", "Live")]
DEFAULT_TEMPLATE = "{user}-{type}-{video}-{date}_{time}"

# Équivalents des marqueurs de date d'OBS, pour l'aperçu.
_OBS_DATE = {"{date}": "%CCYY-%MM-%DD", "{time}": "%hh-%mm-%ss"}

# Chemin complet d'un enregistrement (dossier + nom + extension), en caractères Windows. Windows s'arrête à
# 259 (MAX_PATH) : au-delà, OBS n'arrive pas à créer le fichier, ou c'est la conversion en MP4, l'Explorateur
# ou le logiciel de montage qui échouent. Marge pour « (2) » qu'OBS ajoute quand le nom existe déjà.
PATH_LIMIT = 250
EXT_LEN = 4   # « .mkv », « .mp4 », « .mov »


def clean(text: str) -> str:
    """Retire ce qui est interdit dans un nom de fichier (et % qu'OBS interpréterait)."""
    return re.sub(r'[\\/:*?"<>|%]+', "", text).strip()


def type_key(label: str) -> str:
    """Libellé -> clé utilisée dans le nom des fichiers : « Écran » -> « ecran », « Face cam » -> « face-cam »."""
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "type"


def rec_types(settings: dict | None) -> list[tuple[str, str]]:
    """Types définis par l'utilisateur (ou ceux par défaut)."""
    out = []
    for item in (settings or {}).get("rec_types") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] and item[1]:
            out.append((str(item[0]), str(item[1])))
    return out or list(DEFAULT_REC_TYPES)


def type_label(key: str, settings: dict | None) -> str:
    return dict(rec_types(settings)).get(key, key)


def guess_type(name: str, settings: dict | None = None) -> str:
    """Type le plus probable d'après le nom de l'instance (« Écran 2 » -> écran)."""
    types = rec_types(settings)
    n = type_key(name)
    for key, label in types:
        if key in n or type_key(label) in n:
            return key
    synonyms = {"ecran": ("screen", "desktop", "bureau"), "live": ("stream", "twitch", "direct"),
                "cam": ("webcam", "camera", "face")}
    for key, words in synonyms.items():
        if any(w in n for w in words) and key in dict(types):
            return key
    return types[0][0]


def _collapse(s: str) -> str:
    # Champs vides : « Bernard--2026 » -> « Bernard-2026 ».
    s = re.sub(r"([-_ ])[-_ ]+", r"\1", s)
    return s.strip("-_ ") or "Enregistrement"


def obs_format(template: str, user: str, rec_type: str, video: str, suffix: str = "") -> str:
    """Format passé à OBS (FilenameFormatting) ; OBS remplace la date au début du rec."""
    s = (template or DEFAULT_TEMPLATE)
    s = s.replace("{user}", clean(user)).replace("{type}", rec_type).replace("{video}", clean(video))
    for k, v in _OBS_DATE.items():
        s = s.replace(k, v)
    s = _collapse(s)
    if suffix:
        s += "-" + clean(suffix)
    return s


def expand(fmt: str) -> str:
    """Nom du fichier qu'OBS fera de ce format maintenant (marqueurs de date remplacés)."""
    now = time.localtime()
    repl = {"%CCYY": time.strftime("%Y", now), "%MM": time.strftime("%m", now),
            "%DD": time.strftime("%d", now), "%hh": time.strftime("%H", now),
            "%mm": time.strftime("%M", now), "%ss": time.strftime("%S", now)}
    for k, v in repl.items():
        fmt = fmt.replace(k, v)
    return fmt


def preview(template: str, user: str, rec_type: str, video: str, suffix: str = "") -> str:
    return expand(obs_format(template, user, rec_type, video, suffix))


def win_len(text: str) -> int:
    """Longueur pour Windows (UTF-16 : un emoji compte pour 2)."""
    return len(text.encode("utf-16-le")) // 2


def path_length(folder: str, fmt: str) -> int:
    """Longueur du chemin complet du prochain enregistrement : dossier, nom, extension."""
    return win_len(str(folder).rstrip("/\\")) + 1 + win_len(expand(fmt)) + EXT_LEN


def limit_note(room: int, inst_name: str = "") -> tuple[str, str]:
    """Message sous le nom de la vidéo selon la place restante : (texte, état « over » / « near » / "")."""
    where = f", dossier de « {inst_name} »" if inst_name else ""
    if room < 0:
        s = "s" if -room > 1 else ""
        return (f"⚠ Nom trop long de {-room} caractère{s} pour Windows (chemin du fichier{where}) : "
                "raccourcis-le, sinon il sera coupé", "over")
    if room == 0:
        return f"Longueur maximale atteinte (limite de Windows{where})", "near"
    if room <= 15:
        s = "s" if room > 1 else ""
        return f"Encore {room} caractère{s} possible{s} (limite de Windows{where})", "near"
    return "", ""


def cut(text: str, length: int) -> str:
    """Les `length` premiers caractères Windows de text (sans couper un emoji en deux)."""
    out = ""
    for ch in text:
        if win_len(out + ch) > length:
            break
        out += ch
    return out.rstrip(" -_")
