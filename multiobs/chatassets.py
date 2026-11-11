"""Badges Twitch et emotes 7TV / BTTV / FFZ, pour que le chat ressemble à celui de Twitch (avec ces
extensions), sans compte ni clé : api.ivr.fi (badges globaux et de la chaîne), 7tv.io, api.betterttv.net,
api.frankerfacez.com. Le global est chargé une fois, chaque chaîne une fois ; tout se passe dans des threads.
"""
import json
import threading
import urllib.request

UA = "MultiOBS-chat (+https://github.com/ConejoCORP/multi-obs)"
TIMEOUT = 15
_lock = threading.Lock()
_global: dict | None = None          # {"badges": {...}, "emotes": {...}} : commun à toutes les chaînes
_channels: dict[str, dict] = {}      # id de la chaîne → {"badges": {...}, "emotes": {...}}

# Cheermotes globaux de Twitch (bits) : image animée du palier, montant de la couleur du palier.
CHEER_PREFIXES = ("cheer", "doodlecheer", "biblethump", "cheerwhal", "corgo", "scoops", "uni", "showlove", "party",
                  "seemsgood", "pride", "kappa", "frankerz", "heyguys", "dansgame", "elegiggle", "trihard", "kreygasm",
                  "4head", "swiftrage", "notlikethis", "failfish", "vohiyo", "pjsalt", "mrdestructoid", "bday",
                  "ripcheer", "shamrock", "bitboss", "streamlabs", "muxy", "holidaycheer", "goal", "anon", "charity")
CHEER_TIERS = ((100000, "#F3A71A"), (10000, "#F43021"), (5000, "#0099FE"), (1000, "#1DB2A5"), (100, "#9C3EE8"),
               (1, "#979797"))


def cheer(word: str):
    """« Cheer100 » → (url de l'image du palier, montant, couleur) ; None si ce n'est pas un cheermote."""
    w = word.lower()
    for prefix in CHEER_PREFIXES:
        if w.startswith(prefix) and w[len(prefix):].isdigit():
            amount = int(w[len(prefix):])
            if amount <= 0:
                return None
            tier, color = next((t, c) for t, c in CHEER_TIERS if amount >= t)
            return (f"https://d3aqoihi2n8ty8.cloudfront.net/actions/{prefix}/dark/animated/{tier}/2.gif",
                    amount, color)
    return None


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _try(url: str):
    try:
        return _get(url)
    except (OSError, ValueError):
        return None


def _ivr_badges(data) -> dict:
    """[{set_id, versions: [{id, image_url_2x, title}]}] → {"subscriber/12": (url, titre)}."""
    out = {}
    for s in data or []:
        for v in s.get("versions") or []:
            url = v.get("image_url_2x") or v.get("image_url_1x")
            if url:
                out[f"{s.get('set_id')}/{v.get('id')}"] = (url, v.get("title") or s.get("set_id", ""))
    return out


def _seventv(data) -> dict:
    """Ensemble d'emotes 7TV → {"nom": (url 2x, largeur, hauteur, "7TV")}."""
    es = (data or {}).get("emote_set") or data or {}
    out = {}
    for e in es.get("emotes") or []:
        host = (e.get("data") or {}).get("host") or {}
        files = [f for f in host.get("files") or [] if f.get("name", "").endswith(".webp")]
        one = next((f for f in files if f.get("name") == "1x.webp"), None)
        if host.get("url") and e.get("name"):
            out[e["name"]] = ("https:" + host["url"] + "/2x.webp", (one or {}).get("width") or 0,
                              (one or {}).get("height") or 0, "7TV")
    return out


def _bttv(items) -> dict:
    return {e["code"]: (f"https://cdn.betterttv.net/emote/{e['id']}/2x.webp", 0, 0, "BTTV")
            for e in items or [] if e.get("code") and e.get("id")}


def _ffz(data) -> dict:
    out = {}
    for s in ((data or {}).get("sets") or {}).values():
        for e in s.get("emoticons") or []:
            urls = e.get("animated") or e.get("urls") or {}
            url = urls.get("2") or urls.get("1")
            if url and e.get("name"):
                out[e["name"]] = (url if url.startswith("http") else "https:" + url, e.get("width") or 0,
                                  e.get("height") or 0, "FFZ")
    return out


def load_global() -> dict:
    """Badges globaux de Twitch et emotes globales 7TV / BTTV / FFZ (une seule fois)."""
    global _global
    with _lock:
        if _global is not None:
            return _global
    emotes = {}
    for part in (_ffz(_try("https://api.frankerfacez.com/v1/set/global")),
                 _bttv(_try("https://api.betterttv.net/3/cached/emotes/global")),
                 _seventv(_try("https://7tv.io/v3/emote-sets/global"))):
        emotes.update(part)   # 7TV gagne sur BTTV, qui gagne sur FFZ (comme leurs extensions)
    badges = _ivr_badges(_try("https://api.ivr.fi/v2/twitch/badges/global"))
    data = {"badges": badges, "emotes": emotes}
    with _lock:
        if badges:   # pas mis en cache si ivr.fi ne répondait pas : nouvel essai à la prochaine chaîne
            _global = data
    return data


def load_channel(login: str, room_id: str) -> dict:
    """Badges de la chaîne (abonnés, bits…) et ses emotes 7TV / BTTV / FFZ (une seule fois par chaîne)."""
    with _lock:
        if room_id in _channels:
            return _channels[room_id]
    emotes = {}
    ffz = _try(f"https://api.frankerfacez.com/v1/room/id/{room_id}")
    emotes.update(_ffz(ffz))
    bttv = _try(f"https://api.betterttv.net/3/cached/users/twitch/{room_id}") or {}
    emotes.update(_bttv((bttv.get("channelEmotes") or []) + (bttv.get("sharedEmotes") or [])))
    emotes.update(_seventv(_try(f"https://7tv.io/v3/users/twitch/{room_id}")))
    badges = _ivr_badges(_try(f"https://api.ivr.fi/v2/twitch/badges/channel?login={login}"))
    data = {"badges": badges, "emotes": emotes}
    with _lock:
        _channels[room_id] = data
    return data
