"""Contrôle de cohérence d'un AAF (plus strict que la norme, orienté Pro Tools).
Usage : python check_aaf.py fichier.aaf"""
import sys
import collections

import aaf2
from aaf2.components import Sequence, SourceClip, Filler, OperationGroup, Transition, Selector

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from betteraaf import model  # noqa


def inputs_of(seg):
    inner, _w = model.unwrap(seg)
    return inner


def check(path):
    errs = []
    with aaf2.open(path, "r") as f:
        mob = model.list_compositions(f)[0]
        phys = collections.Counter()
        for slot in model.sound_slots(mob):
            name = slot.name
            phys[slot["PhysicalTrackNumber"].value if "PhysicalTrackNumber" in slot.keys() else None] += 1
            fmt = None
            if "TimelineMobAttributeList" in slot.keys():
                names = [tv.name for tv in slot["TimelineMobAttributeList"].value]
                dup = [n for n, c in collections.Counter(names).items() if c > 1]
                if dup:
                    errs.append(f"{name}: attributs en double {dup}")
                for tv in slot["TimelineMobAttributeList"].value:
                    if tv.name == "_TRACK_FORMAT":
                        fmt = tv.value
            want = 2 if fmt == 2 else 1
            seq = model.track_sequence(slot)
            if not isinstance(seq, Sequence):
                errs.append(f"{name}: pas de Sequence")
                continue
            comps = list(seq.components)
            total = 0
            for i, c in enumerate(comps):
                if isinstance(c, Transition):
                    total -= c.length
                    prev = comps[i - 1] if i > 0 else None
                    nxt = comps[i + 1] if i + 1 < len(comps) else None
                    if prev is None or nxt is None:
                        errs.append(f"{name}: fondu en bout de piste (#{i})")
                    elif isinstance(prev, Filler) and isinstance(nxt, Filler):
                        errs.append(f"{name}: fondu entre deux vides (#{i})")
                    elif isinstance(prev, Transition) or isinstance(nxt, Transition):
                        errs.append(f"{name}: deux fondus collés (#{i})")
                    else:
                        if c.length > prev.length or c.length > nxt.length:
                            errs.append(f"{name}: fondu plus long que ses voisins (#{i})")
                    continue
                total += c.length
                if isinstance(c, Filler):
                    if i > 0 and isinstance(comps[i - 1], Filler):
                        errs.append(f"{name}: deux vides consécutifs (#{i})")
                    continue
                inner = inputs_of(c)
                # cohérence longueurs des enveloppes
                seg = c
                while isinstance(seg, (OperationGroup, Selector)):
                    if isinstance(seg, Selector):
                        seg = seg["Selected"].value
                        continue
                    for s in seg.segments:
                        if s.length != seg.length:
                            errs.append(f"{name}: longueur d'entrée différente dans {seg.operation.name} (#{i})")
                    segs = list(seg.segments)
                    if len(segs) != 1:
                        break
                    seg = segs[0]
                if isinstance(inner, OperationGroup):
                    n = len(list(inner.segments))
                elif isinstance(inner, SourceClip):
                    n = 1
                else:
                    n = 0
                if n != want:
                    errs.append(f"{name}: clip à {n} canal(aux) sur une piste {'stéréo' if want == 2 else 'mono'} (#{i})")
                for sc in ([inner] if isinstance(inner, SourceClip) else
                           [model.unwrap(s)[0] for s in inner.segments] if isinstance(inner, OperationGroup) else []):
                    if not isinstance(sc, SourceClip) or sc.mob is None:
                        errs.append(f"{name}: référence média cassée (#{i})")
            if total != seq.length:
                errs.append(f"{name}: longueur de séquence {seq.length} != somme {total}")
        for k, v in phys.items():
            if v > 1:
                errs.append(f"numéro de piste {k} utilisé {v} fois")
    return errs


if __name__ == "__main__":
    for p in sys.argv[1:]:
        e = check(p)
        print(("OK   " if not e else "ERR  ") + p + (f"  ({len(e)} problèmes)" if e else ""))
        for x in e[:15]:
            print("     -", x)
