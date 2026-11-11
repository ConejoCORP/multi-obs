"""Fenêtre du chat du live, à l'identique du chat de Twitch : badges (abonnés avec leurs mois, modos, VIP, bits…),
emotes Twitch et 7TV / BTTV / FFZ (animées), cheermotes, réponses, annonces, messages mis en avant, premiers
messages, mentions de la chaîne, messages supprimés ; même police (Inter 13 px), mêmes lignes de 20 px, mêmes
couleurs. YouTube dans le même style avec ses badges (propriétaire, modérateur, membre, validé).

Nos ajouts : multi-chat Twitch + YouTube (un logo devant chaque message), fil d'actualité en bas, une fenêtre par
instance du live, « Chat personnalisé ». Lecture seule (lecteurs de livechat.py, dans des threads) ; pour écrire,
les boutons du bas ouvrent le vrai chat de chaque plateforme.
"""
import hashlib
import html
import queue
import re
import threading
import time
import urllib.request
from collections import deque

from PySide6.QtCore import (QAbstractListModel, QBuffer, QByteArray, QEvent, QIODevice, QModelIndex, QObject, QPointF,
                            QRectF, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QFontMetricsF, QImage, QImageReader,
                           QLinearGradient, QMovie, QPainter, QPainterPath, QTextBlockFormat, QTextCursor,
                           QTextDocument, QTextOption)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu,
                               QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter, QStyle, QStyledItemDelegate,
                               QToolButton, QToolTip, QVBoxLayout, QWidget)

from .. import chatassets, livechat, paths, twitch
from ..theme import YAMI, mix
from .widgets import icon

MAX_ROWS = 500           # messages gardés (bien plus quand on remonte lire : rien ne saute)
MAX_FEED = 200           # lignes du fil d'actualité
ROW = Qt.ItemDataRole.UserRole
# Couleurs du chat de Twitch (thème sombre).
BG = "#18181B"
TEXT = "#EFEFF1"
MUTED = "#ADADB8"
LINK = "#BF94FF"
NOTICE_BG = "#1F1F23"
TWITCH_PURPLE = "#9147FF"
HIGHLIGHT = "#755EBC"    # message mis en avant (points de chaîne)
MENTION = "#E91916"      # message qui mentionne la chaîne
FIRST = ("#00F593", "#57BEE6")
ANNOUNCE = {"PRIMARY": ("#9147FF", "#9147FF"), "BLUE": ("#00D6D6", "#9146FF"), "GREEN": ("#00DB84", "#57BEE6"),
            "ORANGE": ("#FFB31A", "#E0E000"), "PURPLE": ("#9146FF", "#FF75E6")}
# Mesures du chat de Twitch pour un texte de 13 px (tout suit la taille du texte).
BASE_PX, PAD_X, PAD_Y = 13, 20, 5
BADGE_FR = {"broadcaster": "Diffuseur", "moderator": "Modérateur", "vip": "VIP", "subscriber": "Abonné(e)",
            "founder": "Fondateur", "premium": "Prime Gaming", "turbo": "Turbo", "partner": "Partenaire vérifié",
            "staff": "Staff Twitch", "admin": "Admin Twitch", "artist-badge": "Artiste", "sub-gifter": "A offert des abonnements",
            "bits": "Bits", "bits-leader": "Meilleur donneur de bits", "sub-gift-leader": "Meilleur donneur d'abonnements",
            "no_audio": "Regarde sans le son", "no_video": "Écoute seulement"}
NAMES = {"twitch": "Twitch", "youtube": "YouTube"}
YT_ROLE_COLORS = {"owner": "#FFD600", "mod": "#5E84F1", "member": "#2BA640"}
YT_COLORS = ["#FF7F50", "#9ACD32", "#1E90FF", "#FF69B4", "#DAA520", "#00C8FF", "#B57EDC", "#3CB371",
             "#F08080", "#5F9EA0", "#FFA94D", "#7FD1B9"]
STATE_DOTS = {"ok": "#37D247", "connecting": "#E5AF24", "waiting": "#8B909C", "error": "#E85E75", "off": "#5B6273"}
CHAT_MODES = [("twitch", "Chat Twitch", "twitch.svg"), ("youtube", "Chat YouTube", "youtube.svg"),
              ("multi", "Multi-chat : Twitch + YouTube", "multichat.svg")]
FEED_NOTE = ("Les follows Twitch et les nouveaux abonnés YouTube ne passent pas par le chat : « Flux Twitch "
             "complet » (en haut du fil) ouvre le flux d'activité de Twitch, avec ton compte connecté dans Chrome "
             "ou Edge.")
URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)


