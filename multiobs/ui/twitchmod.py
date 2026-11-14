"""Modération Twitch dans les fenêtres du chat : connexion dans une fenêtre de Multi OBS (page de Twitch avec
Edge WebView2, comme dans OBS ; sinon le navigateur), compte commun à toutes les fenêtres (hub), actions
(supprimer, exclure, bannir, lever) dans des threads. Réseau et jetons : twitchauth.py ; page : webview2.py."""
import html
import json
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import applog, twitchauth
from ..theme import YAMI
from . import webview2
from .widgets import icon

# Exclusions du menu (clic droit) ; l'icône d'exclusion du chat exclut 10 minutes, comme sur Twitch.
TIMEOUTS = [(60, "1 minute"), (600, "10 minutes"), (3600, "1 heure"), (86400, "24 heures"), (604800, "1 semaine")]
ICON_TIMEOUT_S = 600
CONSOLE_URL = "https://dev.twitch.tv/console/apps"
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9]{20,40}$")


def duration_fr(seconds: int) -> str:
    """600 → « 10 minutes », 86400 → « 1 jour »…"""
    seconds = int(seconds)
    for unit, one, many in ((604800, "semaine", "semaines"), (86400, "jour", "jours"), (3600, "heure", "heures"),
                            (60, "minute", "minutes")):
        if seconds >= unit and seconds % unit == 0:
            n = seconds // unit
            return f"{n} {one if n == 1 else many}"
    return f"{seconds} seconde{'s' if seconds > 1 else ''}"


class _Job(QObject):
    done = Signal(object, object)   # (résultat, erreur)


def run(parent: QObject, fn, callback) -> None:
    """fn() dans un thread, puis callback(résultat, erreur) dans le thread de l'interface."""
    job = _Job(parent)

    def finished(result, error) -> None:
        job.deleteLater()
        callback(result, error)
    job.done.connect(finished)

    def work() -> None:
        try:
            result, error = fn(), None
        except (twitchauth.TwitchError, twitchauth.Cancelled) as e:
            result, error = None, e
        except Exception as e:   # bug : noté dans le journal (sans jeton), expliqué simplement
            applog.log(f"modération Twitch : {type(e).__name__}: {e}")
            result, error = None, twitchauth.TwitchError(f"erreur inattendue ({type(e).__name__})")
        try:
            job.done.emit(result, error)
        except RuntimeError:   # fenêtre fermée entre-temps
            pass
    threading.Thread(target=work, daemon=True, name="twitch-mod").start()


class ModHub(QObject):
    """Compte Twitch connecté, commun à toutes les fenêtres du chat. changed : connexion, déconnexion, ou
    connexion devenue invalide ; display : icônes de modération affichées ou cachées. Vérifie le jeton toutes les
    heures, comme Twitch le demande."""
    changed = Signal()
    display = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = twitchauth.Session()
        self.login_at = 0.0          # dernière connexion faite dans la fenêtre « Connexion à Twitch »
        self._timer = QTimer(self, interval=twitchauth.VALIDATE_EVERY_S * 1000, timeout=self._check)
        self._timer.start()

    def account(self) -> dict | None:
        return self.session.account()

    def set_client_id(self, client_id: str) -> None:
        self.session.client_id = (client_id or "").strip() or twitchauth.CLIENT_ID

    def _check(self) -> None:
        if self.session.account() is not None:
            run(self, lambda: self.session.ensure(force_validate=True), self._checked)

    def _checked(self, _result, error) -> None:
        if isinstance(error, twitchauth.NeedLogin):
            self.changed.emit()

    def logged_in(self) -> None:
        """Connexion réussie : les fenêtres du chat vérifient leurs droits et le disent dans le chat."""
        self.login_at = time.monotonic()
        self.changed.emit()

    def lost(self, error) -> None:
        """Une action a trouvé la connexion expirée : les fenêtres l'affichent."""
        if isinstance(error, twitchauth.NeedLogin):
            self.changed.emit()

    def logout(self) -> None:
        run(self, self.session.logout, lambda _r, _e: self.changed.emit())


_hub: ModHub | None = None


def hub() -> ModHub:
    global _hub
    if _hub is None:
        _hub = ModHub(QApplication.instance())
    return _hub


def webview_dir() -> Path:
    """Profil de la fenêtre de connexion (on reste connecté à Twitch d'une fois sur l'autre)."""
    return twitchauth.TOKEN_FILE.parent / "WebView2"


