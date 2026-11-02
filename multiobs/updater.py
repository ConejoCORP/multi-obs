"""Mises à jour : vérification au démarrage, changelog, téléchargement et redémarrage.

La nouvelle version remplace l'exe en cours (Windows autorise à renommer un exe qui
tourne), puis se relance ; les instances OBS continuent de tourner et sont reprises.
"""
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTextBrowser, QVBoxLayout)

from . import APP_NAME, APP_VERSION, GITHUB_REPO, github
from .ui.tasks import run_task

FROZEN = getattr(sys, "frozen", False)


def cleanup_old_exe() -> None:
    """Supprime les anciens exe laissés par les mises à jour (« Multi OBS.old.exe », « .old2 »…) ;
    réessaie tant qu'un est encore verrouillé (ancienne version encore ouverte)."""
    if not FROZEN:
        return
    me = Path(sys.executable).resolve()
    locked = False
    for old in me.parent.glob("Multi OBS.old*.exe"):
        if old.resolve() == me:
            continue
        try:
            old.unlink()
        except OSError:
            locked = True
    if locked:
        QTimer.singleShot(5000, cleanup_old_exe)


class UpdateChecker(QObject):
    found = Signal(dict)
    finished = Signal(str)   # message (manuel) : « à jour » ou erreur

    def check(self, manual: bool = False, skipped: str = "") -> None:
        def work():
            try:
                rel = github.latest_release(GITHUB_REPO)
            except github.GitHubError as e:
                if manual:
                    self.finished.emit(str(e))
                return
            tag = rel.get("tag_name") or ""
            if github.is_newer(tag, APP_VERSION) and (manual or tag != skipped):
                self.found.emit(rel)
            elif manual:
                self.finished.emit(f"Multi OBS est à jour (version {APP_VERSION}).")
        threading.Thread(target=work, daemon=True).start()


class UpdateDialog(QDialog):
    LATER, SKIP, INSTALL = 0, 1, 2

    def __init__(self, release: dict, parent=None):
        super().__init__(parent)
        self.choice = self.LATER
        tag = release.get("tag_name", "")
        self.setWindowTitle("Mise à jour disponible")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumSize(560, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        title = QLabel(f"{APP_NAME} {tag.lstrip('v')} est disponible")
        title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        lay.addWidget(title)
        sub = QLabel(f"Tu as la version {APP_VERSION}. Les instances OBS ouvertes continuent de tourner "
                     "pendant la mise à jour.")
        sub.setWordWrap(True)
        sub.setProperty("muted", True)
        lay.addWidget(sub)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.get("body") or "_Pas de notes pour cette version._")
        lay.addWidget(notes, 1)
        row = QHBoxLayout()
        skip = QPushButton("Ignorer cette version")
        skip.clicked.connect(lambda: self._done(self.SKIP))
        row.addWidget(skip)
        row.addStretch(1)
        later = QPushButton("Plus tard")
        later.clicked.connect(lambda: self._done(self.LATER))
        row.addWidget(later)
        install = QPushButton("Mettre à jour")
        install.setProperty("role", "primary")
        install.setDefault(True)
        install.clicked.connect(lambda: self._done(self.INSTALL))
        row.addWidget(install)
        lay.addLayout(row)

    def _done(self, choice: int) -> None:
        self.choice = choice
        self.accept()


class Updater(QObject):
    """Câblage côté fenêtre principale."""

    def __init__(self, window, manager):
        super().__init__(window)
        self.window = window
        self.manager = manager
        self.checker = UpdateChecker(self)
        self.checker.found.connect(self._on_found)
        self.checker.finished.connect(self._on_message)
        self._manual = False

    def check_on_startup(self) -> None:
        if self.manager.settings.get("check_updates", True):
            self._manual = False
            self.checker.check(manual=False, skipped=self.manager.settings.get("skipped_version", ""))

    def check_now(self) -> None:
        self._manual = True
        self.checker.check(manual=True)

    def _on_message(self, text: str) -> None:
        if self._manual:
            QMessageBox.information(self.window, APP_NAME, text)

    def _on_found(self, release: dict) -> None:
        dlg = UpdateDialog(release, self.window)
        dlg.exec()
        if dlg.choice == UpdateDialog.SKIP:
            self.manager.settings["skipped_version"] = release.get("tag_name", "")
            self.manager.save()
        elif dlg.choice == UpdateDialog.INSTALL:
            self.install(release)

    def install(self, release: dict) -> None:
        asset = github.find_asset(release, github.SETUP_ASSET)
        if not FROZEN or not asset:
            QMessageBox.information(
                self.window, APP_NAME,
                "Cette copie de Multi OBS tourne depuis les sources : mets-la à jour avec « git pull ».\n"
                "La page de la version va s'ouvrir." if not FROZEN else
                "Cette version ne contient pas d'installateur : la page va s'ouvrir.")
            webbrowser.open(release.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases")
            return
        setup = Path(tempfile.gettempdir()) / f"Multi-OBS-Setup-{release.get('tag_name', 'new')}.exe"
        private = bool(release.get("_token_used"))

        def download(progress):
            github.download_asset(asset, setup, lambda done, total: progress(done, total,
                                  f"Téléchargement… {done // 1048576} / {max(total, 1) // 1048576} Mo"), private)

        run_task(self.window, f"Mise à jour vers {release.get('tag_name', '')}", download,
                 on_done=lambda _r: self._run_setup(setup))

    def _run_setup(self, setup: Path) -> None:
        """L'installateur attend la fermeture de Multi OBS, remplace l'exe et le relance."""
        exe = Path(sys.executable)
        try:
            subprocess.Popen([str(setup), "--update", "--dir", str(exe.parent), "--wait-pid", str(os.getpid())],
                             close_fds=True, creationflags=subprocess.DETACHED_PROCESS)
        except OSError as e:
            QMessageBox.critical(self.window, APP_NAME, f"Impossible de lancer la mise à jour :\n{e}")
            return
        self.window.quit_for_update()