def _luminance(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def readable(color: str, fallback: str = TEXT) -> str:
    """Couleur assez claire pour se lire sur le fond sombre du chat (comme le mode sombre de Twitch) :
    éclaircie tant que sa luminosité perçue est trop faible (un bleu pur passe en bleu lavande)."""
    c = QColor(color)
    if not c.isValid():
        c = QColor(fallback)
    h, s, l, _a = c.getHslF()
    while _luminance(c) < 0.24 and l < 0.92:
        l = min(0.92, l + 0.03)
        c.setHslF(max(h, 0.0), s, l)
    return c.name()


def _pick(key: str, palette: list) -> str:
    return palette[int(hashlib.md5(key.encode("utf-8")).hexdigest()[:6], 16) % len(palette)]


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


_chat_family: str | None = None


def chat_family() -> str:
    """Police du chat de Twitch : Inter (licence SIL OFL, livrée dans assets/fonts) ; "" si elle manque."""
    global _chat_family
    if _chat_family is None:
        _chat_family = ""
        for f in sorted((paths.ICONS.parent / "fonts").glob("Inter-*.ttf")):
            fid = QFontDatabase.addApplicationFont(str(f))
            families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if families:
                _chat_family = families[0]
    return _chat_family


def feed_events(settings: dict) -> set:
    keys = {k for k, _label in livechat.FEED_EVENTS}
    chosen = settings.get("chat_feed_events")
    return keys if chosen is None else keys & set(chosen)


class Row:
    __slots__ = ("msg", "seq", "deleted", "color", "time", "tips", "mention")

    def __init__(self, msg, seq: int, color: str, when: str = ""):
        self.msg, self.seq, self.deleted, self.color, self.time = msg, seq, False, color, when
        self.tips: list = []       # infobulles des badges et emotes (liens « t:N » du texte)
        self.mention = None        # mentionne la chaîne ? (calculé une fois)


class ChatModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == ROW:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{row.msg.author} : {row.msg.text()}"
        return None

    def append(self, rows: list) -> None:
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def prepend(self, rows: list) -> None:
        """Les plus récents en haut (fil d'actualité)."""
        self.beginInsertRows(QModelIndex(), 0, len(rows) - 1)
        self.rows[0:0] = list(reversed(rows))
        self.endInsertRows()

    def trim(self, keep: int) -> list:
        extra = len(self.rows) - keep
        if extra <= 0:
            return []
        self.beginRemoveRows(QModelIndex(), 0, extra - 1)
        removed = self.rows[:extra]
        del self.rows[:extra]
        self.endRemoveRows()
        return removed

    def trim_end(self, keep: int) -> list:
        if len(self.rows) <= keep:
            return []
        self.beginRemoveRows(QModelIndex(), keep, len(self.rows) - 1)
        removed = self.rows[keep:]
        del self.rows[keep:]
        self.endRemoveRows()
        return removed

    def touch(self, i: int) -> None:
        idx = self.index(i)
        self.dataChanged.emit(idx, idx)

    def clear(self) -> None:
        self.beginResetModel()
        self.rows = []
        self.endResetModel()


class ImageStore(QObject):
    """Logos, badges et emotes, téléchargés une seule fois ; les animées (GIF, WebP) bougent comme sur Twitch.
    « changed » : une image est arrivée ou une animée a changé (25 i/s au plus) ; « resized » : une image n'a
    pas la forme prévue (emote large), il faut remettre en page. Threads « daemon » : un téléchargement en cours
    ne retient pas Multi OBS à la fermeture."""
    changed = Signal()
    resized = Signal()
    _loaded = Signal(str, object)
    WORKERS = 4

    def __init__(self, parent=None, animate: bool = True):
        super().__init__(parent)
        self.animate = animate
        self.images: dict[str, QImage] = {}
        self.movies: dict[str, tuple] = {}     # url → (QMovie, QBuffer)
        self.ratios: dict[str, float] = {}     # url → largeur / hauteur
        self._pending: set[str] = set()
        self._jobs: queue.Queue = queue.Queue()
        for i in range(self.WORKERS):
            threading.Thread(target=self._worker, daemon=True, name=f"chat-img-{i}").start()
        self._blank = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        self._blank.fill(Qt.GlobalColor.transparent)
        self._dirty = False
        self._tick = QTimer(self, interval=40, timeout=self._emit_tick)
        self._loaded.connect(self._on_loaded)
        for key, name in (("logo:twitch", "twitch.svg"), ("logo:youtube", "youtube.svg"),
                          ("icon:moderator", "yt_moderator.svg"), ("icon:verified", "yt_verified.svg")):
            self.images[key] = icon(name).pixmap(64, 64).toImage()

    def get(self, url: str) -> QImage:
        movie = self.movies.get(url)
        if movie is not None:
            return movie[0].currentImage()
        img = self.images.get(url)
        if img is not None:
            return img
        if url.startswith("https://") and url not in self._pending:
            self._pending.add(url)
            self._jobs.put(url)
        return self._blank   # transparent en attendant (pas l'icône « image manquante » de Qt)

    def ratio(self, url: str) -> float:
        return self.ratios.get(url, 0.0)

    def _worker(self) -> None:
        while True:
            url = self._jobs.get()
            if url is None:
                return
            self._fetch(url)

    def _fetch(self, url: str) -> None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": chatassets.UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read(4_000_000)
        except OSError:
            data = b""
        try:
            self._loaded.emit(url, data)
        except RuntimeError:   # fenêtre fermée entre-temps
            pass

    def _on_loaded(self, url: str, data) -> None:
        self._pending.discard(url)
        img = QImage()
        if data:
            probe = QBuffer()
            probe.setData(QByteArray(data))
            probe.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(probe)
            reader.setDecideFormatFromContent(True)
            if self.animate and reader.supportsAnimation() and reader.imageCount() > 1:
                buf = QBuffer(self)
                buf.setData(QByteArray(data))
                buf.open(QIODevice.OpenModeFlag.ReadOnly)
                movie = QMovie(buf, QByteArray(), self)
                movie.setCacheMode(QMovie.CacheMode.CacheAll)
                if movie.isValid():
                    movie.frameChanged.connect(self._frame)
                    movie.start()
                    self.movies[url] = (movie, buf)
                    img = movie.currentImage()
            if img.isNull():
                img.loadFromData(data)
        if img.isNull():
            self.images[url] = self._blank   # jamais redemandée
        else:
            self.images[url] = img
            ratio = img.width() / max(1, img.height())
            self.ratios[url] = ratio
            if abs(ratio - 1.0) > 0.05:
                self.resized.emit()
        self.changed.emit()

    def _frame(self, _n: int) -> None:
        self._dirty = True
        if not self._tick.isActive():
            self._tick.start()

    def _emit_tick(self) -> None:
        if self._dirty:
            self._dirty = False
            self.changed.emit()
        else:
            self._tick.stop()

    def pause(self, paused: bool) -> None:
        """Animations arrêtées quand la fenêtre est réduite ou cachée (rien ne tourne pour rien)."""
        for movie, _buf in self.movies.values():
            movie.setPaused(paused or not self.animate)

    def close(self) -> None:
        with self._jobs.mutex:   # images pas encore commencées : abandonnées
            self._jobs.queue.clear()
        for _ in range(self.WORKERS):
            self._jobs.put(None)
        for movie, _buf in self.movies.values():
            movie.stop()
        self._tick.stop()


class ChatDoc(QTextDocument):
    """Texte d'un message ; ses images (badges, emotes) viennent du magasin commun."""

    def __init__(self, store: ImageStore):
        super().__init__()
        self._store = store

    def loadResource(self, kind, url):
        return self._store.get(url.toString())


class MessageDelegate(QStyledItemDelegate):
    """Une ligne du chat comme sur Twitch : [logo en multi-chat] badges, pseudo en couleur, « : », message avec
    emotes ; les lignes passent à la ligne sous les badges. Fond ou barre de couleur pour les annonces,
    abonnements, mises en avant, premiers messages, mentions ; carte pour les Super Chats YouTube.
    feed=True : ligne du fil d'actualité (logo, barre de couleur à gauche, texte court)."""
    CARD_MARGIN, CARD_PAD, GAP = 3, 6, 8

    def __init__(self, win, view, feed: bool = False):
        super().__init__(win)
        self.win, self.view, self.feed = win, view, feed
        self.docs: dict[int, tuple] = {}

    def _geometry(self, row: Row, width: float, style: dict | None = None):
        """(début du texte, largeur du texte, marge haute)."""
        if self.feed:
            text_x = 14 + self.win.logo_px + self.GAP
            return text_x, max(40.0, width - text_x - 10), PAD_Y - 2
        style = self.win.row_style(row) if style is None else style
        if style.get("card"):
            text_x = 8 + 3 + 10
            return text_x, max(40.0, width - text_x - 8 - 10), self.CARD_MARGIN + self.CARD_PAD
        text_x = PAD_X if not style.get("stripe") else max(PAD_X, style["stripe_w"] + 12)
        return text_x, max(40.0, width - text_x - PAD_X), PAD_Y

    def doc(self, row: Row, width: float) -> ChatDoc:
        key = (round(width), self.win.font_key, self.win.assets_key, row.deleted)
        cached = self.docs.get(row.seq)
        if cached and cached[0] == key:
            return cached[1]
        doc = ChatDoc(self.win.images)
        doc.setDefaultFont(self.win.chat_font)
        doc.setDocumentMargin(0)
        doc.setDefaultStyleSheet("a { text-decoration: none; }")
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(opt)
        self.win.html_width = width   # en-tête de réponse : une seule ligne, coupée par « … » comme sur Twitch
        doc.setHtml(self.win.feed_html(row) if self.feed else self.win.message_html(row))
        if not self.feed:   # lignes de 20 px comme sur Twitch (les emotes débordent un peu, pareil)
            cur = QTextCursor(doc)
            cur.select(QTextCursor.SelectionType.Document)
            fmt = QTextBlockFormat()
            fmt.setLineHeight(self.win.line_px, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
            cur.mergeBlockFormat(fmt)
        doc.setTextWidth(width)
        self.docs[row.seq] = (key, doc)
        return doc

    def sizeHint(self, option, index):
        row = index.data(ROW)
        width = self.view.viewport().width()
        style = {} if self.feed else self.win.row_style(row)
        _x, text_w, top = self._geometry(row, width, style)
        h = max(self.doc(row, text_w).size().height(), self.win.line_px) + 2 * top
        return QSize(int(width), int(h + 0.999))

    def paint(self, p: QPainter, option, index):
        row = index.data(ROW)
        r = QRectF(option.rect)
        style = {} if self.feed else self.win.row_style(row)
        text_x, text_w, top = self._geometry(row, r.width(), style)
        doc = self.doc(row, text_w)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if style.get("card"):
            card = r.adjusted(8, self.CARD_MARGIN, -8, -self.CARD_MARGIN)
            path = QPainterPath()
            path.addRoundedRect(card, 6, 6)
            accent = QColor(style["card"])
            p.fillPath(path, QColor(mix(BG, accent.name(), 0.22)))
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(card.left(), card.top(), 3, card.height()), accent)
            p.restore()
        elif style.get("bg"):
            p.fillRect(r, QColor(style["bg"]))
        if style.get("stripe"):
            c1, c2 = style["stripe"]
            grad = QLinearGradient(r.left(), r.top(), r.left(), r.bottom())
            grad.setColorAt(0, QColor(c1))
            grad.setColorAt(1, QColor(c2))
            p.fillRect(QRectF(r.left(), r.top(), style["stripe_w"], r.height()), grad)
        if option.state & QStyle.StateFlag.State_MouseOver:
            p.fillRect(r, QColor(255, 255, 255, 10))
        if self.feed:
            accent = QColor(row.msg.accent or YAMI["blue2"])
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(r.left() + 5, r.top() + 3, 3, r.height() - 6), 1.5, 1.5)
            p.fillPath(bar, accent)
            img = self.win.images.images.get(f"logo:{row.msg.platform}")
            if img is not None:
                logo = self.win.logo_px
                p.drawImage(QRectF(r.left() + 14, r.top() + top + (self.win.line_px - logo) / 2, logo, logo), img)
        p.translate(r.left() + text_x, r.top() + top)
        doc.drawContents(p)
        p.restore()

    def _anchor(self, row: Row, rect, pos) -> str:
        style = {} if self.feed else self.win.row_style(row)
        text_x, text_w, top = self._geometry(row, rect.width(), style)
        doc = self.doc(row, text_w)
        return doc.documentLayout().anchorAt(QPointF(pos.x() - rect.left() - text_x, pos.y() - rect.top() - top))

    def helpEvent(self, event, view, option, index):
        """Infobulle des badges et des emotes (comme sur Twitch), ou l'adresse d'un lien."""
        row = index.data(ROW) if index.isValid() else None
        if row is not None and event.type() == QEvent.Type.ToolTip:
            a = self._anchor(row, option.rect, event.pos())
            tip = ""
            if a.startswith("t:") and a[2:].isdigit() and int(a[2:]) < len(row.tips):
                tip = row.tips[int(a[2:])]
            elif a.startswith(("http://", "https://")):
                tip = a
            if tip:
                QToolTip.showText(event.globalPos(), tip, view)
                return True
            QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)

    def editorEvent(self, event, model, option, index):
        """Clic sur un lien du chat : il s'ouvre dans le navigateur."""
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(ROW)
            if row is not None:
                a = self._anchor(row, option.rect, event.position())
                if a.startswith(("http://", "https://")):
                    QDesktopServices.openUrl(QUrl(a))
                    return True
        return False

    def forget(self, rows) -> None:
        for row in rows:
            self.docs.pop(row.seq, None)


