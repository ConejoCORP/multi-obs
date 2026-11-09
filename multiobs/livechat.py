"""Chat du live : Twitch, YouTube, ou les deux réunis dans le multi-chat.

- Twitch : lecture anonyme (IRC sur TLS, comme un visiteur non connecté) : il suffit du nom de la chaîne.
- YouTube : le live en cours est trouvé à partir de la chaîne (requête resolve_url de YouTube sur « /live »,
  1 Ko), puis son chat est lu comme le fait la fenêtre de chat de YouTube (page live_chat, puis requêtes
  get_live_chat toutes les ~2 s), sans compte ni clé d'API.
Les lecteurs tournent dans des threads et appellent emit(…) avec un événement : ("status", plateforme, état,
texte), ("live", "youtube", id de la vidéo), ("message", ChatMessage), ("delete", plateforme, id),
("delete_author", plateforme, auteur), ("clear", plateforme). Le multi-chat est en lecture seule : pour écrire,
on ouvre le vrai chat de la plateforme.
"""
import json
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

YT = "https://www.youtube.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
# SOCS=CAI : cookies non essentiels refusés, ce qui évite la page de consentement de YouTube en Europe.
YT_HEADERS = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8", "Cookie": "SOCS=CAI"}
YT_CLIENT_VERSION = "2.20261105.05.01"   # remplacée par celle de la page du chat dès qu'elle est lue
TWITCH_PURPLE = "#9146FF"
MEMBER_GREEN = "#0F9D58"
POINTS_TEAL = "#00C8AF"
# Fil d'actualité : ce qui passe par le chat (les follows Twitch et les abonnés YouTube n'y passent pas).
FEED_EVENTS = [("sub", "Abonnements Twitch (et abonnements offerts)"), ("raid", "Raids"), ("bits", "Bits"),
               ("donation", "Super Chats et Super Stickers"), ("member", "Membres YouTube (et abonnements offerts)"),
               ("announce", "Annonces"), ("points", "Points de chaîne (récompenses avec message)")]
_NOTICE_EVENTS = {"sub": "sub", "resub": "sub", "primepaidupgrade": "sub", "giftpaidupgrade": "sub",
                  "anongiftpaidupgrade": "sub", "subgift": "sub", "anonsubgift": "sub", "submysterygift": "sub",
                  "anonsubmysterygift": "sub", "raid": "raid", "announcement": "announce", "bitsbadgetier": "bits"}
YT_HISTORY = 20            # messages déjà là à l'ouverture du chat YouTube (Twitch n'en donne aucun)
YT_RETRY_S = 30            # pas de live : nouvel essai toutes les 30 s
YT_POLL_S = 2.0            # nouveaux messages YouTube : toutes les 2 s au plus


@dataclass
class ChatMessage:
    platform: str                 # "twitch" | "youtube"
    id: str
    author: str
    author_key: str = ""          # login Twitch / chaîne YouTube : pour « supprimer tous ses messages »
    color: str = ""               # couleur du pseudo ("" : la fenêtre en choisit une)
    parts: list = field(default_factory=list)   # [("text", "…") | ("emote", url, alt)]
    role: str = ""                # owner / mod / vip / member / sub
    action: bool = False          # « /me » sur Twitch : tout le message à la couleur du pseudo
    label: str = ""               # message mis en avant : « Super Chat · 5,00 € », « Raid »…
    accent: str = ""              # couleur du message mis en avant
    system: bool = False          # label = phrase complète avec le pseudo (abonnement Twitch…)
    event: str = ""               # pour le fil d'actualité : une des clés de FEED_EVENTS ("" : simple message)

    def text(self) -> str:
        return "".join(p[1] if p[0] == "text" else p[2] for p in self.parts)