# Fenêtres ouvertes par la page de Twitch qui restent dans Multi OBS (« Continuer avec Google / Apple / Amazon ») ;
# les autres (aide, conditions…) s'ouvrent dans le navigateur.
AUTH_POPUP_HOSTS = ("twitch.tv", "google.com", "apple.com", "amazon.")
TWITCH_PURPLE, DONE_GREEN = "#9147FF", "#2E9E48"
# Où en est la page de Twitch : compte connecté (cookie « login » de Twitch), bouton « Autoriser » affiché.
PROBE_JS = """(() => {
  const m = document.cookie.match(/(?:^|; )login=([^;]*)/);
  const b = Array.from(document.querySelectorAll('button')).map(x => (x.innerText || '').trim().toLowerCase());
  return JSON.stringify({login: m ? decodeURIComponent(m[1]) : '',
                         authorize: b.some(t => t === 'autoriser' || t === 'authorize')});
})()"""
# (titre, détail dans la fenêtre intégrée, détail avec le navigateur)
STEPS = [
    ("Connecte-toi à Twitch",
     "Dans la page à droite, avec le compte du <b>diffuseur</b> ou d'un de ses <b>modérateurs</b>. Déjà "
     "connecté ? Passe à l'étape 2.<br>Twitch demande d'abord pour les cookies : « Accepter » ou « Rejeter », "
     "au choix.",
     "Clique sur <b>Ouvrir Twitch</b> : la page s'ouvre dans ton navigateur, le code déjà rempli. Connecte-toi "
     "si besoin, avec le compte du <b>diffuseur</b> ou d'un de ses <b>modérateurs</b>."),
    ("Vérifie le code, puis clique sur « Activer »",
     "Twitch doit afficher le même code qu'ici :",
     "Twitch doit afficher le même code qu'ici :"),
    ("Clique sur « Autoriser »",
     "Twitch montre ce que Multi OBS demande (voir ci-dessous) : clique sur <b>Autoriser</b>.",
     "Twitch montre ce que Multi OBS demande (voir ci-dessous) : clique sur <b>Autoriser</b>."),
    ("C'est fait",
     "Cette fenêtre se ferme toute seule, le bouclier du chat devient vert et les icônes de modération "
     "apparaissent à côté des messages.",
     "Cette fenêtre se ferme toute seule, le bouclier du chat devient vert et les icônes de modération "
     "apparaissent. Tu peux ensuite fermer l'onglet de Twitch."),
]
RIGHTS = ("<b>Multi OBS pourra</b> : supprimer des messages, exclure, bannir et lever un bannissement, voir les "
          "chaînes que tu modères.<br><b>Il ne pourra pas</b> : écrire dans le chat, voir ton mot de passe, toucher "
          "à ton compte ou à ta chaîne.<br>Connexion gardée chiffrée par Windows sur ce PC ; « Se déconnecter » "
          "(bouclier du chat) l'annule chez Twitch.")
HELP_EMBEDDED = ("<b>Un souci ?</b><br>• Page blanche ou en erreur : <i>Recharger</i>.<br>• Twitch dit que le code "
                 "est invalide ou expiré : <i>Nouveau code</i>.<br>• Mauvais compte : déconnecte-toi en haut à droite "
                 "de la page de Twitch, puis reconnecte-toi.<br>• Tu préfères ton navigateur : <i>Navigateur</i>.")
HELP_BROWSER = ("<b>Un souci ?</b><br>• Twitch dit que le code est invalide ou expiré : <i>Nouveau code</i>.<br>"
                "• Mauvais compte : déconnecte-toi de Twitch dans ton navigateur (en haut à droite), puis "
                "reconnecte-toi.")