class ChatList(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setStyleSheet(f"QListView {{ background: {BG}; border: none; }}")
        self.verticalScrollBar().setSingleStep(24)
        self.empty_text = ""

    def relayout(self) -> None:
        self.scheduleDelayedItemsLayout()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.model() is not None and self.model().rowCount() == 0 and self.empty_text:
            p = QPainter(self.viewport())
            p.setPen(QColor(MUTED))
            p.drawText(self.viewport().rect().adjusted(24, 0, -24, 0),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            p.end()


class StatusChip(QWidget):
    """Logo + chaîne + état de la connexion ; cliquable (changer la chaîne)."""
    clicked = Signal()

    def __init__(self, platform: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(icon(f"{platform}.svg").pixmap(18, 18))
        lay.addWidget(logo)
        self.text = QLabel()
        lay.addWidget(self.text)

    def set_state(self, name: str, state: str, short: str, tip: str) -> None:
        """Pastille verte quand tout va bien ; sinon l'état en clair à côté (détail dans l'infobulle)."""
        dot = STATE_DOTS.get(state, MUTED)
        extra = "" if state == "ok" else f' <span style="color:{MUTED}">{html.escape(short)}</span>'
        self.text.setText(f'<b>{html.escape(name)}</b> <span style="color:{dot}">●</span>{extra}')
        self.setToolTip(tip)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _Bridge(QObject):
    event = Signal(object)


class _AssetLoader(QObject):
    """Badges de la chaîne et emotes 7TV / BTTV / FFZ, chargés dans un thread (chatassets)."""
    ready = Signal(object)

    def start(self, login: str, room_id: str) -> None:
        threading.Thread(target=self._work, args=(login, room_id), daemon=True, name="chat-assets").start()

    def _work(self, login: str, room_id: str) -> None:
        g = chatassets.load_global()
        c = chatassets.load_channel(login, room_id)
        data = {"badges": {**g["badges"], **c["badges"]}, "emotes": {**g["emotes"], **c["emotes"]}}
        try:
            self.ready.emit(data)
        except RuntimeError:   # fenêtre fermée entre-temps
            pass


class LiveFinder(QObject):
    """Cherche le live YouTube de la chaîne dans un thread : done((id de la vidéo, "") ou (None, explication))."""
    done = Signal(object)

    def start(self, link: str) -> None:
        threading.Thread(target=self._work, args=(link,), daemon=True, name="chat-youtube-live").start()

    def _work(self, link: str) -> None:
        label = livechat.youtube_label(link)
        try:
            vid = livechat.find_live(livechat.youtube_target(link))
            if vid:
                livechat.chat_page(vid)   # le chat est bien ouvert
                result = (vid, "")
            else:
                result = (None, f"Pas de live YouTube en cours sur {label}.\n"
                                "Lance ton live (ou programme-le), puis réessaie.")
        except livechat.ChatUnavailable as e:
            result = (None, f"Le chat YouTube de ton live n'est pas disponible : {e}.")
        except (OSError, ValueError) as e:
            result = (None, f"YouTube ne répond pas ({livechat.short_error(e)}). Réessaie dans un instant.")
        try:
            self.done.emit(result)
        except RuntimeError:
            pass


class ChatWindow(QWidget):
    """Fenêtre du chat d'une instance du live (key : son id, name : son nom) : platforms = ["twitch"],
    ["youtube"] ou les deux (multi-chat), fil d'actualité en bas. Une fenêtre par instance, chacune à sa place.
    open_native(cible, id vidéo) ouvre le vrai chat pour écrire, ou « twitch-feed » le flux d'activité de
    Twitch ; ask_link(plateforme) demande la chaîne (None si annulé) ; save() enregistre les paramètres."""
    closed = Signal()

    def __init__(self, settings: dict, platforms, open_native, ask_link, save, key: str = "", name: str = ""):
        super().__init__(None, Qt.WindowType.Window)
        self.settings, self._open_native, self._ask_link, self._save = settings, open_native, ask_link, save
        self.key = key
        self.platforms = [p for p in NAMES if p in platforms]
        self.set_name(name)
        self.setMinimumSize(300, 260)
        self.images = ImageStore(self, bool(settings.get("chat_animated", True)))
        self.images.changed.connect(self._repaint)
        self.images.resized.connect(self._relayout_soon)
        self._relayout_timer = QTimer(self, singleShot=True, interval=250, timeout=self._relayout_all)
        self._bridge = _Bridge(self)
        self._bridge.event.connect(self._on_event)
        self._assets = _AssetLoader(self)
        self._assets.ready.connect(self._on_assets)
        self.badges: dict = {}           # badges Twitch « set/version » → (image, titre)
        self.emotes: dict = {}           # emotes 7TV / BTTV / FFZ « mot » → (image, largeur, hauteur, source)
        self.assets_key = 0              # change quand badges / emotes arrivent : les messages se redessinent
        self.html_width = 400.0          # largeur du texte du message en cours de mise en page
        self._room = ""
        self.readers: dict[str, livechat._Reader] = {}
        self.youtube_video = ""
        self._queue: list = []
        self._seen: set = set()
        self._seen_order: deque = deque()
        self._seq = 0
        self._unread = 0
        self._split_ready = False
        self._flush = QTimer(self, singleShot=True, interval=80, timeout=self._flush_queue)
        self._apply_font(int(settings.get("chat_font_px", BASE_PX)))
        self._events = feed_events(settings)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget(objectName="chatHead")
        head.setStyleSheet(f"QWidget#chatHead {{ background: {YAMI['grey6']}; border-bottom: 1px solid {YAMI['grey4']}; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 6, 6, 6)
        hl.setSpacing(14)
        self.chips = {p: StatusChip(p) for p in self.platforms}
        for p, chip in self.chips.items():
            chip.clicked.connect(lambda p=p: self._change_link(p))
            hl.addWidget(chip)
        hl.addStretch(1)
        for text, tip, step in (("A−", "Texte plus petit", -1), ("A+", "Texte plus grand", 1)):
            b = self._tool(tip)
            b.setText(text)
            b.clicked.connect(lambda _=False, s=step: self._step_font(s))
            hl.addWidget(b)
        self.feed_btn = self._tool("Fil d'actualité en bas (abonnements, raids, bits, Super Chats…)", "bell.svg")
        self.feed_btn.setCheckable(True)
        self.feed_btn.toggled.connect(self._show_feed)
        hl.addWidget(self.feed_btn)
        self.pin = self._tool("Toujours devant les autres fenêtres", "pin.svg")
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self._set_on_top)
        hl.addWidget(self.pin)
        clear = self._tool("Effacer les messages affichés", "trash.svg")
        clear.clicked.connect(self.clear)
        hl.addWidget(clear)
        lay.addWidget(head)

        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(5)
        self.split.setStyleSheet(f"QSplitter::handle {{ background: {YAMI['grey5']}; }}")
        self.model = ChatModel(self)
        self.view = ChatList()
        self.view.setModel(self.model)
        self.delegate = MessageDelegate(self, self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(lambda pos: self._message_menu(self.view, pos))
        self.view.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.split.addWidget(self.view)
        self.more = QPushButton(self.view)
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setStyleSheet(f"QPushButton {{ background: {TWITCH_PURPLE}; color: white; border: none; "
                                f"border-radius: 13px; padding: 4px 14px; font-weight: 600; }}"
                                f"QPushButton:hover {{ background: #772CE8; }}")
        self.more.clicked.connect(self.scroll_to_end)
        self.more.hide()

        # Fil d'actualité : les plus récents en haut.
        self.feed_box = QWidget(objectName="feedBox")
        self.feed_box.setStyleSheet(f"QWidget#feedBox {{ background: {BG}; }}")
        fb = QVBoxLayout(self.feed_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(0)
        fh = QWidget(objectName="feedHead")
        fh.setStyleSheet(f"QWidget#feedHead {{ background: {YAMI['grey6']}; }}")
        fhl = QHBoxLayout(fh)
        fhl.setContentsMargins(10, 3, 6, 3)
        title = QLabel("Fil d'actualité")
        title.setStyleSheet(f"color: {YAMI['white4']}; font-weight: 600;")
        title.setToolTip(FEED_NOTE)
        fhl.addWidget(title)
        fhl.addStretch(1)
        if "twitch" in self.platforms:
            full = self._tool("Flux d'activité officiel de Twitch, follows compris (ton compte doit être connecté "
                              "dans Chrome ou Edge)", "twitch.svg")
            full.setText("Flux Twitch complet")
            full.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            full.clicked.connect(lambda: self._open_native("twitch-feed", ""))
            fhl.addWidget(full)
        fb.addWidget(fh)
        self.feed_model = ChatModel(self)
        self.feed_view = ChatList()
        self.feed_view.setModel(self.feed_model)
        self.feed_delegate = MessageDelegate(self, self.feed_view, feed=True)
        self.feed_view.setItemDelegate(self.feed_delegate)
        self.feed_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.feed_view.customContextMenuRequested.connect(lambda pos: self._message_menu(self.feed_view, pos))
        self.feed_view.empty_text = "Abonnements, raids, bits, Super Chats, membres… s'afficheront ici."
        fb.addWidget(self.feed_view, 1)
        self.split.addWidget(self.feed_box)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)
        lay.addWidget(self.split, 1)

        foot = QWidget(objectName="chatFoot")
        foot.setStyleSheet(f"QWidget#chatFoot {{ background: {YAMI['grey6']}; border-top: 1px solid {YAMI['grey4']}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 5, 6, 5)
        fl.setSpacing(6)
        hint = QLabel("Lecture seule · pour écrire :")
        hint.setProperty("muted", True)
        fl.addWidget(hint)
        fl.addStretch(1)
        for p in self.platforms:
            b = QPushButton(icon(f"{p}.svg"), f"Chat {NAMES[p]}")
            b.setToolTip(f"Ouvrir le vrai chat {NAMES[p]} pour écrire (fenêtre à part)")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=p: self._open_native(p, self.youtube_video if p == "youtube" else ""))
            fl.addWidget(b)
        lay.addWidget(foot)

        geo = (settings.get("chat_geometries") or {}).get(key, "")   # place de la fenêtre de cette instance
        if not (geo and self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))):
            self.resize(440, 760)
        self.apply_settings()
        self.start_readers()

    def set_name(self, name: str) -> None:
        """Titre : le chat et l'instance (« Chat Twitch — OBS Purple »), pour s'y retrouver à plusieurs."""
        what = "Multi-chat — Twitch + YouTube" if len(self.platforms) > 1 else f"Chat {NAMES[self.platforms[0]]}"
        self.setWindowTitle(f"{what} — {name}" if name else what)

    def _tool(self, tip: str, icon_name: str = "") -> QToolButton:
        b = QToolButton(objectName="flatIcon")
        if icon_name:
            b.setIcon(icon(icon_name))
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("QToolButton:checked { background: rgba(113, 140, 220, 0.35); border-radius: 4px; }")
        return b

    def apply_settings(self) -> None:
        """Réglages du chat (fil d'actualité, texte, emotes, toujours devant), à l'ouverture ou après
        « Chat personnalisé »."""
        s = self.settings
        self._events = feed_events(s)
        self.feed_btn.setChecked(bool(s.get("chat_feed", True)))
        self._show_feed(self.feed_btn.isChecked())
        self.pin.setChecked(bool(s.get("chat_on_top", False)))
        animate = bool(s.get("chat_animated", True))
        if animate != self.images.animate:
            self.images.animate = animate
            self.images.pause(not self.isVisible() or self.isMinimized())
        px = int(s.get("chat_font_px", BASE_PX))
        if px != self.font_px:
            self._apply_font(px)
        self._relayout_all()   # emotes 7TV / BTTV / FFZ peut-être activées ou coupées

    def _show_feed(self, on: bool) -> None:
        self.settings["chat_feed"] = on
        self.feed_box.setVisible(on)
        if on:
            self._apply_split()

    def _apply_split(self) -> None:
        """Fil d'actualité sur chat_feed_pct % de la hauteur (20 % par défaut), le chat au-dessus."""
        total = sum(self.split.sizes())
        if total <= 0 or not self.feed_box.isVisible():
            return
        pct = max(10, min(50, int(self.settings.get("chat_feed_pct", 20))))
        feed = round(total * pct / 100)
        self.split.setSizes([total - feed, feed])

    def showEvent(self, e):
        super().showEvent(e)
        self.images.pause(False)
        if not self._split_ready:   # tailles connues seulement une fois la fenêtre affichée
            self._split_ready = True
            QTimer.singleShot(0, self._apply_split)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.images.pause(True)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange:
            self.images.pause(self.isMinimized())

    # ------------------------------------------------------------------ lecteurs
    def _link(self, platform: str) -> str:
        if platform == "twitch":
            return twitch.channel(self.settings.get("twitch_channel", ""))
        link = self.settings.get("youtube_channel", "")
        return link if livechat.youtube_target(link) else ""

    def start_readers(self, only: str | None = None) -> None:
        """(Re)lance la lecture des chats ; une plateforme sans chaîne reste « non configurée »."""
        for p in ([only] if only in self.platforms else [] if only else self.platforms):
            old = self.readers.pop(p, None)
            if old is not None:
                old.stop()
            link = self._link(p)
            if p == "youtube":
                self.youtube_video = ""
            if not link:
                self._set_status(p, "off", "non configuré — clique pour indiquer ta chaîne")
                continue
            emit = self._bridge.event.emit
            reader = livechat.TwitchReader(link, emit) if p == "twitch" else livechat.YouTubeReader(link, emit)
            self.readers[p] = reader
            reader.start()
        names = [NAMES[p] for p in self.platforms if p in self.readers]
        self.view.empty_text = ("Les messages de ton chat " + " et ".join(names) + " s'afficheront ici."
                                if names else "Indique ta chaîne (en haut) pour voir son chat.")
        self.view.viewport().update()

    def _change_link(self, platform: str) -> None:
        if self._ask_link(platform):
            self.start_readers(platform)

    def _set_status(self, platform: str, state: str, text: str) -> None:
        if platform not in self.chips:
            return
        if platform == "twitch":
            name = self._link("twitch") or "Twitch"
        else:
            name = livechat.youtube_label(self.settings.get("youtube_channel", "")) or "YouTube"
        short = {"ok": "connecté", "connecting": "connexion…", "error": "erreur", "off": "non configuré"}.get(state)
        if short is None:   # en attente
            short = "chat désactivé" if "désactivé" in text else "pas de live"
        self.chips[platform].set_state(name, state, short, f"{NAMES[platform]} : {text}")

    def _on_event(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "status":
            self._set_status(ev[1], ev[2], ev[3])
        elif kind == "live":
            self.youtube_video = ev[2]
        elif kind == "room":
            if ev[2] != self._room:
                self._room = ev[2]
                self._assets.start(ev[3], ev[2])
        elif kind == "message":
            msg = ev[1]
            key = (msg.platform, msg.id)
            if key in self._seen:
                return
            self._seen.add(key)
            self._seen_order.append(key)
            if len(self._seen_order) > 4000:
                self._seen.discard(self._seen_order.popleft())
            self._queue.append(msg)
            if not self._flush.isActive():
                self._flush.start()
        elif kind in ("delete", "delete_author", "clear"):
            self._delete(kind, ev[1], ev[2] if len(ev) > 2 else "")

    def _on_assets(self, data: dict) -> None:
        self.badges, self.emotes = data["badges"], data["emotes"]
        self._relayout_all()

    # ------------------------------------------------------------------ messages
    def _color(self, msg) -> str:
        if msg.platform == "youtube":
            return readable(YT_ROLE_COLORS.get(msg.role) or _pick(msg.author_key or msg.author, YT_COLORS))
        return readable(msg.color or _pick(msg.author_key or msg.author, livechat.TWITCH_COLORS))

    def _flush_queue(self) -> None:
        if not self._queue:
            return
        at_end = self._at_end()
        rows, feed = [], []
        now = time.strftime("%H:%M")
        for msg in self._queue:
            color = self._color(msg)
            self._seq += 1
            rows.append(Row(msg, self._seq, color, now))
            if msg.event and msg.event in self._events:
                self._seq += 1
                feed.append(Row(msg, self._seq, color, now))
        self._queue = []
        self.model.append(rows)
        if feed:
            self.feed_model.prepend(feed)
            self.feed_delegate.forget(self.feed_model.trim_end(MAX_FEED))
            self.feed_view.scrollToTop()
        if at_end:
            self._trim(MAX_ROWS)
            self.view.scrollToBottom()
        else:
            self._unread += len(rows)
            self._trim(MAX_ROWS * 4)
            self._show_more()

    def _trim(self, keep: int) -> None:
        if len(self.model.rows) <= keep:
            return
        sb = self.view.verticalScrollBar()
        extra = self.model.rows[:len(self.model.rows) - keep]
        width = self.view.viewport().width()
        lost = sum(self.delegate.sizeHint(None, self.model.index(i)).height() for i in range(len(extra))) \
            if width > 0 else 0
        before = sb.value()
        self.delegate.forget(self.model.trim(keep))
        if lost and before > 0:   # le texte lu reste à sa place
            self.view.doItemsLayout()
            sb.setValue(max(0, before - lost))

    def _delete(self, kind: str, platform: str, target: str) -> None:
        at_end, changed = self._at_end(), False
        for i, row in enumerate(self.model.rows):
            m = row.msg
            if m.platform != platform or row.deleted:
                continue
            if (kind == "delete" and m.id == target) or (kind == "delete_author" and m.author_key == target) \
                    or kind == "clear":
                row.deleted = True
                self.model.touch(i)
                changed = True
        if changed:
            self.view.relayout()
            if at_end:   # la hauteur du message change : on reste collé en bas
                self.view.scrollToBottom()

    # ------------------------------------------------------------------ rendu, comme le chat de Twitch
    def _me(self, platform: str) -> str:
        """Nom de la chaîne, pour repérer les messages qui la mentionnent."""
        if platform == "twitch":
            return self._link("twitch").lower()
        label = livechat.youtube_label(self.settings.get("youtube_channel", ""))
        return label[1:].lower() if label.startswith("@") else ""

    def _mentions(self, row: Row) -> bool:
        if row.mention is None:
            m, me = row.msg, self._me(row.msg.platform)
            words = {w.strip(".,!?:;)(").lower() for w in m.text().split() if w.startswith("@")}
            row.mention = bool(me) and m.author_key.lower() != me and m.login != me and (
                f"@{me}" in words or (bool(m.reply) and m.reply[0].lower() == me))
        return row.mention

    def row_style(self, row: Row) -> dict:
        """Fond / barre de couleur de la ligne, comme sur Twitch ; carte pour les Super Chats YouTube."""
        m = row.msg
        if m.platform == "youtube":
            return {"card": m.accent or YAMI["blue2"]} if m.label else (
                {"bg": mix(BG, MENTION, 0.28)} if self._mentions(row) else {})
        if m.notice == "announcement":
            return {"bg": NOTICE_BG, "stripe": ANNOUNCE.get(m.announce, ANNOUNCE["PRIMARY"]), "stripe_w": 6}
        if m.notice == "highlighted-message":
            return {"bg": mix(BG, HIGHLIGHT, 0.55)}
        if m.notice:   # abonnement, réabonnement, abonnements offerts, raid…
            return {"bg": NOTICE_BG, "stripe": (TWITCH_PURPLE, TWITCH_PURPLE), "stripe_w": 4}
        if self._mentions(row):
            return {"bg": mix(BG, MENTION, 0.28)}
        if m.first:
            return {"bg": mix(BG, FIRST[1], 0.10), "stripe": FIRST, "stripe_w": 4}
        if m.reward:
            return {"bg": mix(BG, HIGHLIGHT, 0.18)}
        return {}

    def _img(self, url: str, w: float, h: float, tip_index: int | None) -> str:
        img = (f'<img src="{_esc(url)}" width="{round(w)}" height="{round(h)}" style="vertical-align: middle">')
        return f'<a href="t:{tip_index}">{img}</a>' if tip_index is not None else img

    def _emote_size(self, url: str, w: float = 0, h: float = 0):
        ratio = (w / h) if w and h else (self.images.ratio(url) or 1.0)
        return self.emote_px * ratio, self.emote_px

    def _text_html(self, text: str, row: Row, tip) -> str:
        """Texte d'un message : emotes 7TV / BTTV / FFZ, cheermotes, @mentions, liens."""
        m, me = row.msg, self._me(row.msg.platform)
        third = self.emotes if m.platform == "twitch" and self.settings.get("chat_third_party", True) else {}
        out = []
        for token in re.split(r"(\s+)", text):
            if not token:
                continue
            if token.isspace():
                out.append(" ")
                continue
            e = third.get(token)
            if e:
                w, h = self._emote_size(e[0], e[1], e[2])
                out.append(self._img(e[0], w, h, tip(f"{token}\n{e[3]}")))
                continue
            c = chatassets.cheer(token) if m.bits else None
            if c:
                out.append(self._img(c[0], self.emote_px, self.emote_px, tip(f"{c[1]} bits")))
                out.append(f'<span style="color:{c[2]};font-weight:700">{c[1]}</span>')
                continue
            if token.startswith("@") and len(token) > 1:
                name = token[1:].rstrip(".,!?:;)(").lower()
                if me and name == me:   # la chaîne est mentionnée : comme sur Twitch, en rouge
                    out.append(f'<span style="background-color:{MENTION};color:#FFFFFF;font-weight:700">'
                               f'{_esc(token)}</span>')
                else:
                    out.append(f'<span style="font-weight:700">{_esc(token)}</span>')
                continue
            if URL_RE.match(token):
                url = token if token.lower().startswith("http") else "https://" + token
                out.append(f'<a href="{_esc(url)}" style="color:{LINK}">{_esc(token)}</a>')
                continue
            out.append(_esc(token))
        return "".join(out)

    def _body_html(self, row: Row, tip) -> str:
        m = row.msg
        out = []
        for p in m.parts:
            if p[0] == "emote":
                out.append(self._img(p[1], *self._emote_size(p[1]), tip(p[2])))
            else:
                out.append(self._text_html(p[1], row, tip))
        return "".join(out)

    def _badges_html(self, m, tip) -> str:
        size, out = self.badge_px, []
        if m.platform == "twitch":
            for set_id, version in m.badges:
                entry = self.badges.get(f"{set_id}/{version}") or (
                    self.badges.get(f"{set_id}/0") if set_id == "subscriber" else None)
                if not entry:
                    continue
                url, title = entry
                months = m.badge_info.get(set_id, "")
                if set_id in ("subscriber", "founder") and months.isdigit():
                    title = f"{BADGE_FR.get(set_id, title)} depuis {months} mois"
                elif set_id in BADGE_FR and set_id != "bits":
                    title = BADGE_FR[set_id]
                out.append(self._img(url, size, size, tip(title)) + "&#8202;")
        else:
            for _yt, kind, url, title in m.badges:
                src = url if kind == "member" else f"icon:{kind}" if kind in ("moderator", "verified") else ""
                if src:
                    out.append(self._img(src, size, size, tip(title or kind)) + "&#8202;")
        return "".join(out)

    def _name_html(self, row: Row) -> str:
        m = row.msg
        if m.platform == "youtube" and m.role == "owner":   # propriétaire : nom sur fond jaune, comme YouTube
            return (f'<span style="background-color:#FFD600;color:#0F0F0F;font-weight:700">&nbsp;{_esc(m.author)}'
                    f'&nbsp;</span>')
        name = _esc(m.author)
        if m.platform == "twitch" and m.login and m.author.lower() != m.login:   # pseudo localisé : (login)
            name += f" ({_esc(m.login)})"
        return f'<span style="color:{row.color};font-weight:700">{name}</span>'

    def _header_html(self, row: Row) -> str:
        m, small = row.msg, f"font-size:{self.font_px - 1}px"
        if m.platform == "youtube":
            return (f'<span style="color:{readable(m.accent or YAMI["blue1"])};font-weight:700">{_esc(m.label)}</span>'
                    if m.label else "")
        if m.notice == "announcement":
            return f'<span style="color:{MUTED};font-weight:700;{small}">📢 Annonce</span>'
        if m.notice == "highlighted-message":
            return f'<span style="color:#FFFFFF;font-weight:700;{small}">Message mis en avant</span>'
        if m.notice:
            return f'<span style="color:{TEXT};font-weight:600">{_esc(m.label)}</span>'
        if m.reward:
            return (f'<span style="color:{MUTED};{small}">{_esc(m.author)} a utilisé une récompense de points de '
                    f'chaîne</span>')
        if m.first:
            return f'<span style="color:{FIRST[1]};font-weight:700;{small}">✦ Premier message</span>'
        return ""

    def message_html(self, row: Row) -> str:
        m = row.msg
        row.tips = []

        def tip(text: str) -> int:
            row.tips.append(text)
            return len(row.tips) - 1
        lines = []
        head = self._header_html(row)
        if head:
            lines.append(head)
        if m.reply:   # réponse : « ↳ En réponse à @pseudo : son message », sur une ligne comme sur Twitch
            small = QFont(self.chat_font)
            small.setPixelSize(self.font_px - 1)
            text = QFontMetricsF(small).elidedText(f"↳ En réponse à @{m.reply[0]} : {m.reply[1]}",
                                                   Qt.TextElideMode.ElideRight, max(40.0, self.html_width - 4))
            lines.append(f'<span style="color:{MUTED};font-size:{self.font_px - 1}px">{_esc(text)}</span>')
        # Multi-chat : logo de la plateforme sur la ligne du pseudo (sur la 1re ligne s'il n'y en a pas).
        logo = (f'{self._img("logo:" + m.platform, self.logo_px, self.logo_px, None)}&nbsp;'
                if len(self.platforms) > 1 else "")
        if m.platform == "twitch" and m.notice and m.notice not in ("highlighted-message", "announcement") \
                and m.system and not m.parts:
            if lines:   # abonnement, raid… sans message : la phrase de Twitch suffit
                lines[0] = logo + lines[0]
        else:
            badges = self._badges_html(m, tip)
            name = self._name_html(row)
            if row.deleted:
                line = f'{name}<span style="color:{TEXT}">: </span><span style="color:{MUTED}">&lt;message supprimé&gt;</span>'
            elif m.action:   # /me : tout le message à la couleur du pseudo, sans « : »
                line = f'{name} <span style="color:{row.color}">{self._body_html(row, tip)}</span>'
            elif m.parts:
                line = f'{name}<span style="color:{TEXT}">: {self._body_html(row, tip)}</span>'
            else:
                line = name
            lines.append(logo + badges + line)
        return f'<div style="color:{TEXT}">' + "".join(f"<div>{line}</div>" for line in lines) + "</div>"

    def feed_html(self, row: Row) -> str:
        """Ligne du fil : « pseudo · quoi · heure », puis le message éventuel."""
        m = row.msg
        what = f'<span style="color:{readable(m.accent or YAMI["blue1"])};font-weight:700">{_esc(m.label)}</span>'
        head = what if m.system else (f'<span style="color:{row.color};font-weight:700">{_esc(m.author)}</span>'
                                      f'<span style="color:{MUTED}"> · </span>{what}')
        when = f'<span style="color:{MUTED}"> · {row.time}</span>'
        detail = ""
        if m.parts:
            parts = "".join(_esc(p[1]) if p[0] == "text" else self._img(p[1], *self._emote_size(p[1]), None)
                            for p in m.parts)
            detail = f'<div style="color:#C3C7D0">{parts}</div>'
        return f'<div style="color:{TEXT}">{head}{when}{detail}</div>'

    def clear(self) -> None:
        for model, delegate in ((self.model, self.delegate), (self.feed_model, self.feed_delegate)):
            delegate.docs.clear()
            model.clear()
        self._unread = 0
        self.more.hide()

    # ------------------------------------------------------------------ défilement
    def _at_end(self) -> bool:
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 6

    def _scrolled(self, _value: int) -> None:
        if self._at_end():
            self._unread = 0
            self.more.hide()

    def _show_more(self) -> None:
        n = self._unread
        self.more.setText(f"↓  {n} nouveau{'x' if n > 1 else ''} message{'s' if n > 1 else ''}")
        self.more.adjustSize()
        self._place_more()
        self.more.show()
        self.more.raise_()

    def _place_more(self) -> None:
        self.more.move((self.view.width() - self.more.width()) // 2, self.view.height() - self.more.height() - 12)

    def scroll_to_end(self) -> None:
        self.view.scrollToBottom()
        self._unread = 0
        self.more.hide()
        self._trim(MAX_ROWS)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.more.isVisible():
            self._place_more()

    def _repaint(self) -> None:
        self.view.viewport().update()
        self.feed_view.viewport().update()

    # ------------------------------------------------------------------ texte, fenêtre
    def _apply_font(self, px: int) -> None:
        """Taille du texte en pixels (13 px comme Twitch) ; lignes, badges et emotes suivent ses mesures."""
        self.font_px = max(10, min(24, px))
        family = chat_family()   # Inter, comme le chat de Twitch
        font = QFont(family) if family else QFont(QApplication.font())
        font.setPixelSize(self.font_px)
        self.chat_font = font
        scale = self.font_px / BASE_PX
        self.line_px = round(20 * scale)
        self.badge_px = round(18 * scale)
        self.emote_px = round(28 * scale)
        self.logo_px = round(16 * scale)
        self.font_key = self.font_px

    def _relayout_all(self) -> None:
        at_end = self._at_end()
        self.assets_key += 1
        for view, delegate in ((self.view, self.delegate), (self.feed_view, self.feed_delegate)):
            delegate.docs.clear()
            view.relayout()
        if at_end:
            QTimer.singleShot(0, self.view.scrollToBottom)

    def _relayout_soon(self) -> None:
        """Emote large arrivée : remise en page groupée (pas une par image)."""
        if not self._relayout_timer.isActive():
            self._relayout_timer.start()

    def _step_font(self, step: int) -> None:
        self._apply_font(self.font_px + step)
        self.settings["chat_font_px"] = self.font_px
        self._relayout_all()

    def _set_on_top(self, on: bool) -> None:
        self.settings["chat_on_top"] = on
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == on:
            return
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    def _message_menu(self, view, pos) -> None:
        index = view.indexAt(pos)
        if not index.isValid():
            return
        row = index.data(ROW)
        menu = QMenu(self)
        menu.addAction("Copier le message", lambda: QApplication.clipboard().setText(row.msg.text() or row.msg.label))
        menu.addAction("Copier le pseudo", lambda: QApplication.clipboard().setText(row.msg.author))
        menu.exec(view.viewport().mapToGlobal(pos))

    def stop(self) -> None:
        for reader in self.readers.values():
            reader.stop()
        self.readers.clear()
        self.images.close()

    def closeEvent(self, e):
        self.stop()
        s = self.settings
        geos = s.get("chat_geometries")
        if not isinstance(geos, dict):
            geos = s["chat_geometries"] = {}
        geos[self.key] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        s["chat_font_px"] = self.font_px
        s["chat_on_top"] = self.pin.isChecked()
        if self.feed_box.isVisible():
            sizes = self.split.sizes()
            if sum(sizes) > 0:
                s["chat_feed_pct"] = max(10, min(50, round(100 * sizes[1] / sum(sizes))))
        self._save()
        self.closed.emit()
        super().closeEvent(e)


class ChatSettingsDialog(QDialog):
    """« Chat personnalisé » : ce que le bouton Chat de l'instance ouvre (mode, propre à chaque instance du live),
    et pour toutes les fenêtres du chat : les chaînes, le fil d'actualité, le texte et les emotes."""

    def __init__(self, settings: dict, parent=None, mode: str = "twitch", name: str = ""):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"Chat personnalisé — {name}" if name else "Chat personnalisé")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        g = QGroupBox("Chat")
        form = QFormLayout(g)
        form.setVerticalSpacing(10)
        self.mode = QComboBox()
        for value, label, icon_name in CHAT_MODES:
            self.mode.addItem(icon(icon_name), label, value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.mode.setToolTip("Propre à cette instance : une instance du live peut avoir le chat Twitch,\n"
                             "une autre le chat YouTube, une autre les deux.")
        form.addRow(f"Le bouton Chat de « {name} » ouvre" if name else "Le bouton Chat ouvre", self.mode)
        self.twitch = QLineEdit(settings.get("twitch_channel", ""))
        self.twitch.setPlaceholderText("https://www.twitch.tv/tachaine")
        form.addRow("Chaîne Twitch", self.twitch)
        self.youtube = QLineEdit(settings.get("youtube_channel", ""))
        self.youtube.setPlaceholderText("https://www.youtube.com/@tachaine")
        self.youtube.setToolTip("Lien de ta chaîne ou ton @pseudo : le chat de ton live en cours est trouvé tout seul.")
        form.addRow("Chaîne YouTube", self.youtube)
        self.native = QCheckBox("Twitch ou YouTube seul : vrai chat du site (pour écrire)")
        self.native.setToolTip("Le chat Twitch ou YouTube seul s'ouvre dans la fenêtre du site, où tu peux écrire,\n"
                               "à la place de la fenêtre de Multi OBS (pas de fil d'actualité alors).\n"
                               "Le multi-chat reste dans la fenêtre de Multi OBS.")
        self.native.setChecked(bool(settings.get("chat_native_single", False)))
        form.addRow(self.native)
        lay.addWidget(g)

        g = QGroupBox("Fil d'actualité")
        gl = QVBoxLayout(g)
        self.feed = QCheckBox("Afficher le fil d'actualité sous le chat")
        self.feed.setChecked(bool(settings.get("chat_feed", True)))
        gl.addWidget(self.feed)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hauteur du fil"))
        self.pct = QSlider(Qt.Orientation.Horizontal)
        self.pct.setRange(2, 10)   # par 5 % : 10 à 50 %
        self.pct.setValue(max(2, min(10, round(int(settings.get("chat_feed_pct", 20)) / 5))))
        self.pct_label = QLabel()
        self.pct.valueChanged.connect(lambda v: self.pct_label.setText(f"{v * 5} %"))
        self.pct_label.setText(f"{self.pct.value() * 5} %")
        row.addWidget(self.pct, 1)
        row.addWidget(self.pct_label)
        gl.addLayout(row)
        chosen = feed_events(settings)
        self.events = {}
        for key, label in livechat.FEED_EVENTS:
            box = QCheckBox(label)
            box.setChecked(key in chosen)
            gl.addWidget(box)
            self.events[key] = box
        note = QLabel(FEED_NOTE)
        note.setProperty("muted", True)
        note.setWordWrap(True)
        note.setMinimumHeight(round(note.fontMetrics().lineSpacing() * 3.3))   # 3 lignes (texte replié)
        gl.addWidget(note)
        self.feed.toggled.connect(lambda on: [w.setEnabled(on) for w in (self.pct, *self.events.values())])
        self.feed.toggled.emit(self.feed.isChecked())
        lay.addWidget(g)

        g = QGroupBox("Fenêtre")
        form = QFormLayout(g)
        self.font_px = QSpinBox()
        self.font_px.setRange(10, 24)
        self.font_px.setSuffix(" px")
        self.font_px.setValue(int(settings.get("chat_font_px", BASE_PX)))
        self.font_px.setToolTip("13 px : la taille du chat de Twitch")
        form.addRow("Taille du texte", self.font_px)
        self.third_party = QCheckBox("Emotes 7TV, BTTV et FFZ de la chaîne (comme avec leurs extensions)")
        self.third_party.setChecked(bool(settings.get("chat_third_party", True)))
        form.addRow(self.third_party)
        self.animated = QCheckBox("Emotes animées")
        self.animated.setChecked(bool(settings.get("chat_animated", True)))
        form.addRow(self.animated)
        self.on_top = QCheckBox("Toujours devant les autres fenêtres")
        self.on_top.setChecked(bool(settings.get("chat_on_top", False)))
        form.addRow(self.on_top)
        lay.addWidget(g)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.setMinimumWidth(520)

    def accept(self) -> None:
        tw, yt = self.twitch.text().strip(), self.youtube.text().strip()
        if tw and not twitch.channel(tw):
            QMessageBox.warning(self, "Chat personnalisé", "Ce n'est pas un lien de chaîne Twitch.\n"
                                "Exemple : https://www.twitch.tv/tachaine")
            return
        if yt and not livechat.youtube_target(yt):
            QMessageBox.warning(self, "Chat personnalisé", "Ce n'est pas un lien de chaîne YouTube.\n"
                                "Exemple : https://www.youtube.com/@tachaine")
            return
        s = self.settings
        s["twitch_channel"], s["youtube_channel"] = tw, yt
        s["chat_native_single"] = self.native.isChecked()
        s["chat_feed"] = self.feed.isChecked()
        s["chat_feed_pct"] = self.pct.value() * 5
        s["chat_feed_events"] = [k for k, box in self.events.items() if box.isChecked()]
        s["chat_font_px"] = self.font_px.value()
        s["chat_third_party"] = self.third_party.isChecked()
        s["chat_animated"] = self.animated.isChecked()
        s["chat_on_top"] = self.on_top.isChecked()
        super().accept()