def short_error(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"erreur {e.code}"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "délai dépassé"
    text = str(getattr(e, "reason", "") or e) or type(e).__name__
    return text[:80]


# ------------------------------------------------------------------ Twitch
TWITCH_COLORS = ["#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50", "#9ACD32", "#FF4500", "#2E8B57",
                 "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F"]
_TAG_ESC = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}
_NOTICE_LABELS = {"sub": "Abonnement", "resub": "Réabonnement", "subgift": "Abonnement offert",
                  "submysterygift": "Abonnements offerts", "giftpaidupgrade": "Abonnement",
                  "primepaidupgrade": "Abonnement", "raid": "Raid", "announcement": "Annonce",
                  "bitsbadgetier": "Bits"}


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)?", lambda m: _TAG_ESC.get(m.group(1) or "", m.group(1) or ""), value)


def parse_irc(line: str):
    """« @tags :préfixe COMMANDE params :texte » → (tags, préfixe, commande, params, texte ou None)."""
    tags = {}
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        for kv in raw.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = _unescape(v)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if line.startswith(":"):
        line, trailing = "", line[1:]
    elif " :" in line:
        line, _, trailing = line.partition(" :")
    parts = line.split()
    return tags, prefix, (parts[0] if parts else ""), parts[1:], trailing


def twitch_emote_url(emote_id: str) -> str:
    return f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark/2.0"


def _twitch_parts(text: str, emotes: str) -> list:
    """Texte découpé autour des emotes (positions en caractères, tag « emotes » de Twitch)."""
    spans = []
    for group in filter(None, (emotes or "").split("/")):
        eid, _, ranges = group.partition(":")
        for r in ranges.split(","):
            a, _, b = r.partition("-")
            if a.isdigit() and b.isdigit():
                spans.append((int(a), int(b), eid))
    parts, pos = [], 0
    for a, b, eid in sorted(spans):
        if a < pos or b < a or b >= len(text):
            continue
        if a > pos:
            parts.append(("text", text[pos:a]))
        parts.append(("emote", twitch_emote_url(eid), text[a:b + 1]))
        pos = b + 1
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return parts


def _twitch_role(badges: str) -> str:
    names = {b.split("/")[0] for b in (badges or "").split(",") if b}
    for role, badge in (("owner", "broadcaster"), ("mod", "moderator"), ("vip", "vip"), ("sub", "subscriber")):
        if badge in names:
            return role
    return ""


def _twitch_message(tags: dict, prefix: str, text: str) -> ChatMessage:
    login = (tags.get("login") or prefix.split("!", 1)[0]).lower()
    action = text.startswith("\x01ACTION ") and text.endswith("\x01")
    if action:
        text = text[8:-1]
    return ChatMessage("twitch", tags.get("id") or f"tw-{time.monotonic_ns()}", tags.get("display-name") or login,
                       login, tags.get("color", ""), _twitch_parts(text, tags.get("emotes", "")),
                       _twitch_role(tags.get("badges", "")), action)


def twitch_event(line: str):
    """Une ligne du chat Twitch → événement (voir en haut du fichier), ou None."""
    tags, prefix, cmd, params, text = parse_irc(line)
    if cmd == "PRIVMSG":
        msg = _twitch_message(tags, prefix, text or "")
        if tags.get("bits"):
            msg.label, msg.accent, msg.event = f"{tags['bits']} Bits", TWITCH_PURPLE, "bits"
        elif tags.get("custom-reward-id") or tags.get("msg-id") == "highlighted-message":
            msg.label, msg.accent, msg.event = "Points de chaîne", POINTS_TEAL, "points"
        elif tags.get("first-msg") == "1":
            msg.label, msg.accent = "Premier message", "#37D247"
        return ("message", msg)
    if cmd == "USERNOTICE":
        kind = tags.get("msg-id", "")
        if kind == "subgift" and tags.get("msg-param-community-gift-id"):
            return None   # un des N abonnements d'un don groupé : l'annonce « offre N abonnements » suffit
        msg = _twitch_message(tags, prefix, text or "")
        system = tags.get("system-msg", "").strip()
        msg.label = system or _NOTICE_LABELS.get(kind, "Twitch")
        msg.system = bool(system)
        msg.accent = TWITCH_PURPLE
        msg.event = _NOTICE_EVENTS.get(kind, "")
        return ("message", msg)
    if cmd == "CLEARMSG" and tags.get("target-msg-id"):
        return ("delete", "twitch", tags["target-msg-id"])
    if cmd == "CLEARCHAT":
        return ("delete_author", "twitch", text.strip().lower()) if text else ("clear", "twitch")
    return None


