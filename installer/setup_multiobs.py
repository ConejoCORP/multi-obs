"""Installateur de Multi OBS (le seul fichier publié sur GitHub).

Il contient l'application. Avant d'installer, il regarde sur GitHub si une version plus
récente existe : si oui, il télécharge le nouvel installateur et lui passe la main.

Installation par utilisateur, sans droits administrateur :
  %LOCALAPPDATA%\\Programs\\Multi OBS (modifiable), raccourcis menu Démarrer / bureau,
  entrée « Applications installées » pour la désinstallation.

Options en ligne de commande :
  --silent  --dir <dossier>  --no-start-menu  --no-desktop  --no-shortcuts  --no-register  --no-launch
  --no-online
  --update --dir <dossier> [--wait-pid <pid>]   (utilisé par la mise à jour automatique de l'appli)
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multiobs import APP_VERSION, GITHUB_REPO, github, installinfo  # noqa: E402

BG, BASE, INPUT, TEXT, MUTED, ACCENT, ACCENT_HI = (
    "#1D1F26", "#272A33", "#3C404D", "#FFFFFF", "#969696", "#284CB8", "#476BD7")


def payload_path() -> Path:
    """L'application embarquée dans l'installateur."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "payload" / "Multi-OBS.exe"
    return ROOT / "dist" / "Multi-OBS.exe"


def newer_release() -> dict | None:
    """Release GitHub plus récente que l'appli embarquée (None si à jour ou hors ligne)."""
    try:
        rel = github.latest_release(GITHUB_REPO)
    except Exception:
        return None
    if github.is_newer(rel.get("tag_name", ""), APP_VERSION) and github.find_asset(rel, github.SETUP_ASSET):
        return rel
    return None


def hand_over(release: dict, args: list[str], progress=lambda d, t: None) -> None:
    """Télécharge l'installateur plus récent et le lance avec les mêmes options."""
    asset = github.find_asset(release, github.SETUP_ASSET)
    dest = Path(tempfile.gettempdir()) / f"Multi-OBS-Setup-{release.get('tag_name', 'new')}.exe"
    github.download_asset(asset, dest, progress, private=bool(release.get("_token_used")))
    subprocess.Popen([str(dest), *args, "--no-online"], close_fds=True)


def wait_for_pid(pid: int, timeout: float = 30) -> None:
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        ctypes.windll.kernel32.CloseHandle(handle)


def free_old_name(exe: Path) -> Path:
    """Nom libre pour l'exe remplacé : « Multi OBS.old.exe », ou « .old2 », « .old3 »… quand l'exe d'une
    mise à jour précédente tourne encore (Multi OBS pas relancé depuis) : verrouillé, il ne peut pas être
    supprimé. Multi OBS fait le ménage au démarrage."""
    for n in range(1, 30):
        old = exe.with_name("Multi OBS.old.exe" if n == 1 else f"Multi OBS.old{n}.exe")
        try:
            old.unlink(missing_ok=True)
            return old
        except OSError:
            continue
    raise OSError("aucun nom libre pour l'ancien exe")


def put_exe(target_dir: Path) -> Path:
    """Copie l'appli embarquée à sa place (en gardant l'ancienne en cas d'échec)."""
    src = payload_path()
    if not src.exists():
        raise RuntimeError("Installateur incomplet : l'application n'est pas incluse.")
    target_dir.mkdir(parents=True, exist_ok=True)
    exe = target_dir / installinfo.EXE_NAME
    old = None
    tmp = exe.with_name("Multi OBS.new.exe")
    shutil.copyfile(src, tmp)
    for _ in range(20):   # l'appli peut mettre un instant à libérer son exe
        try:
            if exe.exists():
                old = free_old_name(exe)
                exe.rename(old)
            tmp.rename(exe)
            break
        except OSError:
            time.sleep(0.5)
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Multi OBS est ouvert : ferme-le, puis relance l'installation.")
    try:
        if old is not None:
            old.unlink(missing_ok=True)
    except OSError:
        pass   # l'appli le supprimera au prochain démarrage
    return exe


def install(target_dir: Path, desktop: bool, shortcuts: bool, register: bool,
            status=lambda text: None, start_menu: bool = True) -> Path:
    status(f"Installation de Multi OBS {APP_VERSION}…")
    exe = put_exe(target_dir)
    if shortcuts and (desktop or start_menu):
        status("Création des raccourcis…")
        installinfo.create_shortcuts(exe, desktop, start_menu)
    if register:
        installinfo.register(target_dir, APP_VERSION)
    status(f"Multi OBS {APP_VERSION} est installé.")
    return exe


def update(target_dir: Path, wait_pid: int | None, launch: bool = True) -> int:
    """Mise à jour lancée par l'appli : attend sa fermeture, remplace l'exe, relance."""
    if wait_pid:
        wait_for_pid(wait_pid)
    exe = put_exe(target_dir)
    installinfo.update_registered_version(APP_VERSION, exe)
    if launch:
        subprocess.Popen([str(exe), "--updated"], cwd=str(exe.parent), close_fds=True)
    return 0