class _Step(QWidget):
    """Une étape : pastille (numéro, ✓), titre, explication ; en cours (violet), faite (vert), à venir (gris)."""

    def __init__(self, n: int, title: str, parent=None):
        super().__init__(parent)
        self.n = n
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        self.badge = QLabel(str(n))
        self.badge.setFixedSize(24, 24)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self.badge, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel(title)
        grid.addWidget(self.title, 0, 1)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setWordWrap(True)
        grid.addWidget(self.detail, 1, 1)
        self.extra = QHBoxLayout()   # le code, à l'étape 2
        self.extra.setContentsMargins(0, 2, 0, 0)
        grid.addLayout(self.extra, 2, 1)
        grid.setColumnStretch(1, 1)
        self.state = ""
        self.set_state("todo")

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "now":
            badge = f"background: {TWITCH_PURPLE}; color: white; border-radius: 12px; font-weight: 700;"
            title, detail = f"color: {YAMI['white1']}; font-weight: 700;", f"color: {YAMI['white4']};"
        elif state == "done":
            badge = f"background: {DONE_GREEN}; color: white; border-radius: 12px; font-weight: 700;"
            title, detail = f"color: {YAMI['white4']}; font-weight: 600;", f"color: {YAMI['grey1']};"
        else:
            badge = (f"background: transparent; color: {YAMI['white5']}; border: 1px solid {YAMI['grey2']}; "
                     "border-radius: 12px;")
            title, detail = f"color: {YAMI['white5']}; font-weight: 600;", f"color: {YAMI['grey1']};"
        self.badge.setText("✓" if state == "done" else str(self.n))
        self.badge.setStyleSheet(badge)
        self.title.setStyleSheet(title)
        self.detail.setStyleSheet(detail)


