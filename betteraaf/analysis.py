"""Détection : clips dupliqués, quasi identiques (null test), stéréo double mono."""

from dataclasses import dataclass, replace
from fractions import Fraction
import math

import numpy as np

from .model import lr_split

BLOCK = 48000 * 10  # lecture par blocs de ~10 s


@dataclass
class Finding:
    kind: str            # "exact" | "near" | "dualmono"
    clip: object
    ref: object = None   # clip conservé (doublons)
    residual_db: float = float("-inf")
    convertible: bool = True
    note: str = ""


@dataclass
class Results:
    exact: list
    near: list
    dualmono: list
    warnings: list
    n_clips: int = 0


def db(x):
    return -math.inf if x <= 0 else 10 * math.log10(x)


def null_residual(pool, cha, offa, chb, offb, n, stop=None):
    """Résidu (dB) de A - B rapporté au plus fort des deux signaux.
    -inf = identiques à l'échantillon près (ou tous deux silencieux)."""
    ea = eb = ed = 0.0
    done = 0
    while done < n:
        if stop and stop():
            raise InterruptedError
        k = min(BLOCK, n - done)
        a = pool.read(cha, offa + done, k).astype(np.float64)
        b = pool.read(chb, offb + done, k).astype(np.float64)
        ea += float(np.dot(a, a))
        eb += float(np.dot(b, b))
        d = a - b
        ed += float(np.dot(d, d))
        done += k
    ref = max(ea, eb)
    if ref <= 0:
        return -math.inf
    if ed <= 0:
        return -math.inf
    return db(ed / ref)


def is_muted(c):
    """Clip déjà muté par un gain de clip à 0 (-inf dB) : ignoré par l'analyse."""
    for w in c.wrappers:
        try:
            name = (w.operation.name or "").lower()
            if "gain" not in name:
                continue
            for p in w.parameters:
                v = p["Value"].value if "Value" in p.keys() else None
                if v is not None and float(v) == 0.0:
                    return True
        except Exception:
            pass
    return False


def prepare_clips(comp, pool, log):
    """Ouvre les médias, calcule les positions en échantillons, déplie les
    clips 'mono' qui pointent en fait vers un fichier multicanal."""
    clips = []
    for c in comp.all_clips():
        if c.error or not c.channels:
            continue
        if c.muted or is_muted(c):
            continue
        ok = True
        for ch in c.channels:
            if pool.prepare(ch) is None:
                ok = False
                c.error = pool.errors.get(ch.mob_id, "média illisible")
                break
        if not ok:
            continue
        if c.kind == "mono":
            ch = c.channels[0]
            src = pool.source(ch.mob_id)
            if src.nch > 1 and not (ch.phys and ch.phys <= src.nch):
                c.channels = [replace(ch, channel=i) for i in range(src.nch)]
                c.kind = "multi"
        rates = {ch.sample_rate for ch in c.channels}
        c.sr = c.channels[0].sample_rate
        if len(rates) > 1:
            c.error = "fréquences d'échantillonnage différentes entre canaux"
            continue
        e = c.track.edit_rate
        c.s0 = int(round(Fraction(c.position) * c.sr / e))
        c.s1 = int(round(Fraction(c.position + c.length) * c.sr / e))
        clips.append(c)
    return clips


def pair_split_stereo(clips):
    """Appaire les clips mono L/R posés au même endroit sur deux pistes
    (export Pro Tools 'stéréo -> mono séparé')."""
    by_range = {}
    for c in clips:
        if c.nch == 1:
            by_range.setdefault((c.s0, c.s1, c.sr), []).append(c)
    pairs = []
    for group in by_range.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda c: c.track.index)
        used = set()
        for i, a in enumerate(group):
            if a.uid in used:
                continue
            for b in group[i + 1:]:
                if b.uid in used or b.track is a.track:
                    continue
                side = is_lr_pair(a, b)
                if side:
                    L, R = (a, b) if side == "ab" else (b, a)
                    L.partner, R.partner = R, L
                    L.side, R.side = "L", "R"
                    used.update((a.uid, b.uid))
                    pairs.append((L, R))
                    break
    return pairs