class SetupWindow:
    def __init__(self, root: tk.Tk, online: bool):
        self.root = root
        self.online = online
        root.title("Installation de Multi OBS")
        root.configure(bg=BG)
        root.resizable(False, False)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=INPUT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", background=INPUT, foreground=TEXT, borderwidth=0, padding=(14, 6))
        style.map("TButton", background=[("active", "#464B59"), ("disabled", BASE)],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT)
        style.map("Accent.TButton", background=[("active", ACCENT_HI), ("disabled", BASE)])
        style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor=TEXT, borderwidth=0)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT, borderwidth=0)

        frm = ttk.Frame(root, padding=22)
        frm.grid(sticky="nsew")
        ttk.Label(frm, text=f"Multi OBS {APP_VERSION}", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text="Plusieurs OBS indépendants, pilotés depuis une seule fenêtre.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 16))
        ttk.Label(frm, text="Dossier d'installation").grid(row=2, column=0, columnspan=3, sticky="w")
        self.dir_var = tk.StringVar(value=str(installinfo.default_install_dir()))
        ttk.Entry(frm, textvariable=self.dir_var, width=52).grid(row=3, column=0, columnspan=2, sticky="we",
                                                                  pady=(4, 10), ipady=4)
        ttk.Button(frm, text="Changer…", command=self.browse).grid(row=3, column=2, padx=(8, 0), pady=(4, 10))
        self.start_menu = tk.BooleanVar(value=True)
        self.desktop = tk.BooleanVar(value=True)
        self.launch = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Ajouter au menu Démarrer", variable=self.start_menu).grid(
            row=4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Raccourci sur le bureau", variable=self.desktop).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Checkbutton(frm, text="Lancer Multi OBS à la fin", variable=self.launch).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 14))
        self.bar = ttk.Progressbar(frm, length=440, mode="determinate")
        self.bar.grid(row=7, column=0, columnspan=3, sticky="we")
        self.status = ttk.Label(frm, text="Prêt à installer.", style="Muted.TLabel")
        self.status.grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 14))
        self.cancel_btn = ttk.Button(frm, text="Fermer", command=root.destroy)
        self.cancel_btn.grid(row=9, column=1, sticky="e", padx=(0, 8))
        self.go_btn = ttk.Button(frm, text="Installer", style="Accent.TButton", command=self.start)
        self.go_btn.grid(row=9, column=2, sticky="e")
        frm.columnconfigure(0, weight=1)

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            p = Path(d)
            self.dir_var.set(str(p if p.name.lower() == "multi obs" else p / "Multi OBS"))

    def set_status(self, text):
        self.root.after(0, lambda: self.status.configure(text=text))

    def set_progress(self, done, total):
        def upd():
            if total:
                self.bar.configure(mode="determinate", maximum=total, value=done)
        self.root.after(0, upd)

    def start(self):
        self.go_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        target = Path(self.dir_var.get())
        desktop, launch, start_menu = self.desktop.get(), self.launch.get(), self.start_menu.get()

        def work():
            try:
                if self.online:
                    self.set_status("Recherche d'une version plus récente…")
                    rel = newer_release()
                    if rel:
                        self.set_status(f"Version {rel['tag_name'].lstrip('v')} disponible : téléchargement…")
                        args = ["--dir", str(target)] + ([] if desktop else ["--no-desktop"]) \
                            + ([] if start_menu else ["--no-start-menu"]) + ([] if launch else ["--no-launch"])
                        hand_over(rel, args, self.set_progress)
                        self.root.after(500, self.root.destroy)
                        return
                self.set_progress(1, 1)
                exe = install(target, desktop, True, True, self.set_status, start_menu)
            except Exception as e:  # message clair pour l'utilisateur
                self.set_status(f"Échec : {e}")
                self.root.after(0, lambda: (self.go_btn.configure(state="normal", text="Réessayer"),
                                            self.cancel_btn.configure(state="normal")))
                return
            if launch:
                subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
                self.root.after(1200, self.root.destroy)
            else:
                self.root.after(0, lambda: (self.cancel_btn.configure(state="normal", text="Terminer"),
                                            self.go_btn.grid_remove()))
        threading.Thread(target=work, daemon=True).start()


def _arg(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def main():
    args = sys.argv[1:]
    target = Path(_arg(args, "--dir") or installinfo.default_install_dir())
    if "--update" in args:
        pid = _arg(args, "--wait-pid")
        return update(target, int(pid) if pid else None, "--no-launch" not in args)
    online = "--no-online" not in args
    if "--silent" in args:
        try:
            if online:
                rel = newer_release()
                if rel:
                    hand_over(rel, [a for a in args if a != "--no-online"])
                    return 0
            exe = install(target, "--no-desktop" not in args, "--no-shortcuts" not in args,
                          "--no-register" not in args, print, "--no-start-menu" not in args)
        except Exception as e:
            print(f"ERREUR : {e}")
            return 1
        if "--no-launch" not in args:
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        return 0
    root = tk.Tk()
    try:
        root.iconbitmap(default=os.path.join(getattr(sys, "_MEIPASS", str(ROOT)), "multiobs.ico"))
    except tk.TclError:
        pass
    window = SetupWindow(root, online)
    if "--dir" in args:
        window.dir_var.set(str(target))
    if "--no-desktop" in args:
        window.desktop.set(False)
    if "--no-start-menu" in args:
        window.start_menu.set(False)
    if "--no-launch" in args:
        window.launch.set(False)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