class _Reader(threading.Thread):
    def __init__(self, platform: str, emit, name: str):
        super().__init__(daemon=True, name=name)
        self.platform = platform
        self._emit = emit
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def emit(self, *event) -> None:
        if self._stop.is_set():
            return
        try:
            self._emit(event)
        except RuntimeError:   # fenêtre fermée entre-temps
            self._stop.set()

    def status(self, state: str, text: str) -> None:
        self.emit("status", self.platform, state, text)


class TwitchReader(_Reader):
    """Chat Twitch d'une chaîne, en lecture anonyme ; se reconnecte tout seul."""
    HOST, PORT = "irc.chat.twitch.tv", 6697

    def __init__(self, channel: str, emit):
        super().__init__("twitch", emit, "chat-twitch")
        self.channel = channel.lower()
        self._sock = None
        self._joined = False

    def stop(self) -> None:
        super().stop()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def run(self) -> None:
        delay = 2
        while not self.stopped:
            self.status("connecting", f"connexion à #{self.channel}…")
            try:
                self._session()
                delay = 2
            except OSError as e:
                if self.stopped:
                    break
                self.status("error", f"déconnecté ({short_error(e)}), nouvel essai dans {delay} s")
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 30)

    def _session(self) -> None:
        raw = socket.create_connection((self.HOST, self.PORT), timeout=10)
        sock = self._sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.HOST)
        try:
            sock.settimeout(1.0)
            for line in ("CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS SCHMOOPIIE",
                         f"NICK justinfan{random.randint(10000, 99999)}", f"JOIN #{self.channel}"):
                sock.sendall((line + "\r\n").encode("utf-8"))
            buf, last, pinged = b"", time.monotonic(), False
            self._joined, joined_at, warned = False, time.monotonic(), False
            while not self.stopped:
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    idle = time.monotonic() - last
                    if idle > 90:
                        raise OSError("plus de réponse du serveur")
                    if idle > 60 and not pinged:   # Twitch envoie un PING toutes les ~5 min : on vérifie avant
                        sock.sendall(b"PING :tmi.twitch.tv\r\n")
                        pinged = True
                    if not self._joined and not warned and time.monotonic() - joined_at > 15:
                        self.status("error", f"#{self.channel} ne répond pas : vérifie le nom de la chaîne")
                        warned = True
                    continue
                if not data:
                    raise OSError("connexion fermée")
                last, pinged = time.monotonic(), False
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    self._line(sock, line.decode("utf-8", "replace"))
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _line(self, sock, line: str) -> None:
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode("utf-8"))
            return
        tags, _prefix, cmd, _params, text = parse_irc(line)
        if cmd == "ROOMSTATE":
            if not self._joined:
                self._joined = True
                self.status("ok", f"connecté à #{self.channel}")
        elif cmd == "RECONNECT":
            raise OSError("le serveur demande une reconnexion")
        elif cmd == "NOTICE" and text:
            self.status("error", text[:120])
        else:
            event = twitch_event(line)
            if event:
                self.emit(*event)


# ------------------------------------------------------------------ YouTube
class ChatUnavailable(Exception):
    """Pas de chat à lire (pas de live, chat désactivé, live terminé…) : on réessaie plus tard."""


