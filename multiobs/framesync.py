"""Départ, pause, reprise et arrêt « à l'image près » de plusieurs instances OBS.

OBS coupe une pause sur une image de sa propre horloge vidéo, deux images après l'appel. Chaque
instance a sa propre horloge : mises en pause au même instant, deux instances peuvent enlever une
image de plus ou de moins l'une que l'autre, et au montage les pistes se décalent d'une image à
chaque pause. Ici toutes les instances enlèvent exactement la même durée :

- pause : lot obs-websocket exécuté dans le thread graphique d'OBS (« SerialFrame ») :
  [GetStats, GetVideoSettings, PauseRecord] → numéro exact de l'image où la pause est demandée ;
- reprise : sonde [GetStats] (image en cours), puis [GetStats, Sleep n images, GetStats,
  ResumeRecord] envoyé aussitôt, pour que chaque instance reprenne exactement N images après
  sa propre pause (même N partout, ou même durée si les cadences diffèrent).

Au départ, les recs commencent à des images différentes (0 à 7 images d'écart) : OBS branche
l'encodeur depuis son interface, à un moment qui dépend aussi de son fil audio. Rien dans
obs-websocket ne dit à quelle image un fichier commence ; le module OBS livré avec Multi OBS
(obsplugin/multiobs-sync.c) le dit : pour chaque fichier, l'heure de rendu de sa première image.
- départ : les instances parties en avance font une pause d'exactement n images (lot
  [PauseRecord, Sleep n, ResumeRecord]), vérifiée ensuite par le module, et refaite si besoin ;
- arrêt : chaque instance s'arrête sur son image la plus proche d'une même heure.

Tout se passe dans un thread à part, avec ses propres connexions : l'interface ne peut pas
retarder un envoi.
"""
import base64
import hashlib
import itertools
import json
import math
import os
import queue
import select
import socket
import struct
import sys
import threading
import time
from fractions import Fraction

from PySide6.QtCore import QObject, Signal

EV_OUTPUTS = 1 << 6
SERIAL_FRAME = 1        # lot exécuté dans le thread graphique, image par image
LEAD_FRAMES = 3         # la reprise est programmée au moins 3 images après la décision
MAX_LCM_SECONDS = 1     # cadences différentes : durée commune au plus 1 s, sinon « au mieux »
PAUSE_CONFIRM_S = 0.5   # sans événement PAUSED après ce délai, on regarde si OBS a ignoré la pause
PAUSE_TRIES = 4
VENDOR = "multiobs-sync"   # module OBS de Multi OBS (requête « status »)
VENDOR_STATUS = {"requestType": "CallVendorRequest", "requestData": {"vendorName": VENDOR, "requestType": "status"}}
# Versions d'OBS avec lesquelles le module a été vérifié (il lit des structures d'OBS : une autre version
# majeure pourrait les changer, ses mesures ne seraient alors plus sûres).
SYNC_OBS_VERSIONS = ("31.", "32.", "33.")


def sync_usable(status: dict | None) -> bool:
    """Le module répond, peut suivre l'enregistrement, et tourne dans un OBS vérifié."""
    return bool(status and status.get("supported") and str(status.get("obs", "")).startswith(SYNC_OBS_VERSIONS))
ALIGN_WAIT_S = 8.0      # 1re image écrite 0,2 à 1 s après le départ (encodeur, tampon audio d'OBS)
ALIGN_MAX_S = 2.0       # une instance partie bien plus tard : on n'enlève pas autant aux autres
ALIGN_ROUNDS = 4
ALIGN_GUARD_S = 0.35    # OBS ignore une pause demandée juste après une reprise
ALIGN_MIN_PAUSE_S = 0.1
RESYNC_MAX_S = 0.25     # à la reprise : on rattrape un écart de quelques images, pas des recs lancés à part
STOP_LEAD_FRAMES = 4    # l'arrêt est programmé 4 images plus loin


class WsError(Exception):
    pass


