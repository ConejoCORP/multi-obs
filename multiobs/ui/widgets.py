"""Widgets dessinés à la main : aperçu vidéo, vumètres, pastilles de couleur, disque."""
import math
import os
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLayout, QLineEdit, QSizePolicy, QWidget

from .. import naming, paths
from ..theme import YAMI

_icon_cache: dict[str, QIcon] = {}


def limit_name(edit: QLineEdit, room: int) -> None:
    """Pas de saisie au-delà de la place qui reste avant la limite de Windows (room caractères ; négatif :
    déjà trop long). Le texte déjà là n'est jamais coupé : on peut toujours effacer."""
    edit.setMaxLength(max(1, naming.win_len(edit.text()) + max(0, room)))


def icon(name: str) -> QIcon:
    if name not in _icon_cache:
        _icon_cache[name] = QIcon(paths.icon_path(name))
    return _icon_cache[name]


def color_dot(color: str, size: int = 12) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return pm


def _pill(p: QPainter, x: float, y: float, text: str, bg: str, font: QFont) -> float:
    p.setFont(font)
    w = p.fontMetrics().horizontalAdvance(text) + 16
    h = p.fontMetrics().height() + 6
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
    p.fillPath(path, QColor(bg))
    p.setPen(QColor("white"))
    p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
    return w


class PreviewWidget(QWidget):
    """Aperçu 16:9 de la scène programme d'une instance.

    L'image arrive déjà réduite à la taille exacte du widget (thread d'aperçu) :
    l'affichage n'est qu'une copie, sans mise à l'échelle ni détourage.
    """
    doubleClicked = Signal()
    expandRequested = Signal()
    targetChanged = Signal(int, int)   # taille en pixels physiques

    COLLAPSED_H = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QImage | None = None
        self.placeholder = "Instance éteinte"
        self.badges: list[tuple[str, str]] = []
        self.corner = ""
        self.collapsed = False
        self._box_w = 0   # largeur imposée par la disposition des cartes (0 : libre, 16:9 de la largeur)
        # Autour de l'image : la couleur de la carte (du noir passerait pour du vide dans la scène OBS).
        self.background = QColor(YAMI["grey8"])
        self._screen_hooked = False
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)  # on peint tout le rectangle
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Double-clic : ouvrir la fenêtre OBS de cette instance")

    # Taille ------------------------------------------------------------- #
    def hasHeightForWidth(self) -> bool:
        return not self.collapsed and not self._box_w

    def heightForWidth(self, w: int) -> int:
        return self.COLLAPSED_H if self.collapsed else int(w * 9 / 16)

    def sizeHint(self) -> QSize:
        if self._box_w:
            return QSize(self._box_w, self.COLLAPSED_H if self.collapsed else round(self._box_w * 9 / 16))
        return QSize(400, self.COLLAPSED_H if self.collapsed else 225)

    def set_box(self, width: int) -> None:
        """Taille exacte choisie par la fenêtre (disposition des cartes) : width × 16:9, ou le bandeau masqué."""
        self._box_w = max(1, int(width))
        self.setFixedSize(self._box_w, self.COLLAPSED_H if self.collapsed else round(self._box_w * 9 / 16))

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        if collapsed:
            self.image = None
            self.setToolTip("Aperçu masqué (aucune ressource utilisée) — clic pour l'afficher")
        else:
            self.setToolTip("Double-clic : ouvrir la fenêtre OBS de cette instance")
        if self._box_w:
            self.set_box(self._box_w)
        elif collapsed:
            self.setMinimumHeight(self.COLLAPSED_H)
            self.setMaximumHeight(self.COLLAPSED_H)
        else:
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(120)
        self.updateGeometry()
        self.update()

    def _emit_target(self) -> None:
        dpr = self.devicePixelRatioF()
        self.targetChanged.emit(round(self.width() * dpr), round(self.height() * dpr))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._emit_target()

    def showEvent(self, e):
        super().showEvent(e)
        handle = self.window().windowHandle()
        if handle and not self._screen_hooked:
            # Changement d'écran (autre facteur d'échelle) : nouvelle taille physique.
            handle.screenChanged.connect(lambda _s: self._emit_target())
            self._screen_hooked = True
        self._emit_target()

    # Image -------------------------------------------------------------- #
    def set_image(self, img: QImage) -> None:
        if self.collapsed:
            return
        self.image = None if img.isNull() else img
        self.update()

    def mousePressEvent(self, e):
        if self.collapsed:
            self.expandRequested.emit()

    def mouseDoubleClickEvent(self, e):
        if not self.collapsed:
            self.doubleClicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect())
        if self.collapsed:
            p.fillRect(r, QColor(YAMI["grey8"]))
            p.setPen(QColor(YAMI["white5"]))
            f = QFont(self.font())
            f.setPointSizeF(8.5)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Aperçu masqué — clic pour l'afficher")
            p.end()
            return
        area = r   # zone des badges : l'image elle-même quand il y en a une
        if self.image:
            dpr = self.devicePixelRatioF()
            size = self.image.size().scaled(round(r.width() * dpr), round(r.height() * dpr),
                                            Qt.AspectRatioMode.KeepAspectRatio)
            if size != self.image.size():
                # Juste après un redimensionnement, en attendant l'image à la bonne taille.
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            w, h = size.width() / dpr, size.height() / dpr
            area = QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h)
            p.fillRect(r, self.background)
            p.drawImage(area, self.image)
        else:
            p.fillRect(r, QColor(YAMI["grey8"]))
            p.setPen(QColor(YAMI["white5"]))
            f = QFont(self.font())
            f.setPointSizeF(10)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.placeholder)
        if self.badges or self.corner:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            small = QFont(self.font())
            small.setPointSizeF(8)
            small.setBold(True)
            x = area.left() + 8
            for text, bg in self.badges:
                x += _pill(p, x, area.top() + 8, text, bg, small) + 6
            if self.corner:
                p.setFont(small)
                w = p.fontMetrics().horizontalAdvance(self.corner) + 16
                _pill(p, area.right() - w - 8, area.top() + 8, self.corner, "#B0" + YAMI["grey7"][1:], small)
        p.end()


