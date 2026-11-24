"""Disposition des cartes : nombre de colonnes, aperçu dessous ou à côté, et taille des aperçus.

Pour n cartes dans la zone visible, on essaie chaque nombre de colonnes, avec les commandes sous l'aperçu
(« vertical ») ou à sa droite (« side », écrans larges avec plusieurs lignes), et on garde ce qui donne les
plus grands aperçus SANS défilement. Une carte seule ne devient pas plus grande qu'avec deux cartes (sinon
elle déborderait de l'écran). Si rien ne tient, cartes au plus petit et défilement, comme avant.
Calcul pur (sans Qt) : testable à part.
"""
from dataclasses import dataclass

GAP = 12              # espace entre les cartes
CARD_MIN_W = 440      # carte « vertical » la plus étroite (en-tête : nom, état, caméra, type, menu)
SIDE_MIN_PW = 400     # aperçu le plus étroit à côté des commandes
SIDE_W = 320          # colonne des commandes à droite de l'aperçu
SIDE_BIAS = 0.95      # à taille presque égale, les commandes sous l'aperçu (disposition habituelle)
EMPTY_CELL_COST = 0.03   # case vide (ex. 3 cartes sur 2 × 2) : un peu moins bien


@dataclass(frozen=True)
class Chrome:
    """Ce qu'il y a autour de l'aperçu dans une carte (pixels logiques)."""
    pad_w: int      # bords gauche + droit
    top: int        # au-dessus de l'aperçu : bandeau, en-tête
    below: int      # sous l'aperçu, commandes dessous : vumètres, infos, disque, boutons, bas
    bottom: int     # bas de la carte sous l'aperçu ou les commandes, commandes à droite
    side_h: int     # hauteur de la colonne des commandes à droite


@dataclass(frozen=True)
class Plan:
    mode: str       # "vertical" ou "side"
    cols: int
    rows: int
    pw: int         # largeur de l'aperçu (16:9)
    card_w: int
    fits: bool      # tout tient dans la hauteur visible

    @property
    def ph(self) -> int:
        return round(self.pw * 9 / 16)


def card_width(mode: str, pw: int, ch: Chrome) -> int:
    return pw + ch.pad_w + (GAP + SIDE_W if mode == "side" else 0)


def card_height(mode: str, pw: int, ch: Chrome) -> int:
    ph = round(pw * 9 / 16)
    if mode == "side":
        return ch.top + max(ph, ch.side_h) + ch.bottom
    return ch.top + ph + ch.below


def _candidates(n: int, avail_w: int, avail_h: int, ch: Chrome):
    for mode in ("vertical", "side"):
        extra_w = card_width(mode, 0, ch)
        for cols in range(1, n + 1):
            rows = -(-n // cols)
            if cols > 1 and (rows - 1) * cols >= n:   # une ligne vide
                continue
            cell_w = (avail_w - (cols - 1) * GAP) / cols
            cell_h = (avail_h - (rows - 1) * GAP) / rows
            if mode == "side":
                room_h = cell_h - ch.top - ch.bottom
                if room_h < ch.side_h:   # la colonne des commandes ne tient pas
                    continue
            else:
                room_h = cell_h - ch.top - ch.below
            pw = int(min(cell_w - extra_w, room_h * 16 / 9))
            if pw < (SIDE_MIN_PW if mode == "side" else CARD_MIN_W - ch.pad_w):
                continue
            yield mode, cols, rows, pw


def _best(n: int, avail_w: int, avail_h: int, ch: Chrome, cap: int | None = None) -> Plan | None:
    best, best_score = None, 0.0
    for mode, cols, rows, pw in _candidates(n, avail_w, avail_h, ch):
        if cap:
            pw = min(pw, cap)
        score = pw * (SIDE_BIAS if mode == "side" else 1.0) * (1 - EMPTY_CELL_COST * (cols * rows - n))
        if score > best_score + 0.5:
            best_score = score
            best = Plan(mode, cols, rows, pw, card_width(mode, pw, ch), True)
    return best


def plan(n: int, avail_w: int, avail_h: int, ch: Chrome) -> Plan:
    """Disposition de n cartes (n ≥ 1) dans une zone visible de avail_w × avail_h."""
    n = max(1, n)
    cap = None
    if n == 1:   # une carte seule : pas plus grande qu'avec deux
        two = _best(2, avail_w, avail_h, ch)
        cap = two.pw if two else None
    best = _best(n, avail_w, avail_h, ch, cap)
    if best:
        return best
    # Rien ne tient sans défiler : le plus de colonnes possible, cartes sur toute la largeur.
    cols = max(1, min(n, (avail_w + GAP) // (CARD_MIN_W + GAP)))
    rows = -(-n // cols)
    pw = max(CARD_MIN_W - ch.pad_w, int((avail_w - (cols - 1) * GAP) / cols) - ch.pad_w)
    return Plan("vertical", cols, rows, pw, card_width("vertical", pw, ch), False)
