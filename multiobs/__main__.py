"""Point d'entrée de Multi OBS."""
import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

from . import APP_NAME, APP_VERSION, theme


def paint_logo(p: QPainter, x: float, y: float, size: float) -> None:
    """Logo de Multi OBS : trois anneaux aux couleurs des instances sur un disque sombre, façon logo OBS."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.YAMI["grey7"]))
    p.drawEllipse(QRectF(x, y, size, size))
    r = size * 0.22
    w = max(1.5, size * 0.075)
    centers = [(0.5, 0.30), (0.30, 0.64), (0.70, 0.64)]
    for (cx, cy), key in zip(centers, ("red", "teal", "green")):
        p.setPen(QPen(QColor(theme.accent(key, 1)), w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(x + cx * size - r, y + cy * size - r, 2 * r, 2 * r))


def app_icon() -> QIcon:
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_logo(p, 0, 0, size)
        p.end()
        ic.addPixmap(pm)
    return ic


def _arg_value(name: str) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _wait_previous(pid_text: str | None) -> None:
    """Après une mise à jour : attend que l'ancienne version soit bien fermée."""
    if not pid_text:
        return
    try:
        import psutil
        psutil.Process(int(pid_text)).wait(timeout=20)
    except Exception:
        pass


def uninstall(app: QApplication) -> int:
    import os
    import subprocess
    import psutil
    from . import installinfo, obsfiles, paths, vcam

    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\MultiOBS_SingleInstance")
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        QMessageBox.warning(None, APP_NAME, "Ferme d'abord Multi OBS, puis relance la désinstallation.")
        return 1
    running = [p for p in psutil.process_iter(["name", "exe"])
               if (p.info["name"] or "").lower() == "obs64.exe" and p.info["exe"]
               and str(paths.INSTANCES).lower() in p.info["exe"].lower()]
    if running:
        QMessageBox.warning(None, APP_NAME, "Des instances OBS de Multi OBS sont encore allumées.\n"
                                            "Éteins-les (ou ferme Multi OBS avec « Tout éteindre ») puis recommence.")
        return 1
    box = QMessageBox(QMessageBox.Icon.Question, "Désinstaller Multi OBS",
                      "Désinstaller Multi OBS ?\n\nTon OBS habituel et tes enregistrements vidéo ne sont pas touchés.")
    wipe = QCheckBox("Supprimer aussi les instances, préconfigs et réglages")
    box.setCheckBox(wipe)
    yes = box.addButton("Désinstaller", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is not yes:
        return 0
    vcam.uninstall(list(range(1, obsfiles.MAX_VCAM + 1)))
    installinfo.remove_shortcuts()
    installinfo.unregister()
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # Suppression après la fermeture de ce processus.
        target = f'rmdir /s /q "{exe.parent}"' if wipe.isChecked() else f'del /f /q "{exe}"'
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & {target}', shell=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    QMessageBox.information(None, APP_NAME, "Multi OBS a été désinstallé.")
    return 0


def main() -> int:
    # Icône propre dans la barre des tâches (sinon celle de python.exe).
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MultiOBS.Manager")
    except Exception:
        pass
    _wait_previous(_arg_value("--wait-pid"))

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    app.setStyleSheet(theme.app_qss())

    if "--uninstall" in sys.argv:
        return uninstall(app)

    # Une seule fenêtre Multi OBS à la fois.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MultiOBS_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        QMessageBox.information(None, APP_NAME, "Multi OBS est déjà ouvert.")
        return 0

    from . import applog, installinfo
    from .manager import Manager
    from .ui.main_window import MainWindow
    from .updater import Updater, cleanup_old_exe

    cleanup_old_exe()
    if getattr(sys, "frozen", False):
        installinfo.update_registered_version(APP_VERSION, Path(sys.executable))

    manager = Manager()
    manager.apply_unthrottle()   # aperçus fluides même derrière un jeu (option SafeGuard)
    win = MainWindow(manager)
    win.updater = Updater(win, manager)
    win.show()
    applog.log(f"Multi OBS {APP_VERSION} démarré")
    win.watch_vcams()
    win.apply_remote()
    if "--updated" in sys.argv:
        win.statusBar().showMessage(f"Multi OBS a été mis à jour en version {APP_VERSION}.", 15000)
    QTimer.singleShot(3000, win.updater.check_on_startup)
    code = app.exec()
    del mutex
    return code


if __name__ == "__main__":
    sys.exit(main())