def youtube_target(link: str):
    """Lien ou nom de chaîne YouTube → ("video", id) ou ("channel", adresse de la chaîne) ; None sinon.
    Accepte @pseudo, youtube.com/@pseudo, /channel/UC…, /c/…, /user/…, ou le lien d'un live (watch, live, youtu.be)."""
    s = (link or "").strip()
    if not s:
        return None
    m = re.search(r"(?:youtu\.be/|[?&]v=|youtube\.com/(?:live|shorts|embed)/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", s)
    if m:
        return ("video", m.group(1))
    m = re.search(r"youtube\.com/(@[\w.\-]{3,30}|channel/UC[\w-]{22}|c/[^/?#\s]+|user/[^/?#\s]+)", s, re.IGNORECASE)
    if m:
        return ("channel", f"{YT}/{m.group(1)}")
    if re.fullmatch(r"UC[\w-]{22}", s):
        return ("channel", f"{YT}/channel/{s}")
    if re.fullmatch(r"@?[\w.\-]{3,30}", s):
        return ("channel", f"{YT}/@{s.lstrip('@')}")
    return None


def youtube_label(link: str) -> str:
    """Nom lisible : « @pseudo », « channel/UC… » ou « la vidéo … »."""
    t = youtube_target(link)
    if not t:
        return ""
    return f"la vidéo {t[1]}" if t[0] == "video" else t[1].split("youtube.com/", 1)[1]


def youtube_chat_url(video_id: str) -> str:
    return f"{YT}/live_chat?is_popout=1&v={video_id}"