class TwitchLoginDialog(QDialog):
    """Connexion à Twitch dans une fenêtre de Multi OBS, comme dans OBS : à droite la page de Twitch (Edge
    WebView2), à gauche les 4 étapes expliquées (celle en cours en violet, suivie dans la page), ce que Multi OBS
    pourra faire ou non, et « Un souci ? » (recharger, nouveau code, navigateur). Sans WebView2 (ou s'il échoue) :
    « Ouvrir Twitch » ouvre la page dans le navigateur. Le Client ID n'est demandé que si Multi OBS n'en a pas."""

    def __init__(self, settings: dict, save=None, parent=None, embedded: bool | None = None):
        super().__init__(parent)
        self.settings, self._save = settings, save
        self.hub = hub()
        self.hub.set_client_id(settings.get("twitch_client_id", ""))
        if embedded is None:   # vraie fenêtre Windows et WebView2 installé
            embedded = QGuiApplication.platformName() == "windows" and bool(webview2.runtime_version())
        self.web: webview2.WebView | None = None
        self._stop = threading.Event()
        self._start: dict | None = None
        self._deadline = 0.0
        self._gen = 0                    # chaque essai (Nouveau code) ignore les réponses des précédents
        self._closed = False
        self._step = 0                   # étape en cours (0 : pas encore de code)
        self._who = ""                   # compte connecté dans la page de Twitch
        self._problem = ""               # souci affiché à la place de l'étape (code expiré…)
        self._probing = False
        self._probe_at = 0.0
        self.setWindowTitle("Connexion à Twitch")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(16)

        side = QWidget()
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(12)
        title = QLabel("Connexion à Twitch")
        title.setStyleSheet(f"color: {YAMI['white1']}; font-size: 18px; font-weight: 700;")
        lay.addWidget(title)
        sub = QLabel("Pour modérer ton chat depuis Multi OBS (supprimer un message, exclure, bannir), en 4 étapes. "
                     "La fenêtre suit où tu en es.")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        # Client ID de l'application Twitch : seulement si Multi OBS n'en a pas (version de développement).
        self.cid_box = QWidget()
        cl = QVBoxLayout(self.cid_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cid_text = QLabel("Il manque le Client ID de l'application Twitch de Multi OBS. Crée-la une fois sur "
                          "dev.twitch.tv/console (type de client : <b>Public</b>), puis colle son Client ID ici :")
        cid_text.setWordWrap(True)
        cl.addWidget(cid_text)
        row = QHBoxLayout()
        self.cid = QLineEdit(settings.get("twitch_client_id", ""))
        self.cid.setPlaceholderText("Client ID (lettres et chiffres)")
        row.addWidget(self.cid, 1)
        console = QPushButton("dev.twitch.tv")
        console.setToolTip("Ouvre la console des développeurs de Twitch (tes applications)")
        console.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CONSOLE_URL)))
        row.addWidget(console)
        self.cid_ok = QPushButton("Continuer")
        self.cid_ok.clicked.connect(self._use_client_id)
        row.addWidget(self.cid_ok)
        cl.addLayout(row)
        lay.addWidget(self.cid_box)

        self.steps_box = QWidget()
        sl = QVBoxLayout(self.steps_box)
        sl.setContentsMargins(0, 4, 0, 0)
        sl.setSpacing(12)
        self.steps = [_Step(i + 1, t) for i, (t, _a, _b) in enumerate(STEPS)]
        for s in self.steps:
            sl.addWidget(s)
        lay.addWidget(self.steps_box)
        # Étape 1 sans page intégrée : le bouton qui ouvre le navigateur.
        self.open = QPushButton(icon("twitch.svg"), "Ouvrir Twitch")
        self.open.setProperty("role", "primary")
        self.open.clicked.connect(self._open_activate)
        self.steps[0].extra.addWidget(self.open)
        self.steps[0].extra.addStretch(1)
        # Étape 2 : le code, en grand.
        self.code = QLabel("…")
        self.code.setStyleSheet(f"color: {YAMI['white1']}; font-size: 22px; font-weight: 700; letter-spacing: 3px;")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.steps[1].extra.addWidget(self.code)
        self.copy = QPushButton("Copier")
        self.copy.setToolTip("Copier le code (à coller sur twitch.tv/activate s'il n'est pas déjà rempli)")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self._start["user_code"]
                                                                           if self._start else ""))
        self.steps[1].extra.addWidget(self.copy)
        self.steps[1].extra.addStretch(1)

        rights = QLabel(RIGHTS)
        rights.setTextFormat(Qt.TextFormat.RichText)
        rights.setWordWrap(True)
        rights.setStyleSheet(f"color: {YAMI['white4']}; background: {YAMI['grey6']}; border: 1px solid "
                             f"{YAMI['grey4']}; border-radius: 6px; padding: 8px;")
        lay.addWidget(rights)

        help_box = QWidget()
        hl = QVBoxLayout(help_box)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.help_text = QLabel()
        self.help_text.setTextFormat(Qt.TextFormat.RichText)
        self.help_text.setWordWrap(True)
        self.help_text.setStyleSheet(f"color: {YAMI['white4']};")
        hl.addWidget(self.help_text)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.reload = QPushButton("Recharger")
        self.reload.setToolTip("La page de Twitch reste blanche ou affiche une erreur")
        self.reload.clicked.connect(self._reload)
        row.addWidget(self.reload)
        self.retry = QPushButton("Nouveau code")
        self.retry.setToolTip("Code refusé ou expiré : Twitch en donne un autre")
        self.retry.clicked.connect(self._begin)
        row.addWidget(self.retry)
        self.browser = QPushButton("Navigateur")
        self.browser.setToolTip("Faire les mêmes étapes dans ton navigateur (le code y est déjà rempli)")
        self.browser.clicked.connect(self._open_activate)
        row.addWidget(self.browser)
        row.addStretch(1)
        hl.addLayout(row)
        lay.addWidget(help_box)
        lay.addStretch(1)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {YAMI['white1']}; font-weight: 600;")
        lay.addWidget(self.status)
        self.hint = QLabel()             # souci de la page intégrée (ne se charge pas, WebView2 absent…)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {YAMI['red1']};")
        self.hint.hide()
        lay.addWidget(self.hint)
        # Le panneau défile s'il ne tient pas en hauteur (petit écran) : rien n'est jamais coupé.
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(400)
        scroll.setStyleSheet("QScrollArea { background: transparent; } "
                             "QScrollArea > QWidget > QWidget { background: transparent; }")
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(scroll, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.cancel = QPushButton("Annuler")
        self.cancel.clicked.connect(self.reject)
        bottom.addWidget(self.cancel)
        left.addLayout(bottom)
        root.addLayout(left)

        # Page de Twitch (WebView2), à droite.
        self.web_box = QWidget()
        wl = QVBoxLayout(self.web_box)
        wl.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.web_box, 1)

        self._tick = QTimer(self, interval=1000, timeout=self._render_status)
        self._probe = QTimer(self, interval=1500, timeout=self._probe_page)
        self._set_mode(embedded)
        if self.hub.session.client_id:
            self._begin()
        else:
            self._need_client_id("")

    # -------- page intégrée ou navigateur
    def _set_mode(self, embedded: bool) -> None:
        self.embedded = embedded
        for step, (_t, inside, outside) in zip(self.steps, STEPS):
            step.detail.setText(inside if embedded else outside)
        self.open.setVisible(not embedded)
        self.reload.setVisible(embedded)
        self.browser.setVisible(embedded)
        self.web_box.setVisible(embedded)
        self.help_text.setText(HELP_EMBEDDED if embedded else HELP_BROWSER)
        area = self.screen().availableGeometry()
        if embedded:
            self.resize(min(1080, area.width() - 40), min(900, area.height() - 60))
        else:
            self.resize(440, min(900, area.height() - 60))
        self._render_status()

    def _web_failed(self, text: str) -> None:
        if self._closed:
            return
        self._probe.stop()
        self._set_mode(False)
        self.hint.setText(f"La fenêtre de connexion intégrée ne marche pas sur ce PC ({text}) : fais les étapes "
                          "dans ton navigateur avec « Ouvrir Twitch ».")
        self.hint.show()
        self.open.setEnabled(self._start is not None)

    def _web_loaded(self, ok: bool, error: int) -> None:
        self.hint.setVisible(not ok)
        if not ok:
            self.hint.setText(f"La page de Twitch ne se charge pas (connexion Internet ? erreur {error}) : "
                              "« Recharger », ou « Navigateur ».")

    def _external(self, url: str) -> bool:
        """Fenêtre demandée par la page : connexion Google / Apple / Amazon → reste dans Multi OBS ; le reste
        (aide, conditions…) → navigateur."""
        host = QUrl(url).host().lower()
        if any(h in host for h in AUTH_POPUP_HOSTS):
            return False
        QDesktopServices.openUrl(QUrl(url))
        return True

    def _show_page(self, url: str) -> None:
        if self.web is None:
            self.web = webview2.WebView(webview_dir(), self._external, self.web_box)
            self.web_box.layout().addWidget(self.web)
            self.web.failed.connect(self._web_failed)
            self.web.loaded.connect(self._web_loaded)
            self.web.start(url)
        else:
            try:
                self.web.navigate(url)
            except OSError as e:
                self._web_failed(str(e))
        self._probe.start()

    def _reload(self) -> None:
        if self._start and self.web is not None:
            self.hint.hide()
            self._show_page(self._start.get("verification_uri") or "https://www.twitch.tv/activate")

    def _probe_page(self) -> None:
        """Où en est la page de Twitch (étape 1, 2 ou 3) : compte connecté ? bouton « Autoriser » ?"""
        if self.web is None or self._closed or not self._start or self._problem:
            return
        if self._probing and time.monotonic() - self._probe_at < 6:   # réponse encore attendue
            return
        self._probe_at = time.monotonic()
        self._probing = self.web.run_script(PROBE_JS, self._probed)

    def _probed(self, result) -> None:
        self._probing = False
        if self._closed or result is None or self._step >= 4:
            return
        try:
            state = json.loads(json.loads(result))
        except (TypeError, ValueError):
            return
        self._who = str(state.get("login") or "")
        self._set_step(3 if state.get("authorize") else 2 if self._who else 1)

    # -------- étapes
    def _set_step(self, n: int) -> None:
        self._step = n
        for s in self.steps:
            s.set_state("done" if s.n < n or n == 4 else "now" if s.n == n else "todo")
        self._render_status()

    def _render_status(self) -> None:
        if not hasattr(self, "_tick"):   # fenêtre encore en construction
            return
        left = max(0, int(self._deadline - time.monotonic())) if self._start else 0
        if self._start and not left and self._step < 4 and not self._problem:
            self._failed("le code a expiré")
            return
        if self._problem:
            text = self._problem
        elif self._step == 0:
            text = "Demande d'un code à Twitch…"
        elif self._step == 4:
            return
        elif self.embedded:
            text = {1: "Étape 1 sur 4 : connecte-toi à Twitch dans la page à droite.",
                    2: "Étape 2 sur 4 : vérifie le code, puis clique sur « Activer ».",
                    3: "Étape 3 sur 4 : clique sur « Autoriser »."}[self._step]
        else:
            text = ("Étape 1 sur 4 : clique sur « Ouvrir Twitch »." if self._step == 1 else
                    "En attente de Twitch : dans ton navigateur, vérifie le code, « Activer » puis « Autoriser ».")
        if 1 <= self._step <= 3 and not self._problem:
            if self._who:
                text += f" (Compte Twitch dans la page : {self._who})"
            text += f" Code valable encore {max(1, left // 60)} min."
        self.status.setText(html.escape(text))

    def _need_client_id(self, why: str) -> None:
        self.cid_box.show()
        self.steps_box.hide()
        self.web_box.hide()
        self.retry.hide()
        self.status.setText(html.escape(why))
        self.cid.setFocus()

    def _use_client_id(self) -> None:
        cid = self.cid.text().strip()
        if not CLIENT_ID_RE.match(cid):
            self.status.setText("Ce n'est pas un Client ID : copie-le depuis ton application sur dev.twitch.tv "
                                "(bouton « Gérer »).")
            return
        self.settings["twitch_client_id"] = cid
        if self._save:
            self._save()
        self.hub.set_client_id(cid)
        self._begin()

    def _begin(self) -> None:
        self._stop.set()                 # attente précédente (code expiré…) abandonnée
        self._stop = threading.Event()
        self._gen += 1
        gen = self._gen
        self._start, self._problem, self._who = None, "", ""
        self._tick.stop()
        self._probe.stop()
        self.cid_box.hide()
        self.steps_box.show()
        self.web_box.setVisible(self.embedded)
        self.code.setText("…")
        self.copy.setEnabled(False)
        self.open.setEnabled(False)
        self.reload.setEnabled(False)
        self.browser.setEnabled(False)
        self.retry.show()
        self.retry.setEnabled(False)
        self.hint.hide()
        self._set_step(0)
        run(self, self.hub.session.start_login, lambda r, e: self._got_code(gen, r, e))

    def _got_code(self, gen: int, start, error) -> None:
        if self._closed or gen != self._gen:
            return
        self.retry.setEnabled(True)
        if error is not None:
            text = str(error)
            if "Client ID" in text or "Public" in text:
                self._need_client_id(f"⚠ {text}.")
            else:
                self._failed(text)
            return
        self._start = start
        self._deadline = time.monotonic() + int(start.get("expires_in") or 1800)
        self.code.setText(start["user_code"])
        for b in (self.copy, self.open, self.reload, self.browser):
            b.setEnabled(True)
        self._set_step(1)
        self._tick.start()
        if self.embedded:
            self._show_page(start.get("verification_uri") or "https://www.twitch.tv/activate")
        stop = self._stop
        run(self, lambda: self.hub.session.finish_login(start, stop), lambda r, e: self._finished(gen, r, e))

    def _open_activate(self) -> None:
        if self._start:
            QDesktopServices.openUrl(QUrl(self._start.get("verification_uri") or "https://www.twitch.tv/activate"))
            if not self.embedded and self._step == 1:
                self._set_step(2)

    def _finished(self, gen: int, account, error) -> None:
        if error is None:   # connecté (même si la fenêtre vient d'être fermée) : les fenêtres du chat suivent
            self.hub.logged_in()
        if isinstance(error, twitchauth.Cancelled) or self._closed or gen != self._gen:
            return
        self._tick.stop()
        self._probe.stop()
        if error is not None:
            self._failed(str(error))
            return
        self._set_step(4)
        for b in (self.open, self.reload, self.browser, self.retry, self.cancel):
            b.setEnabled(False)
        self.status.setText(f"✓ Connecté à Twitch : <b>{html.escape(account['login'])}</b>. La fenêtre se "
                            "ferme…")
        QTimer.singleShot(1500, self.accept)

    def _failed(self, text: str) -> None:
        """Souci : expliqué, avec quoi faire (« Nouveau code »…)."""
        self._tick.stop()
        self._probe.stop()
        low = text.lower()
        if "expiré" in low:
            what = "Le code a expiré (il dure 30 minutes) : clique sur « Nouveau code »."
        elif "refusée" in low:
            what = "Tu as refusé sur Twitch : clique sur « Nouveau code » pour recommencer."
        elif "injoignable" in low:
            what = f"Twitch ne répond pas ({text}) : vérifie ta connexion Internet, puis « Nouveau code »."
        else:
            what = f"{text[:1].upper()}{text[1:]} : clique sur « Nouveau code » pour réessayer."
        self._problem = "⚠ " + what
        for b in (self.open, self.reload, self.browser):
            b.setEnabled(False)
        self.retry.setEnabled(True)
        self.status.setText(html.escape(self._problem))

    def done(self, r: int) -> None:   # fermeture (OK, Annuler, Échap, croix) : l'attente et la page s'arrêtent
        self._closed = True
        self._stop.set()
        self._tick.stop()
        self._probe.stop()
        if self.web is not None:
            self.web.close_view()
        super().done(r)