def is_lr_pair(a, b):
    ca, cb = a.channels[0], b.channels[0]
    tba, sa = lr_split(a.track.name)
    tbb, sb = lr_split(b.track.name)
    if sa and sb and tba == tbb and {sa, sb} == {"L", "R"}:
        return "ab" if sa == "L" else "ba"
    nba, na = lr_split(a.name)
    nbb, nb = lr_split(b.name)
    if na and nb and nba == nbb and {na, nb} == {"L", "R"}:
        return "ab" if na == "L" else "ba"
    if ca.master_id and ca.master_id == cb.master_id and ca.master_slot != cb.master_slot:
        return "ab" if ca.master_slot < cb.master_slot else "ba"
    if ca.mob_id == cb.mob_id and ca.channel != cb.channel:
        return "ab" if ca.channel < cb.channel else "ba"
    return None


def aligned_same(a, b):
    """Même média, même canal, même calage : doublon exact sans lire l'audio."""
    if a.nch != b.nch:
        return False
    for x, y in zip(a.channels, b.channels):
        if x.mob_id != y.mob_id or x.channel != y.channel:
            return False
        if (x.sample_offset - a.s0) != (y.sample_offset - b.s0):
            return False
    return True


def analyze(comp, pool, dup_db=-60.0, dm_db=-60.0, progress=None, stop=None, log=print):
    warnings = []
    clips = prepare_clips(comp, pool, log)
    total = sum(1 for _ in comp.all_clips())
    for c in comp.all_clips():
        if c.error:
            warnings.append(f"{c.label()} : {c.error}")
    pair_split_stereo(clips)

    # --- candidats doublons : un clip entièrement recouvert par un autre clip
    #     (sur une autre piste) au même nombre de canaux
    order = sorted(clips, key=lambda c: (c.s0, -c.s1, c.track.index))
    cands = []
    active = []
    for c in order:
        active = [a for a in active if a.s1 > c.s0]
        for a in active:
            if a.track is c.track or a.partner is c or a.nch != c.nch or a.sr != c.sr:
                continue
            if a.s0 <= c.s0 and c.s1 <= a.s1:
                if (a.s0, a.s1) == (c.s0, c.s1):
                    keep, red = (a, c) if a.track.index < c.track.index else (c, a)
                else:
                    keep, red = a, c
                cands.append((red, keep))
            elif c.s0 <= a.s0 and a.s1 <= c.s1:
                cands.append((a, c))
        active.append(c)

    exact, near, dual = [], [], []
    flagged = set()
    n_steps = max(1, len(cands) + len(clips))
    step = 0

    def tick(msg=""):
        nonlocal step
        step += 1
        if progress:
            progress(step / n_steps, msg)

    # doublons exacts d'abord (aucune lecture audio nécessaire)
    rest = []
    for red, keep in cands:
        if red.uid not in flagged and aligned_same(red, keep):
            flagged.add(red.uid)
            exact.append(Finding("exact", red, keep, -math.inf))
            tick()
        else:
            rest.append((red, keep))
    for red, keep in rest:
        tick(red.name)
        if red.uid in flagged:
            continue
        try:
            worst = -math.inf
            for x, y in zip(red.channels, keep.channels):
                r = null_residual(pool, x, 0, y, red.s0 - keep.s0, red.s1 - red.s0, stop)
                worst = max(worst, r)
                if worst > dup_db:
                    break
        except InterruptedError:
            raise
        except Exception as e:
            warnings.append(f"{red.label()} : lecture impossible ({e})")
            continue
        if worst <= dup_db:
            flagged.add(red.uid)
            if worst == -math.inf:
                exact.append(Finding("exact", red, keep, worst, note="audio identique (fichiers différents)"))
            else:
                near.append(Finding("near", red, keep, worst))

    # --- double mono
    for c in clips:
        tick(c.name)
        if c.nch == 2:
            L, R, conv, note = c.channels[0], c.channels[1], c.kind == "combiner", ""
            if not conv:
                note = "conversion non supportée pour ce format de clip"
            target, offR = c, 0
        elif c.side == "L":
            L, R, conv, note = c.channels[0], c.partner.channels[0], True, "paire L/R sur 2 pistes"
            target = c
            offR = 0
        else:
            continue
        try:
            if L.mob_id == R.mob_id and L.channel == R.channel and L.sample_offset == R.sample_offset:
                r = -math.inf
            else:
                r = null_residual_pair(pool, c, L, R, stop)
        except InterruptedError:
            raise
        except Exception as e:
            warnings.append(f"{c.label()} : lecture impossible ({e})")
            continue
        if r <= dm_db:
            dual.append(Finding("dualmono", target, c.partner, r, conv, note))

    return Results(exact, near, dual, warnings, total)


def null_residual_pair(pool, c, L, R, stop):
    return null_residual(pool, L, 0, R, 0, c.s1 - c.s0, stop)