def _get(url: str, timeout: float = 12) -> str:
    req = urllib.request.Request(url, headers=YT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _post_json(url: str, body: dict, timeout: float = 12) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={**YT_HEADERS, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _context(version: str) -> dict:
    return {"client": {"clientName": "WEB", "clientVersion": version, "hl": "fr", "gl": "FR"}}


def find_live(target, version: str = YT_CLIENT_VERSION) -> str | None:
    """Vidéo du live en cours (ou programmé) de la chaîne, None s'il n'y en a pas."""
    if target[0] == "video":
        return target[1]
    d = _post_json(f"{YT}/youtubei/v1/navigation/resolve_url?prettyPrint=false",
                   {"context": _context(version), "url": target[1] + "/live"})
    return ((d.get("endpoint") or {}).get("watchEndpoint") or {}).get("videoId")


def _yt_text(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text") or (r.get("emoji") or {}).get("emojiId", "") for r in obj.get("runs") or [])


def chat_page(video_id: str) -> dict:
    """Page du chat du live : clé, version du client et continuation du « Chat en direct » (tous les messages)."""
    html = _get(youtube_chat_url(video_id))
    m = re.search(r'(?:window\["ytInitialData"\]|var ytInitialData)\s*=\s*(\{.+?\});\s*</script>', html, re.S)
    if not m:
        raise ChatUnavailable("chat introuvable")
    data = json.loads(m.group(1))
    contents = data.get("contents") or {}
    lcr = contents.get("liveChatRenderer")
    if not lcr:
        reason = _yt_text((contents.get("messageRenderer") or {}).get("text"))
        raise ChatUnavailable(reason or "chat désactivé pour ce live")
    if lcr.get("isReplay"):
        raise ChatUnavailable("live terminé")
    cont = None
    items = ((((lcr.get("header") or {}).get("liveChatHeaderRenderer") or {}).get("viewSelector") or {})
             .get("sortFilterSubMenuRenderer") or {}).get("subMenuItems") or []
    if len(items) > 1:   # [« Top Chat » (filtré), « Chat en direct »]
        cont = ((items[1].get("continuation") or {}).get("reloadContinuationData") or {}).get("continuation")
    if not cont:
        for c in lcr.get("continuations") or []:
            cont = next((v.get("continuation") for v in c.values() if isinstance(v, dict)), None)
            if cont:
                break
    if not cont:
        raise ChatUnavailable("chat fermé")
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    ver = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html)
    return {"key": key.group(1) if key else "", "version": ver.group(1) if ver else YT_CLIENT_VERSION,
            "continuation": cont}


def _argb(value) -> str:
    try:
        return f"#{int(value) & 0xFFFFFF:06X}"
    except (TypeError, ValueError):
        return ""


def _yt_parts(runs) -> list:
    parts = []
    for r in runs or []:
        if "text" in r:
            parts.append(("text", r["text"]))
        elif "emoji" in r:
            e = r["emoji"]
            thumbs = (e.get("image") or {}).get("thumbnails") or []
            alt = (e.get("shortcuts") or [e.get("emojiId", "")])[0]
            if e.get("isCustomEmoji") and thumbs:
                best = max(thumbs, key=lambda t: t.get("width", 0) if t.get("width", 0) <= 64 else -1)
                parts.append(("emote", best["url"], alt))
            else:   # emoji Unicode : écrit tel quel
                parts.append(("text", e.get("emojiId") or alt))
    return parts


def _yt_role(badges) -> str:
    role = ""
    for b in badges or []:
        r = b.get("liveChatAuthorBadgeRenderer") or {}
        kind = (r.get("icon") or {}).get("iconType", "")
        if kind == "OWNER":
            return "owner"
        if kind == "MODERATOR":
            role = "mod"
        elif r.get("customThumbnail") and not role:
            role = "member"
    return role


def _yt_item(item: dict):
    for kind, r in (item or {}).items():
        if not isinstance(r, dict):
            continue
        msg = ChatMessage("youtube", r.get("id", ""), _yt_text(r.get("authorName")) or "?",
                          r.get("authorExternalChannelId", ""), "", _yt_parts((r.get("message") or {}).get("runs")),
                          _yt_role(r.get("authorBadges")))
        if kind == "liveChatTextMessageRenderer":
            return msg
        if kind == "liveChatPaidMessageRenderer":
            msg.label = f"Super Chat · {_yt_text(r.get('purchaseAmountText'))}"
            msg.accent, msg.event = _argb(r.get("headerBackgroundColor")) or "#FFCA28", "donation"
            return msg
        if kind == "liveChatPaidStickerRenderer":
            sticker = (((r.get("sticker") or {}).get("accessibility") or {}).get("accessibilityData") or {}).get("label", "")
            msg.label = f"Super Sticker · {_yt_text(r.get('purchaseAmountText'))}"
            msg.parts = [("text", sticker)] if sticker else []
            msg.accent, msg.event = _argb(r.get("backgroundColor")) or "#FFCA28", "donation"
            return msg
        if kind == "liveChatMembershipItemRenderer":
            msg.label = _yt_text(r.get("headerPrimaryText")) or _yt_text(r.get("headerSubtext")) or "Nouveau membre"
            msg.accent, msg.role, msg.event = MEMBER_GREEN, msg.role or "member", "member"
            return msg
        if kind == "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer":
            h = (r.get("header") or {}).get("liveChatSponsorshipsHeaderRenderer") or {}
            msg.author = _yt_text(h.get("authorName")) or msg.author
            msg.label = _yt_text(h.get("primaryText")) or "Abonnements offerts"
            msg.accent, msg.event = MEMBER_GREEN, "member"
            return msg
        if kind == "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer":
            return msg
    return None


def youtube_events(actions) -> list:
    """Actions de get_live_chat → événements (voir en haut du fichier)."""
    out = []
    for a in actions or []:
        if "addChatItemAction" in a or "replaceChatItemAction" in a:
            body = a.get("addChatItemAction") or a.get("replaceChatItemAction") or {}
            msg = _yt_item(body.get("item") or body.get("replacementItem"))
            if msg is not None and msg.id:
                out.append(("message", msg))
        elif "removeChatItemAction" in a or "markChatItemAsDeletedAction" in a:
            target = (a.get("removeChatItemAction") or a.get("markChatItemAsDeletedAction") or {}).get("targetItemId")
            if target:
                out.append(("delete", "youtube", target))
        elif "removeChatItemByAuthorAction" in a or "markChatItemsByAuthorAsDeletedAction" in a:
            body = a.get("removeChatItemByAuthorAction") or a.get("markChatItemsByAuthorAsDeletedAction") or {}
            if body.get("externalChannelId"):
                out.append(("delete_author", "youtube", body["externalChannelId"]))
    return out


def get_live_chat(info: dict, continuation: str):
    """Une requête du chat : (événements, continuation suivante ou None si le chat est fini, délai conseillé en s)."""
    url = f"{YT}/youtubei/v1/live_chat/get_live_chat?prettyPrint=false" + (f"&key={info['key']}" if info["key"] else "")
    d = _post_json(url, {"context": _context(info["version"]), "continuation": continuation})
    lc = (d.get("continuationContents") or {}).get("liveChatContinuation")
    if lc is None:
        return [], None, 0
    nxt, timeout_ms = None, 0
    for c in lc.get("continuations") or []:
        for v in c.values():
            if isinstance(v, dict) and v.get("continuation"):
                nxt, timeout_ms = v["continuation"], int(v.get("timeoutMs") or 0)
                break
        if nxt:
            break
    return youtube_events(lc.get("actions")), nxt, timeout_ms / 1000


class YouTubeReader(_Reader):
    """Chat du live YouTube de la chaîne : attend le live s'il n'a pas commencé, le suit jusqu'à la fin."""

    def __init__(self, link: str, emit):
        super().__init__("youtube", emit, "chat-youtube")
        self.target = youtube_target(link)
        self.label = youtube_label(link)
        self.video_id = ""
        self.version = YT_CLIENT_VERSION

    def _pause(self, state: str, text: str, seconds: float) -> None:
        self.status(state, text)
        self._stop.wait(seconds)

    def run(self) -> None:
        while not self.stopped:
            self.status("connecting", f"recherche du live de {self.label}…")
            try:
                vid = find_live(self.target, self.version)
            except (OSError, ValueError) as e:
                self._pause("error", f"YouTube injoignable ({short_error(e)}), nouvel essai dans {YT_RETRY_S} s",
                            YT_RETRY_S)
                continue
            if not vid:
                self._pause("waiting", f"pas de live en cours sur {self.label} (nouvel essai toutes les "
                            f"{YT_RETRY_S} s)", YT_RETRY_S)
                continue
            try:
                self._follow(vid)
            except ChatUnavailable as e:
                self._pause("waiting", f"{e} (nouvel essai dans {YT_RETRY_S} s)", YT_RETRY_S)
            except (OSError, ValueError, KeyError) as e:
                self._pause("error", f"chat YouTube : {short_error(e)}, nouvel essai dans 15 s", 15)
            finally:
                self.video_id = ""

    def _follow(self, video_id: str) -> None:
        info = chat_page(video_id)
        self.version = info["version"]
        self.video_id = video_id
        self.emit("live", "youtube", video_id)
        self.status("ok", "connecté au chat du live")
        cont, first, errors = info["continuation"], True, 0
        while not self.stopped:
            try:
                events, nxt, wait_s = get_live_chat(info, cont)
                errors = 0
            except (OSError, ValueError) as e:
                errors += 1
                if errors >= 5:
                    raise
                self.status("connecting", f"chat YouTube : {short_error(e)}, nouvel essai…")
                self._stop.wait(min(2 * errors, 10))
                continue
            if first:   # historique du chat : seulement les derniers messages
                events = [e for e in events if e[0] == "message"][-YT_HISTORY:]
                first = False
                self.status("ok", "connecté au chat du live")
            for e in events:
                self.emit(*e)
            if not nxt:
                raise ChatUnavailable("live terminé")
            cont = nxt
            self._stop.wait(min(wait_s or YT_POLL_S, YT_POLL_S))