class Client:
    """Client obs-websocket v5 bloquant (JSON), réduit aux lots de requêtes."""

    def __init__(self, port: int, password: str):
        self.port, self.password = port, password
        self.sock: socket.socket | None = None
        self.identified = False
        self.buf = b""
        self.frag = b""
        self.events: list[tuple[float, str, dict]] = []
        self.responses: dict[str, tuple[float, list]] = {}
        self._next = 0

    # Connexion ------------------------------------------------------------ #
    def connect(self, timeout: float = 3.0) -> None:
        self.close()
        try:
            self._handshake(timeout)
        except (OSError, WsError):
            self.close()   # jamais de connexion à moitié ouverte (requêtes ignorées par OBS)
            raise

    def _handshake(self, timeout: float) -> None:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        self.sock = s
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        head = b""
        deadline = time.perf_counter() + timeout
        while b"\r\n\r\n" not in head:
            if time.perf_counter() > deadline:
                raise WsError("pas de réponse d'OBS")
            chunk = s.recv(4096)
            if not chunk:
                raise WsError("connexion refusée")
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        if b" 101" not in head.split(b"\r\n", 1)[0]:
            raise WsError("connexion websocket refusée")
        s.setblocking(False)
        self.buf, self.frag, self.identified = rest, b"", False
        self._process(time.perf_counter())   # le « Hello » d'OBS arrive souvent collé à la réponse HTTP
        hello = self._wait_op(0, deadline)
        ident = {"rpcVersion": 1, "eventSubscriptions": EV_OUTPUTS}
        auth = hello.get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((self.password + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self._send(1, ident)
        self._wait_op(2, deadline)
        self.identified = True

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.identified = False

    @property
    def alive(self) -> bool:
        return self.sock is not None and self.identified

    # Envoi ---------------------------------------------------------------- #
    def batch(self, requests: list[dict]) -> str:
        """Envoie un lot exécuté dans le thread graphique ; renvoie son identifiant."""
        self._next += 1
        rid = f"fs{self._next}"
        self._send(8, {"requestId": rid, "haltOnFailure": False, "executionType": SERIAL_FRAME,
                       "requests": requests})
        return rid

    def _send(self, op: int, d: dict) -> None:
        payload = json.dumps({"op": op, "d": d}).encode()
        n = len(payload)
        header = bytearray([0x81])   # FIN + texte
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        keystream = (mask * (n // 4 + 1))[:n]
        masked = (int.from_bytes(payload, "big") ^ int.from_bytes(keystream, "big")).to_bytes(n, "big")
        self.sock.setblocking(True)
        try:
            self.sock.sendall(bytes(header) + mask + masked)
        finally:
            self.sock.setblocking(False)

    # Réception ------------------------------------------------------------ #
    def feed(self) -> bool:
        """Lit ce qui est disponible (socket prête). False si la connexion est fermée."""
        try:
            chunk = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            self.close()
            return False
        if not chunk:
            self.close()
            return False
        self.buf += chunk
        self._process(time.perf_counter())
        return True

    def _process(self, now: float) -> None:
        for msg in self._frames():
            self._dispatch(msg, now)

    def _frames(self):
        while True:
            b = self.buf
            if len(b) < 2:
                return
            fin, opcode, n, i = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x7F, 2
            if n == 126:
                if len(b) < 4:
                    return
                n, i = struct.unpack(">H", b[2:4])[0], 4
            elif n == 127:
                if len(b) < 10:
                    return
                n, i = struct.unpack(">Q", b[2:10])[0], 10
            if len(b) < i + n:
                return
            payload, self.buf = b[i:i + n], b[i + n:]
            if opcode == 0x9:                       # ping → pong
                self._pong(payload)
            elif opcode == 0x8:                     # fermeture
                self.close()
                return
            elif opcode in (0x1, 0x2, 0x0):
                self.frag += payload
                if fin:
                    data, self.frag = self.frag, b""
                    try:
                        yield json.loads(data)
                    except ValueError:
                        pass

    def _pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        masked = bytes(c ^ mask[k % 4] for k, c in enumerate(payload))
        try:
            self.sock.setblocking(True)
            self.sock.sendall(bytes([0x8A, 0x80 | n]) + mask + masked)
        except OSError:
            self.close()
        finally:
            if self.sock:
                self.sock.setblocking(False)

    def _dispatch(self, msg: dict, now: float) -> None:
        op, d = msg.get("op"), msg.get("d") or {}
        if op == 5:
            self.events.append((now, d.get("eventType", ""), d.get("eventData") or {}))
        elif op == 9:
            self.responses[d.get("requestId", "")] = (now, d.get("results") or [])
        else:
            self.responses[f"op{op}"] = (now, [d])

    def _wait_op(self, op: int, deadline: float) -> dict:
        while f"op{op}" not in self.responses:
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or not self.sock:
                raise WsError("OBS ne répond pas")
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if ready and not self.feed():
                raise WsError("connexion fermée")
        return self.responses.pop(f"op{op}")[1][0]


def pump(clients: list[Client], timeout: float) -> None:
    """Attend (au plus timeout s) et lit ce qui arrive sur toutes les connexions."""
    socks = {c.sock: c for c in clients if c.sock}
    if not socks:
        time.sleep(max(0.0, timeout))
        return
    ready, _, _ = select.select(list(socks), [], [], max(0.0, timeout))
    for s in ready:
        socks[s].feed()


def _result(results: list, k: int) -> dict:
    return (results[k].get("responseData") or {}) if k < len(results) else {}


def _ok(results: list, k: int) -> bool:
    return k < len(results) and bool((results[k].get("requestStatus") or {}).get("result"))


def frames_late(delay: float, interval: float, margin: float = 0.003) -> tuple[int, bool]:
    """Images de retard d'une pause / reprise appliquée par OBS `delay` s après l'image du lot.

    OBS coupe deux images après l'appel réel : si son interface met plus d'une image à traiter
    l'ordre, la coupe glisse d'autant. Renvoie (images de retard, certitude) : près d'une limite
    d'image (à `margin` près), on ne peut pas savoir de quel côté on est.
    """
    if delay < 0:
        return 0, True
    k = int(delay // interval)
    frac = delay - k * interval
    sure = not ((k >= 1 and frac < margin) or interval - frac < margin)
    return k, sure


def best_shifts(origins: dict, intervals: dict) -> tuple[dict, float]:
    """Images à enlever à chaque instance pour que les fichiers commencent le plus ensemble possible.

    origins : heure (ns) qui correspond au début de chaque fichier ; intervals : durée d'une image (ns).
    On ne peut qu'enlever des images (pause) : une origine ne peut que reculer d'un nombre entier
    d'images. Les horloges d'image des instances n'étant pas en phase, une fraction d'image d'écart
    reste ; on choisit les décalages qui la rendent la plus petite (à égalité : le moins d'images
    enlevées, donc rien si c'est déjà le mieux possible). Renvoie ({instance: images}, écart restant
    en images).
    """
    ref = max(origins, key=origins.get)
    others = [i for i in origins if i != ref]
    best = None
    for choice in itertools.product((0, 1), repeat=len(others)):
        shifts, final = {ref: 0}, {ref: origins[ref]}
        for inst_id, up in zip(others, choice):
            lag = (origins[ref] - origins[inst_id]) / intervals[inst_id]   # images de retard (≥ 0)
            shifts[inst_id] = math.floor(lag + 1e-6) + up
            final[inst_id] = origins[inst_id] + shifts[inst_id] * intervals[inst_id]
        spread = (max(final.values()) - min(final.values())) / intervals[ref]
        key = (round(spread, 2), sum(shifts.values()))
        if best is None or key < best[0]:
            best = (key, shifts, spread)
    return best[1], best[2]


def _common_period(intervals: list[Fraction]) -> Fraction | None:
    """Plus petite durée multiple de toutes les durées d'image (None si plus d'une seconde)."""
    period = intervals[0]
    for f in intervals[1:]:
        period = Fraction(math.lcm(period.numerator, f.numerator), math.gcd(period.denominator, f.denominator))
        if period > MAX_LCM_SECONDS:
            return None
    return period


OP_NAMES = {"pause": "de la pause", "resume": "de la reprise", "align": "du départ", "stop": "de l'arrêt"}


class FrameSync(QObject):
    """Départ, pause, reprise et arrêt synchronisés à l'image près, dans un thread dédié."""
    finished = Signal(str, list, list)   # opération, instances traitées, avertissements

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: queue.Queue = queue.Queue()
        self._clients: dict[str, Client] = {}
        self._paused: dict[str, dict] = {}   # instance → {"frame", "t", "interval", "late"}
        self._debt: dict[str, int] = {}      # images enlevées en trop (reprise tardive) à rendre
        self.last_report: list[dict] = []    # détails de la dernière opération (tests, journal)
        self._thread = threading.Thread(target=self._run, name="Multi OBS FrameSync", daemon=True)
        self._thread.start()

    # API (thread de l'interface) -------------------------------------------- #
    def pause(self, targets: list[tuple[str, int, str, str]]) -> None:
        """targets : (id, port, mot de passe, nom) des instances qui enregistrent."""
        self._jobs.put(("pause", targets))

    def resume(self, targets: list[tuple[str, int, str, str]], resync: bool = True) -> None:
        """resync : recaler aussi les fichiers d'après le module (écart de quelques images rattrapé)."""
        self._jobs.put(("resume", targets, {"resync": resync}))

    def align(self, targets: list[tuple[str, int, str, str]]) -> None:
        """Juste après « Tout enregistrer » : aligne le début des fichiers."""
        self._jobs.put(("align", targets))

    def stop(self, targets: list[tuple[str, int, str, str]]) -> None:
        self._jobs.put(("stop", targets))

    def shutdown(self) -> None:
        self._jobs.put(None)

    # Thread -------------------------------------------------------------- #
    def _run(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=0.1)
            except queue.Empty:
                self._idle()
                continue
            if job is None:
                break
            op, targets, *extra = job
            options = extra[0] if extra else {}
            old_switch = sys.getswitchinterval()
            sys.setswitchinterval(0.0005)   # le thread reprend la main vite pendant l'opération
            run = {"pause": self._do_pause, "resume": self._do_resume, "align": self._do_align,
                   "stop": self._do_stop}[op]
            try:
                ids, warnings = run(targets, **options)
            except Exception as e:  # noqa: BLE001 — remonté à l'interface
                ids, warnings = [], [f"synchro {OP_NAMES[op]} impossible ({e})"]
            finally:
                sys.setswitchinterval(old_switch)
            self.finished.emit(op, ids, warnings)
        for c in self._clients.values():
            c.close()

    def _idle(self) -> None:
        """Entre deux opérations : on lit nos connexions. Sinon, à la fermeture d'une instance, OBS
        attendrait notre réponse (5 s) et pouvait rester bloqué en quittant."""
        alive = [c for c in self._clients.values() if c.sock]
        if not alive:
            return
        pump(alive, 0)
        for inst_id, c in self._clients.items():
            self._absorb_events(inst_id, c)

    def _absorb_events(self, inst_id: str, c: Client) -> None:
        """Pause reprise / arrêtée ailleurs (bouton de la carte, fenêtre OBS…) : repère oublié."""
        if inst_id in self._paused:
            for _t, etype, d in c.events:
                if etype == "RecordStateChanged" and not d.get("outputState", "").endswith("PAUSED"):
                    self._paused.pop(inst_id, None)
                    self._debt.pop(inst_id, None)
                    break
        c.events.clear()

    def _client(self, inst_id: str, port: int, password: str) -> Client:
        c = self._clients.get(inst_id)
        if c is None or c.port != port or c.password != password:
            if c:
                c.close()
            c = self._clients[inst_id] = Client(port, password)
        if not c.alive:
            c.connect()
        else:
            pump([c], 0)   # vide ce qui est arrivé (événements) ; détecte une connexion fermée
            if not c.alive:
                c.connect()
        return c

    def _connect(self, targets) -> tuple[dict, list]:
        clients, warnings = {}, []
        for inst_id, port, password, name in targets:
            try:
                clients[inst_id] = self._client(inst_id, port, password)
            except (OSError, WsError) as e:
                warnings.append(f"{name} : connexion pour la synchro impossible ({e})")
        return clients, warnings

    def _wait(self, clients: dict, rids: dict, timeout: float) -> dict:
        """Attend les réponses des lots ; renvoie {id: (horodatage, résultats)}."""
        got, deadline = {}, time.perf_counter() + timeout
        while len(got) < len(rids) and time.perf_counter() < deadline:
            pump(list(clients.values()), min(0.05, deadline - time.perf_counter()))
            for inst_id, rid in rids.items():
                c = clients[inst_id]
                if inst_id not in got and rid in c.responses:
                    got[inst_id] = c.responses.pop(rid)
        return got

    def _event_delay(self, clients: dict, sent: dict, state: str, timeout: float = 0.5) -> dict:
        """Délai entre l'image du lot et l'événement OBS (pause / reprise réellement appliquée)."""
        delays, deadline = {}, time.perf_counter() + timeout
        while len(delays) < len(sent) and time.perf_counter() < deadline:
            pump(list(clients.values()), 0.02)
            for inst_id, t_batch in sent.items():
                if inst_id in delays:
                    continue
                for t_ev, etype, d in clients[inst_id].events:
                    # L'événement peut arriver juste avant la réponse du lot (threads différents dans OBS).
                    if etype == "RecordStateChanged" and d.get("outputState", "").endswith(state) \
                            and t_ev >= t_batch - 0.05:
                        delays[inst_id] = t_ev - t_batch
                        break
        for c in clients.values():
            c.events.clear()
        return delays

    def _pause_events(self, clients: dict, asks: dict) -> dict:
        """Attend les événements PAUSED ; chacun est rattaché à la dernière demande qui le précède.
        Renvoie {instance: (délai de l'événement, (horodatage du lot, image))}."""
        found, deadline = {}, time.perf_counter() + PAUSE_CONFIRM_S
        while len(found) < len(asks) and time.perf_counter() < deadline:
            pump(list(clients.values()), 0.02)
            for inst_id, reqs in asks.items():
                if inst_id in found:
                    continue
                for t_ev, etype, d in clients[inst_id].events:
                    if etype != "RecordStateChanged" or not d.get("outputState", "").endswith("PAUSED"):
                        continue
                    # L'événement peut arriver juste avant la réponse du lot (threads différents dans OBS).
                    before = [r for r in reqs if r[0] <= t_ev + 0.05]
                    if before:
                        found[inst_id] = (t_ev - before[-1][0], before[-1])
                        break
        return found

    def _record_status(self, clients: dict, ids: list) -> dict:
        got = self._wait(clients, {i: clients[i].batch([{"requestType": "GetRecordStatus"}]) for i in ids}, 1.0)
        return {i: _result(results, 0) for i, (_t, results) in got.items()}

    def _do_pause(self, targets) -> tuple[list, list]:
        clients, warnings = self._connect(targets)
        names = {t[0]: t[3] for t in targets}
        rids = {inst_id: c.batch([{"requestType": "GetStats"}, {"requestType": "GetVideoSettings"},
                                  {"requestType": "PauseRecord"}])
                for inst_id, c in clients.items()}
        got = self._wait(clients, rids, 2.0)
        asks: dict[str, list] = {}    # demandes de pause acceptées : [(horodatage du lot, image)]
        report = {}
        for inst_id, (t, results) in got.items():
            vs = _result(results, 1)
            num, den = vs.get("fpsNumerator") or 0, vs.get("fpsDenominator") or 0
            if not _ok(results, 2) or not num or not den:
                warnings.append(f"{names[inst_id]} : pause refusée par OBS")
                continue
            asks[inst_id] = [(t, _result(results, 0).get("renderTotalFrames"))]
            self._paused[inst_id] = {"interval": Fraction(den, num)}
            report[inst_id] = {"id": inst_id, "op": "pause", "fps": f"{num}/{den}"}
        for inst_id in rids:
            if inst_id not in got:
                warnings.append(f"{names[inst_id]} : pas de réponse d'OBS pour la pause")

        # OBS ignore une pause demandée juste après une reprise (son encodeur n'a pas encore passé
        # l'instant de la reprise), tout en répondant « OK ». Sans événement PAUSED, on vérifie et on
        # redemande : chaque instance reprendra N images après SA vraie pause, l'égalité tient.
        applied = {}
        for attempt in range(PAUSE_TRIES):
            applied.update(self._pause_events(clients, {i: r for i, r in asks.items() if i not in applied}))
            missing = [i for i in asks if i not in applied]
            if not missing or attempt == PAUSE_TRIES - 1:
                break
            status = self._record_status(clients, missing)
            retry = [i for i in missing if status.get(i, {}).get("outputActive") and not status[i].get("outputPaused")]
            if not retry:
                break
            again = self._wait(clients, {i: clients[i].batch([{"requestType": "GetStats"}, {"requestType": "PauseRecord"}])
                                         for i in retry}, 2.0)
            for inst_id, (t, results) in again.items():
                if _ok(results, 1):
                    asks[inst_id].append((t, _result(results, 0).get("renderTotalFrames")))

        missing = [i for i in asks if i not in applied]
        status = self._record_status(clients, missing) if missing else {}
        done = []
        for inst_id, reqs in asks.items():
            p, r = self._paused[inst_id], report[inst_id]
            r["tries"] = len(reqs)
            if inst_id in applied:
                delay, (t, frame) = applied[inst_id]
                # Pause appliquée plus d'une image après le lot : elle a glissé ; la reprise suivra.
                late, sure = frames_late(delay, float(p["interval"]))
                p.update(frame=frame, t=t, late=late)
                r.update(frame=frame, delay_ms=round(delay * 1000, 2), late=late)
                if not sure:
                    warnings.append(f"{names[inst_id]} : OBS a appliqué la pause très tard, possible décalage d'une image")
            elif status.get(inst_id, {}).get("outputPaused"):
                t, frame = reqs[-1]
                p.update(frame=frame, t=t, late=0)
                r.update(frame=frame, delay_ms=-1)
                warnings.append(f"{names[inst_id]} : pause non confirmée par OBS, possible décalage d'une image")
            else:
                self._paused.pop(inst_id, None)
                r.update(frame=None, delay_ms=-1)
                warnings.append(f"{names[inst_id]} : OBS n'a pas mis l'enregistrement en pause")
                continue
            done.append(inst_id)
        for c in clients.values():
            c.events.clear()
        self.last_report = list(report.values())
        return done, warnings

    def _do_resume(self, targets, resync: bool = True) -> tuple[list, list]:
        clients, warnings = self._connect(targets)
        names = {t[0]: t[3] for t in targets}
        for inst_id, c in clients.items():   # derniers événements reçus depuis la pause
            self._absorb_events(inst_id, c)
        synced = {i: self._paused[i] for i in clients if i in self._paused}
        plain = [i for i in clients if i not in synced]
        report, done = [], []

        # Durée commune enlevée par la pause (en secondes, multiple de toutes les durées d'image).
        if synced:
            now = time.perf_counter()
            intervals = [p["interval"] for p in synced.values()]
            period = _common_period(intervals)
            if period is None:
                warnings.append("cadences d'image différentes entre les instances : reprise synchronisée au mieux")
                period = max(intervals)
            longest = max(Fraction(now - p["t"]).limit_denominator(10**6) + (LEAD_FRAMES + 1) * p["interval"]
                          for p in synced.values())
            removed = math.ceil(longest / period) * period
            frames = {i: round(removed / p["interval"]) for i, p in synced.items()}
            # Avec le module : chaque pause resynchronise. Les origines (avant la pause) disent exactement
            # de combien les fichiers se sont écartés (images sautées, reprise précédente tardive…) : on
            # ajoute ces images à la pause des instances en avance, au lieu de compter sur la « dette ».
            # Seulement de petits écarts : des recs lancés à des moments différents (une instance relancée
            # seule…) ne sont pas « décalés », on n'enlève pas des secondes aux autres.
            st = self._vendor(clients, list(synced)) if resync else {}   # désactivable (paramètres)
            if st and all(st.get(i, {}).get("frames") for i in synced):
                shifts, _spread = best_shifts({i: st[i]["origin"] for i in synced},
                                              {i: float(p["interval"]) * 1e9 for i, p in synced.items()})
                if all(n * float(synced[i]["interval"]) <= RESYNC_MAX_S for i, n in shifts.items()):
                    for inst_id in synced:
                        frames[inst_id] += shifts[inst_id]
                        self._debt.pop(inst_id, None)
                    if any(shifts.values()):
                        warnings.append("décalage rattrapé à la reprise : " + ", ".join(
                            f"{names[i]} {n} image(s)" for i, n in shifts.items() if n))
            done, report = self._resume_synced(clients, names, synced, frames, warnings)

        # Instances sans repère de pause commun : reprise simple.
        if plain:
            rids = {i: clients[i].batch([{"requestType": "ResumeRecord"}]) for i in plain}
            for inst_id, (_t, results) in self._wait(clients, rids, 2.0).items():
                if _ok(results, 0):
                    done.append(inst_id)
                    self._paused.pop(inst_id, None)
                report.append({"id": inst_id, "op": "resume-plain", "ok": _ok(results, 0)})
        self.last_report = report
        return done, warnings

    def _resume_synced(self, clients: dict, names: dict, synced: dict, frames: dict, warnings: list) -> tuple[list, list]:
        """Reprend chaque instance exactement frames[i] images après sa pause : sonde de l'image en
        cours, puis reprise programmée dès la réponse (même image + 1)."""
        report, done = [], []
        probes = {i: clients[i].batch([{"requestType": "GetStats"}]) for i in synced}
        pending = dict(probes)
        resumes, deadline = {}, time.perf_counter() + 2.0
        while pending and time.perf_counter() < deadline:
            pump([clients[i] for i in pending], 0.05)
            for inst_id in list(pending):
                c = clients[inst_id]
                if pending[inst_id] not in c.responses:
                    continue
                t_probe, results = c.responses.pop(pending.pop(inst_id))
                now_frame = _result(results, 0).get("renderTotalFrames")
                p = synced[inst_id]
                # Pause qui a glissé (OBS lent) : reprise d'autant plus tard ; images enlevées
                # en trop à la reprise précédente : rendues maintenant.
                target = p["frame"] + frames[inst_id] + p.get("late", 0) + self._debt.get(inst_id, 0)
                wait = target - (now_frame + 1)
                reqs = [{"requestType": "GetStats"}]
                if wait > 0:
                    reqs.append({"requestType": "Sleep", "requestData": {"sleepFrames": wait}})
                reqs += [{"requestType": "GetStats"}, {"requestType": "ResumeRecord"}]
                resumes[inst_id] = (c.batch(reqs), time.perf_counter() - t_probe, now_frame, target, max(0, wait))
        for inst_id in pending:
            warnings.append(f"{names[inst_id]} : pas de réponse d'OBS pour la reprise")
        got = self._wait(clients, {i: r[0] for i, r in resumes.items()}, 5.0)
        sent = {}
        for inst_id, (t, results) in got.items():
            _rid, send_after, now_frame, target, wait = resumes[inst_id]
            first = _result(results, 0).get("renderTotalFrames")
            at = _result(results, 2 if wait > 0 else 1).get("renderTotalFrames")
            ok = _ok(results, len(results) - 1)
            p = synced[inst_id]
            entry = {"id": inst_id, "op": "resume", "pause_frame": p["frame"], "removed_frames": frames[inst_id],
                     "pause_late": p.get("late", 0), "debt": self._debt.get(inst_id, 0),
                     "target": target, "first": first, "expected_first": now_frame + 1, "resume_frame": at,
                     "send_ms": round(send_after * 1000, 2), "ok": ok}
            report.append(entry)
            if not ok:
                warnings.append(f"{names[inst_id]} : reprise refusée par OBS")
                continue
            done.append(inst_id)
            sent[inst_id] = t
            if at != target:
                warnings.append(f"{names[inst_id]} : reprise décalée de {at - target:+d} image(s)")
            self._paused.pop(inst_id, None)
        delays = self._event_delay(clients, sent, "RESUMED")
        for e in report:
            e["delay_ms"] = round(delays.get(e["id"], -1) * 1000, 2)
            if not e["ok"]:
                continue
            self._debt.pop(e["id"], None)
            if e["id"] not in delays:
                warnings.append(f"{names[e['id']]} : reprise non confirmée par OBS, possible décalage d'une image")
                continue
            # Reprise appliquée en retard : images enlevées en trop, rendues à la prochaine reprise.
            late, sure = frames_late(delays[e["id"]], float(synced[e["id"]]["interval"]))
            e["resume_late"] = late
            if late:
                self._debt[e["id"]] = -late
            if not sure:
                warnings.append(f"{names[e['id']]} : OBS a appliqué la reprise très tard, possible décalage d'une image")
        return done, report

    # Départ et arrêt ------------------------------------------------------ #
    def _vendor(self, clients: dict, ids, raw: bool = False) -> dict:
        """État du module multiobs-sync, sans les instances où il est inutilisable (OBS lancé avant son
        installation, OBS d'une version pas vérifiée) : {instance: état}. raw : toutes les réponses."""
        got = self._wait(clients, {i: clients[i].batch([VENDOR_STATUS]) for i in ids}, 1.0)
        out = {}
        for inst_id, (_t, results) in got.items():
            data = _result(results, 0).get("responseData") if _ok(results, 0) else None
            if raw or sync_usable(data):
                out[inst_id] = data
        return out

    def _intervals(self, clients: dict) -> dict:
        got = self._wait(clients, {i: c.batch([{"requestType": "GetVideoSettings"}]) for i, c in clients.items()}, 2.0)
        out = {}
        for inst_id, (_t, results) in got.items():
            vs = _result(results, 0)
            if vs.get("fpsNumerator") and vs.get("fpsDenominator"):
                out[inst_id] = Fraction(vs["fpsDenominator"], vs["fpsNumerator"])
        return out

    def _settle(self, clients: dict, ids: list, moved, intervals: dict, timeout: float = 4.0) -> dict:
        """Attend que le module ait vu, pour chaque instance de `moved`, des images rendues après sa
        dernière reprise (les images arrivent au module 0,2 à 1 s après leur rendu)."""
        st = self._vendor(clients, ids)
        after = {i: st[i]["now"] + 3 * float(intervals[i]) * 1e9 for i in moved if i in st}
        deadline = time.perf_counter() + timeout
        while any(st.get(i, {}).get("last_cts", 0) <= t for i, t in after.items()) \
                and time.perf_counter() < deadline:
            time.sleep(0.03)
            st = self._vendor(clients, ids)
        return st

    def _do_align(self, targets) -> tuple[list, list]:
        clients, warnings = self._connect(targets)
        names = {t[0]: t[3] for t in targets}
        intervals = self._intervals(clients)
        raw = self._vendor(clients, list(intervals), raw=True)
        st = {i: d for i, d in raw.items() if sync_usable(d)}
        absent = [names[i] for i in clients if i not in st]
        if absent and st:
            other = [i for i in clients if i not in st and raw.get(i)]   # module là, mais OBS pas pris en charge
            missing = [names[i] for i in clients if i not in st and i not in other]
            if missing:
                warnings.append("départ non aligné pour " + ", ".join(missing)
                                + " (module de synchro pas chargé : éteins et rallume l'instance)")
            if other:
                warnings.append("départ non aligné pour " + ", ".join(names[i] for i in other)
                                + " (version d'OBS pas prise en charge par l'alignement)")
        ids = list(st)

        # Les fichiers commencent quand OBS a branché l'encodeur et reçu le son : on attend la 1re image.
        deadline = time.perf_counter() + ALIGN_WAIT_S
        ready = []
        while True:
            ready = [i for i in ids if st.get(i, {}).get("active") and st[i].get("frames", 0) > 0]
            if len(ready) == len(ids) or time.perf_counter() > deadline or not self._jobs.empty():
                break
            time.sleep(0.05)
            st.update(self._vendor(clients, ids))
        late = [names[i] for i in ids if i not in ready]
        if late and self._jobs.empty():
            warnings.append("départ non aligné pour " + ", ".join(late) + " (rec pas commencé)")
        report = {"op": "align", "rounds": [], "pauses": [], "absent": absent, "late": late}
        if len(ready) < 2:
            self.last_report = [report]
            return ready, warnings

        # Origine = heure qui correspond au début du fichier : on recule les plus précoces du nombre
        # d'images qui les sépare des autres (best_shifts), puis on vérifie avec le module.
        last_resume: dict[str, float] = {}
        frame_ns = {i: float(intervals[i]) * 1e9 for i in ready}
        for rnd in range(ALIGN_ROUNDS + 1):
            origins = {i: st[i]["origin"] for i in ready}
            latest = max(origins.values())
            report["rounds"].append({names[i]: round((latest - origins[i]) / frame_ns[i], 3) for i in ready})
            shifts, spread = best_shifts(origins, {i: frame_ns[i] for i in ready})
            report["spread"] = round(spread, 3)
            todo = {i: n for i, n in shifts.items() if n > 0}
            if not todo or not self._jobs.empty():
                break
            if rnd == ALIGN_ROUNDS:
                warnings.append("départ aligné au mieux : " + ", ".join(
                    f"{names[i]} {n:+d} image(s)" for i, n in todo.items()))
                break
            far = [names[i] for i, n in todo.items() if n * float(intervals[i]) > ALIGN_MAX_S]
            if far:
                warnings.append("départ non aligné : " + ", ".join(far) + " a démarré bien avant les autres")
                break
            # Micro-pause de toutes les instances, au moins ALIGN_MIN_PAUSE_S (une pause qui tient dans un
            # seul bloc audio de 21 ms fait perdre le son à OBS pour de bon), d'autant d'images en plus
            # pour celles parties en avance. Pause et reprise comme « Tout en pause » : la reprise n'est
            # programmée qu'une fois la pause confirmée par OBS, jamais collée à elle.
            wait = max(last_resume.get(i, 0.0) + ALIGN_GUARD_S - time.perf_counter() for i in ready)
            if wait > 0:
                time.sleep(wait)
            # Les avertissements de pause / reprise ne comptent pas ici : le module vérifie le résultat.
            paused, details = self._do_pause([t for t in targets if t[0] in ready])
            synced = {i: self._paused[i] for i in paused if i in self._paused}
            if not synced:
                warnings += details
                break
            now = time.perf_counter()
            need = {i: max(math.ceil((now - p["t"]) / float(p["interval"])) + LEAD_FRAMES + 1,
                           math.ceil(ALIGN_MIN_PAUSE_S / float(p["interval"]))) for i, p in synced.items()}
            base = max(need[i] - todo.get(i, 0) for i in synced)
            frames = {i: todo.get(i, 0) + base for i in synced}
            report["pauses"].append({names[i]: n for i, n in frames.items()})
            resumed, _report = self._resume_synced(clients, names, synced, frames, details)
            stuck = [i for i in synced if i not in resumed]
            if stuck:   # jamais une instance laissée en pause par l'alignement
                self._wait(clients, {i: clients[i].batch([{"requestType": "ResumeRecord"}]) for i in stuck}, 2.0)
                for inst_id in stuck:
                    self._paused.pop(inst_id, None)
                warnings += details
            for inst_id in synced:
                self._debt.pop(inst_id, None)   # écart vérifié et corrigé ici, grâce au module
                last_resume[inst_id] = time.perf_counter()
            st = self._settle(clients, ready, list(synced), intervals)
            if any(i not in st for i in ready):
                warnings.append("départ : module de synchro muet, alignement interrompu")
                break
        self.last_report = [report]
        return ready, warnings

    def _do_stop(self, targets) -> tuple[list, list]:
        """Arrêt : les fichiers partis ensemble finissent sur la même position (même nombre d'images).
        La position de chaque image vient du module ; sans lui, image la plus proche d'une même heure
        (deux instances déphasées peuvent alors couper de part et d'autre de cette heure)."""
        clients, warnings = self._connect(targets)
        names = {t[0]: t[3] for t in targets}
        # Sonde dans la boucle d'images : image en cours et heure d'OBS au même instant.
        probe = [{"requestType": "GetStats"}, {"requestType": "GetVideoSettings"}, VENDOR_STATUS]
        pending = {i: c.batch(probe) for i, c in clients.items()}
        stops, ref, deadline = {}, None, time.perf_counter() + 2.0
        while pending and time.perf_counter() < deadline:
            pump([clients[i] for i in pending], 0.05)
            for inst_id in list(pending):
                c = clients[inst_id]
                if pending[inst_id] not in c.responses:
                    continue
                t, results = c.responses.pop(pending.pop(inst_id))
                vs = _result(results, 1)
                num, den = vs.get("fpsNumerator") or 0, vs.get("fpsDenominator") or 0
                frame = _result(results, 0).get("renderTotalFrames")
                if not num or not den or frame is None:
                    continue
                interval = den / num * 1e9
                mod = (_result(results, 2).get("responseData") or {}) if _ok(results, 2) else {}
                if not sync_usable(mod):
                    mod = {}
                tick = mod.get("now") or t * 1e9   # sans le module : heure de réception (même horloge)
                origin = mod.get("origin") if mod.get("active") and mod.get("frames") else None
                # Position dans le fichier de l'image de ce tour de boucle : OBS se réveille après
                # l'heure de l'image (jusqu'à presque une image plus tard si Windows le bride), jamais avant.
                pos = math.floor((tick - origin) / interval + 0.01) if origin else None
                if ref is None:   # la 1re qui répond fixe la fin, quelques images plus loin
                    ref = {"time": tick + STOP_LEAD_FRAMES * interval, "interval": interval, "origin": origin,
                           "pos": pos + STOP_LEAD_FRAMES if origin else None}
                if pos is not None and ref["pos"] is not None and interval == ref["interval"] \
                        and abs(origin - ref["origin"]) < interval:
                    ahead = ref["pos"] - pos                      # débuts alignés : même position de fin
                else:
                    ahead = round((ref["time"] - tick) / interval)
                at = frame + max(2, ahead)
                wait = at - (frame + 1)
                stops[inst_id] = (c.batch([{"requestType": "GetStats"},
                                           {"requestType": "Sleep", "requestData": {"sleepFrames": wait}},
                                           {"requestType": "GetStats"}, {"requestType": "StopRecord"}]),
                                  frame, at)
        got = self._wait(clients, {i: s[0] for i, s in stops.items()}, 3.0)
        done, report = [], []
        for inst_id, (t, results) in got.items():
            _rid, frame, at = stops[inst_id]
            first = _result(results, 0).get("renderTotalFrames")
            stopped_at = _result(results, 2).get("renderTotalFrames")
            ok = _ok(results, 3)
            report.append({"id": inst_id, "op": "stop", "probe": frame, "first": first, "target": at,
                           "stop_frame": stopped_at, "ok": ok})
            if ok:
                done.append(inst_id)
                if stopped_at is not None and stopped_at != at:
                    warnings.append(f"{names[inst_id]} : arrêt décalé de {stopped_at - at:+d} image(s)")
        self.last_report = report
        return done, warnings
