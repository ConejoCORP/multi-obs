"""Pilotage à distance (plugin Stream Deck) : petit serveur WebSocket local.

- Écoute seulement sur 127.0.0.1 (port 4460, ou un port libre s'il est pris).
- Fichier de découverte %LOCALAPPDATA%\\Multi OBS\\remote.json : port, jeton aléatoire renouvelé à
  chaque démarrage, chemin de « Multi OBS.exe » (le plugin peut rouvrir Multi OBS).
- Le client se présente avec le jeton. Une connexion venue d'un navigateur (en-tête Origin) est
  refusée : une page web ne peut pas piloter les OBS.
- Multi OBS pousse l'état des instances à chaque changement ; le client envoie des commandes.
  Rien ne pose de question : une commande impossible (éteindre pendant un rec…) est refusée avec
  une raison courte, affichée sur la touche.

Messages (JSON) :
  client → {"op": "hello", "token": "…", "client": "Stream Deck", "version": "1.0.8"}
  serveur → {"op": "welcome", "api": 1, "version": "1.0.8", "state": {…}}
  serveur → {"op": "state", "state": {…}}
  client → {"op": "cmd", "id": 7, "target": "inst1" | "all", "action": "record", "mode": "toggle" | "on" | "off",
            "params": {"scene": "…", "source": "…", "filter": "…", "input": "…"}}
  serveur → {"op": "result", "id": 7, "ok": true, "reason": ""}
  client → {"op": "cmd", "id": 9, "target": "app", "action": "quit"}   (fermer Multi OBS, depuis la 1.1.7)
  client → {"op": "list", "id": 8, "target": "inst1", "what": "scenes" | "items" | "sources" | "filters" | "audio",
            "params": {"scene": "…", "source": "…"}}
  serveur → {"op": "list", "id": 8, "ok": true, "items": ["…"], "reason": ""}
  client → {"op": "watch", "items": [{"target": "inst1", "kind": "filter" | "item" | "mute", "a": "…", "b": "…"}]}
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketProtocol, QWebSocketServer

from . import APP_NAME, APP_VERSION, applog, theme, winapi

API_VERSION = 1
DEFAULT_PORT = 4460            # juste sous les ports des instances (4461 et suivants)
DISCOVERY = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Multi OBS" / "remote.json"
AUTH_TIMEOUT_MS = 5000
PUSH_MS = 150                  # état regardé à ce rythme, envoyé seulement s'il a changé
RESYNC_S = 5.0                 # et renvoyé de temps en temps (timecode recalé, rien de perdu)
ACTIONS = ("power", "record", "pause", "vcam", "show")
OBS_ACTIONS = ("scene", "item", "filter", "mute", "stream", "screenshot")   # un seul OBS à la fois
WATCH_KINDS = ("filter", "item", "mute")
SEP = "\u001f"                 # sépare les morceaux des clés de suivi (jamais dans un nom OBS)


def _want(mode: str, current: bool) -> bool:
    return (not current) if mode == "toggle" else mode == "on"


def _obs_reason(status: dict) -> str:
    code = (status or {}).get("code")
    if code == 600:
        return "Introuvable"
    if code == 500:
        return "Déjà lancé"
    if code == 501:
        return "Pas lancé"
    return "Refusé par OBS"


class RemoteServer(QObject):
    clientsChanged = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.manager = window.manager
        self.token = secrets.token_urlsafe(24)
        self.server = QWebSocketServer(APP_NAME, QWebSocketServer.SslMode.NonSecureMode, self)
        self.server.newConnection.connect(self._on_new_connection)
        self.clients: dict = {}    # QWebSocket -> {"auth", "name", "version", "watch": {(cible, type, a, b)}}
        self._last_sig = ""
        self._last_sent = 0.0
        self._push = QTimer(self, interval=PUSH_MS, timeout=self._broadcast)

    # ------------------------------------------------------------------ #
    def start(self, port: int | None = None) -> bool:
        local = QHostAddress(QHostAddress.SpecialAddress.LocalHost)
        port = DEFAULT_PORT if port is None else port
        if not (self.server.listen(local, port) or self.server.listen(local, 0)):
            applog.log(f"Pilotage à distance : écoute impossible ({self.server.errorString()})")
            return False
        self._write_discovery(running=True)
        return True

    @property
    def port(self) -> int:
        return self.server.serverPort()

    def stop(self) -> None:
        self._push.stop()
        for sock in list(self.clients):
            self._forget(sock)
            sock.close()
        self._apply_watches()
        if self.server.isListening():
            self.server.close()
            try:
                mine = json.loads(DISCOVERY.read_text("utf-8")).get("pid") == os.getpid()
            except (OSError, ValueError):
                mine = False
            if mine:
                self._write_discovery(running=False)
        self.clientsChanged.emit()

    def connected_clients(self) -> list[dict]:
        return [c for c in self.clients.values() if c["auth"]]

    def _write_discovery(self, running: bool) -> None:
        data = {"app": APP_NAME, "version": APP_VERSION, "api": API_VERSION,
                "exe": sys.executable if getattr(sys, "frozen", False) else ""}
        if running:
            data.update(port=self.port, token=self.token, pid=os.getpid())
        try:
            DISCOVERY.parent.mkdir(parents=True, exist_ok=True)
            tmp = DISCOVERY.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), "utf-8")
            os.replace(tmp, DISCOVERY)
        except OSError as e:
            applog.log(f"Pilotage à distance : impossible d'écrire {DISCOVERY} ({e})")

    # ------------------------------------------------------------------ #
    # Connexions
    # ------------------------------------------------------------------ #
    def _on_new_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if sock.origin():   # page web : jamais
                sock.close(QWebSocketProtocol.CloseCode.CloseCodePolicyViolated, "origin")
                sock.deleteLater()
                continue
            self.clients[sock] = {"auth": False, "name": "", "version": "", "watch": set()}
            sock.textMessageReceived.connect(lambda text, s=sock: self._on_message(s, text))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            QTimer.singleShot(AUTH_TIMEOUT_MS, lambda s=sock: self._drop_if_anonymous(s))

    def _drop_if_anonymous(self, sock) -> None:
        if sock in self.clients and not self.clients[sock]["auth"]:
            sock.close(QWebSocketProtocol.CloseCode.CloseCodePolicyViolated, "hello")

    def _forget(self, sock) -> dict | None:
        """Retire une connexion. Ses signaux sont coupés : à la fermeture de Multi OBS, une connexion
        détruite avec le serveur ne doit plus rappeler ce code."""
        client = self.clients.pop(sock, None)
        if client is not None:
            for signal in (sock.textMessageReceived, sock.disconnected):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            sock.deleteLater()
        return client

    def _on_disconnected(self, sock) -> None:
        client = self._forget(sock)
        self._apply_watches()
        if client and client["auth"]:
            self.clientsChanged.emit()

    @staticmethod
    def _send(sock, msg: dict) -> None:
        sock.sendTextMessage(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    def _on_message(self, sock, text: str) -> None:
        client = self.clients.get(sock)
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if client is None or not isinstance(msg, dict):
            return
        op = msg.get("op")
        if not client["auth"]:
            if op == "hello" and secrets.compare_digest(str(msg.get("token", "")), self.token):
                client.update(auth=True, name=str(msg.get("client", ""))[:40],
                              version=str(msg.get("version", ""))[:20])
                self._send(sock, {"op": "welcome", "api": API_VERSION, "version": APP_VERSION,
                                  "state": self.state()})
                self._push.start()
                self.clientsChanged.emit()
            else:
                self._send(sock, {"op": "error", "reason": "Jeton refusé"})
                sock.close(QWebSocketProtocol.CloseCode.CloseCodePolicyViolated, "token")
            return
        if op == "cmd":
            self._on_command(sock, msg)
        elif op == "list":
            self._on_list(sock, msg)
        elif op == "watch":
            client["watch"] = {(str(i.get("target", "")), str(i.get("kind", "")), str(i.get("a", "")),
                                str(i.get("b", ""))) for i in msg.get("items") or []
                               if isinstance(i, dict) and i.get("kind") in WATCH_KINDS}
            self._apply_watches()

    def _on_command(self, sock, msg: dict) -> None:
        target, action = str(msg.get("target", "all")), str(msg.get("action", ""))
        mode = msg.get("mode") if msg.get("mode") in ("on", "off") else "toggle"
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

        def reply(reason: str) -> None:
            if sock in self.clients:
                self._send(sock, {"op": "result", "id": msg.get("id"), "ok": not reason, "reason": reason})
            QTimer.singleShot(0, self._broadcast)
        try:
            reason = self.execute(target, action, mode, params, reply)
        except Exception as e:  # noqa: BLE001 — une commande ratée ne doit pas casser la connexion
            applog.log(f"Pilotage à distance : {action} {target} → {e!r}")
            reason = "Erreur"
        if reason is not None:
            reply(reason)

    def _apply_watches(self) -> None:
        """Ce que les touches affichent (filtres, sources, sons) : suivi par les instances concernées."""
        wanted: dict[str, set] = {}
        for client in self.clients.values():
            for target, kind, a, b in client.get("watch", ()):
                wanted.setdefault(target, set()).add((kind, a, b))
        for inst in self.manager.instances:
            self.manager.controller(inst.id).set_watches(wanted.get(inst.id, set()))

    # ------------------------------------------------------------------ #
    # État
    # ------------------------------------------------------------------ #
    def state(self) -> dict:
        m = self.manager
        names = m.vcam_names()
        deck_alerts = bool(m.settings.get("alert_streamdeck", True))   # SafeGuard
        insts = []
        for inst in m.instances:
            c = m.controller(inst.id)
            remux = c.remux_progress
            warnings = c.warnings()
            insts.append({
                "id": inst.id, "name": inst.name, "color": inst.color,
                "colorName": theme.ACCENTS.get(inst.color, theme.ACCENTS["blue"])[0],
                "shades": theme.ACCENTS.get(inst.color, theme.ACCENTS["blue"])[1],
                "linked": inst.include_global, "minimized": inst.card_minimized,
                "status": "closing" if c.running and c.closing else c.status,
                "recording": c.recording, "paused": c.paused, "recMs": c.rec_elapsed_ms(),
                "streaming": c.streaming, "streamMs": c.stream_elapsed_ms(),
                "vcam": c.vcam_on, "vcamName": names.get(inst.vcam, ""),
                "pauseBlocked": c.pause_blocked,
                "remux": None if remux is None else int(remux * 100),
                "alert": warnings[0] if warnings else "",
                "critical": deck_alerts and bool(c.critical()),   # problème grave : alerte rouge sur les touches
                "scene": c.scene if c.connected else "",
                "mutes": dict(c.mutes),
                "watch": {SEP.join(k): v for k, v in c.watched.items()},
            })
        return {"version": APP_VERSION, "instances": insts, "all": self._global(insts),
                "onClose": m.settings.get("on_close", "ask")}

    def _global(self, insts: list[dict]) -> dict:
        """Ce que pilotent les touches « Tous » : comme les boutons du haut de Multi OBS
        (allumer / éteindre : toutes les instances ; rec et pause : les instances liées)."""
        shown = [i for i in insts if not i["minimized"]]
        running = [i for i in insts if i["status"] != "stopped"]
        targets = [i for i in insts if i["linked"] and i["status"] == "ready"]
        recs = [i for i in targets if i["recording"] and not (i["pauseBlocked"] and not i["paused"])]
        if not running:
            status = "stopped"
        elif any(i["status"] == "starting" for i in running):
            status = "starting"
        elif shown and all(i["status"] != "stopped" for i in shown):
            status = "ready"
        else:
            status = "partial"
        remuxing = [i["remux"] for i in insts if i["linked"] and i["remux"] is not None]
        alerts = [i["alert"] for i in targets if i["alert"]]
        return {
            "status": status, "running": len(running), "shown": len(shown), "total": len(insts),
            "shownRunning": sum(1 for i in shown if i["status"] != "stopped"),
            "targets": len(targets), "ids": [i["id"] for i in insts if i["linked"]],
            "recording": any(i["recording"] for i in targets),
            "paused": bool(recs) and all(i["paused"] for i in recs),
            "canPause": bool(recs),
            "recMs": max((i["recMs"] for i in targets if i["recording"]), default=0),
            "remux": min(remuxing) if remuxing else None,
            "alert": alerts[0] if alerts else "",
            "critical": any(i["critical"] for i in insts),   # rec bloqué, OBS fermé… sur n'importe quelle instance
        }

    @staticmethod
    def _signature(state: dict) -> str:
        """L'état sans ce qui bouge tout seul (durées, texte des alertes) : le client prolonge les
        durées lui-même ; on ne renvoie l'état que s'il a vraiment changé."""
        def strip(d: dict) -> dict:
            return {k: (bool(v) if k == "alert" else v) for k, v in d.items() if k not in ("recMs", "streamMs")}
        return json.dumps({"i": [strip(i) for i in state["instances"]], "a": strip(state["all"]),
                           "c": state.get("onClose")}, sort_keys=True)

    def _broadcast(self) -> None:
        socks = [s for s, c in self.clients.items() if c["auth"]]
        if not socks:
            self._push.stop()
            return
        self._apply_watches()   # instances recréées (préconfig…) : leur suivi repart
        state = self.state()
        sig = self._signature(state)
        now = time.monotonic()
        if sig == self._last_sig and now - self._last_sent < RESYNC_S:
            return
        self._last_sig, self._last_sent = sig, now
        text = json.dumps({"op": "state", "state": state}, ensure_ascii=False, separators=(",", ":"))
        for sock in socks:
            sock.sendTextMessage(text)

    # ------------------------------------------------------------------ #
    # Commandes
    # ------------------------------------------------------------------ #
    def execute(self, target: str, action: str, mode: str = "toggle", params: dict | None = None,
                reply=None) -> str | None:
        """Lance la commande. Renvoie "" si c'est parti, la raison d'un refus (courte), ou None si
        la réponse viendra plus tard par reply(raison) (actions OBS : scène, filtre…)."""
        if action == "quit":   # fermer Multi OBS (selon « À la fermeture de Multi OBS »)
            return self.window.remote_quit()
        if action not in ACTIONS + OBS_ACTIONS:
            return "Action inconnue"
        if target == "all":
            return self._execute_all(action, mode)
        if not any(i.id == target for i in self.manager.instances):
            return "Instance introuvable"
        c = self.manager.controller(target)
        w = self.window
        if action == "power":
            if mode == "on" or (mode == "toggle" and not c.running):
                if not c.running:
                    w.launch([target], interactive=False)
                return ""
            if not c.running:
                return ""
            if c.recording:
                return "REC en cours"
            if c.streaming:
                return "Live en cours"
            if c.remux is not None:   # OBS convertit encore le rec en MP4 : extinction à la fin
                c.close_after_conversion()
                return "Arrêt après la conversion"
            if not c.shutdown_gracefully():
                c.kill()
            return ""
        if action == "show":
            if c.running:
                c.show_window()
            else:
                w.launch([target], interactive=False)
            return ""
        if not c.running:
            return "Éteint"
        if not c.connected:
            return "Démarrage"
        if action in OBS_ACTIONS:
            self._obs_action(c, action, mode, params or {}, reply or (lambda _r: None))
            return None
        if action == "record":
            if mode == "on" or (mode == "toggle" and not c.recording):
                if not w.ask_video_name_then(c.start_record):   # option : nom de la vidéo demandé d'abord
                    return "Nom de la vidéo ?"
            else:
                c.stop_record()
            return ""
        if action == "pause":
            if not c.recording:
                return "Pas de rec"
            if mode == "on" or (mode == "toggle" and not c.paused):
                if c.pause_blocked:
                    return "Pause bloquée"
                c.pause_record()
            else:
                c.resume_record()
            return ""
        # Caméra virtuelle
        if _want(mode, c.vcam_on) != c.vcam_on:
            c.toggle_vcam()
        return ""

    def _obs_action(self, c, action: str, mode: str, p: dict, reply) -> None:
        """Scène, source, filtre, son, live, capture : demandés à OBS, réponse quand il a fini."""
        ws = c.ws

        def done(ok, _d, status):
            reply("" if ok else _obs_reason(status))
        if action == "scene":
            ws.call("SetCurrentProgramScene", {"sceneName": p.get("scene", "")}, done)
        elif action == "item":
            scene, source = p.get("scene", ""), p.get("source", "")

            def with_id(ok, d, status):
                if not ok:
                    reply(_obs_reason(status))
                    return
                item_id = d.get("sceneItemId")

                def with_state(ok2, d2, status2):
                    if not ok2:
                        reply(_obs_reason(status2))
                        return
                    ws.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id,
                                                    "sceneItemEnabled": _want(mode, d2.get("sceneItemEnabled", False))},
                            done)
                ws.call("GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item_id}, with_state)
            ws.call("GetSceneItemId", {"sceneName": scene, "sourceName": source}, with_id)
        elif action == "filter":
            source, name = p.get("source", ""), p.get("filter", "")

            def with_filter(ok, d, status):
                if not ok:
                    reply(_obs_reason(status))
                    return
                ws.call("SetSourceFilterEnabled", {"sourceName": source, "filterName": name,
                                                   "filterEnabled": _want(mode, d.get("filterEnabled", False))}, done)
            ws.call("GetSourceFilter", {"sourceName": source, "filterName": name}, with_filter)
        elif action == "mute":
            name = p.get("input", "")
            if mode == "toggle":
                ws.call("ToggleInputMute", {"inputName": name}, done)
            else:
                ws.call("SetInputMute", {"inputName": name, "inputMuted": mode == "on"}, done)
        elif action == "stream":
            want = _want(mode, c.streaming)
            if want == c.streaming:
                reply("")
            else:
                ws.call("StartStream" if want else "StopStream", None, done)
        else:   # capture d'écran de la scène en cours, rangée avec les enregistrements
            folder = Path(c.record_dir) if c.record_dir else Path.home() / "Videos"
            path = folder / time.strftime("Capture %Y-%m-%d %H-%M-%S.png")
            ws.call("SaveSourceScreenshot", {"sourceName": c.scene, "imageFormat": "png",
                                             "imageFilePath": str(path)}, done)

    def _execute_all(self, action: str, mode: str) -> str:
        w, m = self.window, self.manager
        if action == "record":
            return w.record_all(mode)
        if action == "pause":
            return w.pause_all(mode)
        if action == "show":
            if w.isMinimized():
                w.showNormal()
            w.show()
            w.raise_()
            winapi.bring_to_front(int(w.winId()))
            return ""
        if action != "power":
            return "Choisis un OBS"
        shown = [i for i in m.instances if not i.card_minimized]
        if mode == "on" or (mode == "toggle" and any(not m.controller(i.id).running for i in shown)):
            if not shown:
                return "Aucune instance"
            w.launch_all(interactive=False)
            return ""
        busy = [c for c in m.controllers.values() if c.recording or c.streaming]
        if busy:
            return "REC en cours" if any(c.recording for c in busy) else "Live en cours"
        converting = False
        for c in m.controllers.values():
            if not c.running:
                continue
            if c.remux is not None:   # OBS convertit encore le rec en MP4 : extinction à la fin
                c.close_after_conversion()
                converting = True
            elif not c.shutdown_gracefully():
                c.kill()
        return "Arrêt après la conversion" if converting else ""

    # ------------------------------------------------------------------ #
    # Listes pour la fenêtre de réglages du Stream Deck
    # ------------------------------------------------------------------ #
    def _on_list(self, sock, msg: dict) -> None:
        target, what = str(msg.get("target", "")), msg.get("what")
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}

        def reply(items=None, reason="") -> None:
            if sock in self.clients:
                self._send(sock, {"op": "list", "id": msg.get("id"), "ok": not reason, "items": items or [],
                                  "reason": reason})

        def names(key: str, field: str, index: str = ""):
            # Dans l'ordre d'OBS : la première scène / source de la liste d'OBS en premier.
            def cb(ok, d, status):
                if not ok:
                    reply(reason=_obs_reason(status))
                    return
                rows = d.get(key) or []
                if index:
                    rows = sorted(rows, key=lambda r: -(r.get(index) or 0))
                reply([r.get(field, "") for r in rows])
            return cb
        if not any(i.id == target for i in self.manager.instances):
            reply(reason="Instance introuvable")
            return
        c = self.manager.controller(target)
        if not c.connected:
            reply(reason="Éteint" if not c.running else "Démarrage")
            return
        ws = c.ws
        if what == "scenes":
            ws.call("GetSceneList", None, names("scenes", "sceneName", "sceneIndex"))
        elif what == "items":
            ws.call("GetSceneItemList", {"sceneName": p.get("scene", "")}, names("sceneItems", "sourceName", "sceneItemIndex"))
        elif what == "filters":
            ws.call("GetSourceFilterList", {"sourceName": p.get("source", "")}, names("filters", "filterName"))
        elif what == "sources":   # tout ce qui peut avoir des filtres : sources, puis scènes
            def with_inputs(ok, d, status):
                if not ok:
                    reply(reason=_obs_reason(status))
                    return
                inputs = [r.get("inputName", "") for r in d.get("inputs") or []]

                def with_scenes(ok2, d2, _s2):
                    scenes = [r.get("sceneName", "") for r in sorted(d2.get("scenes") or [],
                                                                    key=lambda r: -(r.get("sceneIndex") or 0))] if ok2 else []
                    reply(inputs + [s for s in scenes if s not in inputs])
                ws.call("GetSceneList", None, with_scenes)
            ws.call("GetInputList", None, with_inputs)
        elif what == "audio":   # les sources audio du petit mélangeur de la carte
            audio = list(dict.fromkeys(c.meter_order + sorted(c.mutes)))
            if audio:
                reply(audio)
            else:
                ws.call("GetInputList", None, names("inputs", "inputName"))
        else:
            reply(reason="Liste inconnue")