class MeterWidget(QWidget):
    """Vumètres audio façon mixeur OBS (clic sur l'icône = couper / rétablir)."""
    muteToggled = Signal(str)

    ROW_H = 22
    LABEL_W = 118
    FLOOR = -60.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[tuple[str, list[tuple[float, float]], bool]] = []
        self.empty_text = ""
        self.setMouseTracking(True)
        self._update_height()

    def set_rows(self, rows, empty_text: str = "") -> None:
        count_changed = len(rows) != len(self.rows) or bool(empty_text) != bool(self.empty_text)
        self.rows = rows
        self.empty_text = empty_text
        if count_changed:
            self._update_height()
        self.update()

    def _update_height(self) -> None:
        n = len(self.rows) if self.rows else (1 if self.empty_text else 0)
        self.setFixedHeight(n * self.ROW_H)

    def _pos(self, db: float) -> float:
        if db == -math.inf or db < self.FLOOR:
            return 0.0
        return min(1.0, (db - self.FLOOR) / -self.FLOOR)

    def mousePressEvent(self, e):
        row = int(e.position().y() // self.ROW_H)
        if 0 <= row < len(self.rows) and e.position().x() < 22:
            self.muteToggled.emit(self.rows[row][0])

    def mouseMoveEvent(self, e):
        row = int(e.position().y() // self.ROW_H)
        on_icon = 0 <= row < len(self.rows) and e.position().x() < 22
        self.setCursor(Qt.CursorShape.PointingHandCursor if on_icon else Qt.CursorShape.ArrowCursor)

    def paintEvent(self, _e):
        p = QPainter(self)
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        if not self.rows:
            if self.empty_text:
                p.setPen(QColor(YAMI["white5"]))
                p.drawText(QRectF(0, 0, self.width(), self.ROW_H),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.empty_text)
            p.end()
            return
        fm = p.fontMetrics()
        bar_x = self.LABEL_W + 26
        bar_w = max(10, self.width() - bar_x)
        for i, (name, chans, muted) in enumerate(self.rows):
            y = i * self.ROW_H
            icon(("mute.svg" if muted else "audio.svg")).paint(p, 2, y + 3, 16, 16)
            p.setPen(QColor(YAMI["white5"] if muted else YAMI["white4"]))
            p.drawText(QRectF(24, y, self.LABEL_W, self.ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, self.LABEL_W - 4))
            chans = chans[:2] or [(-math.inf, -math.inf)]
            bh = 5 if len(chans) > 1 else 7
            gap = 2
            total_h = len(chans) * bh + (len(chans) - 1) * gap
            by = y + (self.ROW_H - total_h) / 2
            for mag, peak in chans:
                self._draw_bar(p, bar_x, by, bar_w, bh, mag, peak, muted)
                by += bh + gap
        p.end()

    def _draw_bar(self, p: QPainter, x, y, w, h, mag, peak, muted):
        # Zones comme OBS : vert jusqu'à -20 dB, jaune jusqu'à -9 dB, rouge au-delà.
        zones = [(self.FLOOR, -20.0, "#25A231", "#17641E"), (-20.0, -9.0, "#E5AF24", "#6E520D"),
                 (-9.0, 0.0, "#E33B57", "#7D1224")]
        level = self._pos(mag)
        for lo, hi, on, off in zones:
            a, b = self._pos(lo), self._pos(hi)
            bg = QColor(off)
            fg = QColor(on)
            if muted:
                bg, fg = QColor(YAMI["grey5"]), QColor(YAMI["grey2"])
            p.fillRect(QRectF(x + a * w, y, (b - a) * w, h), bg)
            if level > a:
                p.fillRect(QRectF(x + a * w, y, (min(level, b) - a) * w, h), fg)
        pk = self._pos(peak)
        if pk > 0:
            p.fillRect(QRectF(x + pk * w - 1, y, 2, h), QColor("white" if not muted else YAMI["grey1"]))


class SwatchButtonPainter:
    """Fabrique l'icône ronde d'une couleur pour les boutons de choix de couleur."""

    @staticmethod
    def icon(color: str, selected: bool) -> QIcon:
        size = 26
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected:
            p.setPen(QPen(QColor("white"), 2))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
        p.end()
        return QIcon(pm)


def human_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            txt = f"{n:.0f}" if n >= 100 or unit in ("o", "Ko") else f"{n:.1f}"
            return f"{txt.replace('.', ',')} {unit}"
        n /= 1024
    return ""


def human_duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d}" if h < 100 else f"{h} h"


class DiskBar(QWidget):
    """Disque d'enregistrement : espace libre (barre) + fichier / dossier de destination."""

    LOW_BYTES = 200 * 1024 ** 3   # en dessous : barre rouge
    BAR_GREEN = "#1E8528"
    BAR_RED = "#A1172E"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.free = 0
        self.total = 0
        self.folder = ""
        self.display = ""
        self.rate = 0.0
        self.setFixedHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Clic : ouvrir le dossier d'enregistrement")

    def set_state(self, free: int, total: int, folder: str, rate: float, display: str = "") -> None:
        state = (free, total, folder, rate, display or folder)
        if state != (self.free, self.total, self.folder, self.rate, self.display):
            self.free, self.total, self.folder, self.rate, self.display = state
            self.update()

    def mousePressEvent(self, e):
        if self.folder and Path(self.folder).exists():
            os.startfile(self.folder)

    def paintEvent(self, _e):
        p = QPainter(self)
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        fm = p.fontMetrics()
        w = self.width()
        if self.total:
            low = self.free < self.LOW_BYTES
            text = f"{human_size(self.free)} libres sur {human_size(self.total)}"
            if self.rate > 0:
                text += f"  ·  ≈ {human_duration(self.free / self.rate)} d'enregistrement possible"
            p.setPen(QColor(YAMI["red1"] if low else YAMI["white5"]))
            p.drawText(QRectF(0, 0, w, 13), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            # Fond gris bien visible (on voit où finit la barre), partie utilisée en vert
            # foncé, rouge foncé quand il reste moins de 200 Go.
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(YAMI["grey2"]))
            p.drawRoundedRect(QRectF(0, 16, w, 5), 2.5, 2.5)
            p.setBrush(QColor(self.BAR_RED if low else self.BAR_GREEN))
            used = max(5.0, w * (1 - self.free / self.total))
            p.drawRoundedRect(QRectF(0, 16, used, 5), 2.5, 2.5)
        if self.display:
            p.setPen(QColor(YAMI["grey1"]))
            target = self.display.replace("/", "\\")
            p.drawText(QRectF(0, 24, w, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText("Enregistre dans " + target, Qt.TextElideMode.ElideMiddle, w))
        p.end()


class RemuxBar(QWidget):
    """Conversion MKV → MP4 en cours : barre au style de celle d'OBS (Yami), fond à la couleur de
    l'instance. Remplace la barre de stockage le temps de la conversion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress = 0.0
        self.target = ""
        self.then_off = False
        self.track = QColor(YAMI["grey4"])
        self.setFixedHeight(38)
        self.setToolTip("OBS convertit l'enregistrement en MP4 (sa fenêtre de conversion est derrière)")

    def set_state(self, progress: float, target: str, then_off: bool = False) -> None:
        """then_off : l'instance s'éteindra à la fin de la conversion (écrit sur la barre)."""
        if (int(progress * 100), target, then_off) != (int(self.progress * 100), self.target, self.then_off):
            self.progress, self.target, self.then_off = progress, target, then_off
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w = self.width()
        bar = QRectF(0, 1, w, 19)
        p.setBrush(self.track)
        p.drawRoundedRect(bar, 4, 4)
        # Partie faite comme dans OBS : bleu, liseré clair en haut, plus foncé en bas.
        done = QRectF(bar.left(), bar.top(), max(6.0, bar.width() * self.progress), bar.height())
        p.setBrush(QColor(YAMI["blue3"]))
        p.drawRoundedRect(done, 3, 3)
        p.setBrush(QColor(YAMI["blue2"]))
        p.drawRect(QRectF(done.left() + 2, done.top(), max(0.0, done.width() - 4), 1))
        p.setBrush(QColor(YAMI["blue4"]))
        p.drawRect(QRectF(done.left() + 2, done.bottom() - 2, max(0.0, done.width() - 4), 2))
        f = QFont(self.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(YAMI["white1"]))
        p.drawText(bar, Qt.AlignmentFlag.AlignCenter, f"Conversion en MP4  ·  {int(self.progress * 100)} %"
                   + ("  ·  puis extinction" if self.then_off else ""))
        if self.target:
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(YAMI["grey1"]))
            p.drawText(QRectF(0, 24, w, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText("Vers " + self.target.replace("/", "\\"),
                                                  Qt.TextElideMode.ElideMiddle, w))
        p.end()


class FlowLayout(QLayout):
    """Éléments alignés à la suite, qui passent à la ligne quand la place manque."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines, line, x = [], [], area.x()
        for item in self._items:
            if item.widget() and item.widget().isHidden():
                continue
            width = item.sizeHint().width()
            if line and x + width > area.right() + 1:
                lines.append(line)
                line, x = [], area.x()
            line.append(item)
            x += width + self.spacing()
        if line:
            lines.append(line)
        y = area.y()
        for line in lines:
            line_h = max(it.sizeHint().height() for it in line)
            if apply:
                x = area.x()
                for it in line:   # centrés verticalement sur la ligne
                    hint = it.sizeHint()
                    it.setGeometry(QRect(QPoint(x, y + (line_h - hint.height()) // 2), hint))
                    x += hint.width() + self.spacing()
            y += line_h + self.spacing()
        return (y - self.spacing() if lines else y) - rect.y() + m.bottom()
