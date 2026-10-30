"""Lecture d'un AAF : pistes audio, clips, et chaîne jusqu'aux fichiers médias.

Formats de clips reconnus :
  - "mono"     : SourceClip simple (1 canal)
  - "combiner" : OperationGroup "Audio Channel Combiner" (piste stéréo / multicanal
                 exportée en multicanal par Pro Tools ou Media Composer)
  - "multi"    : SourceClip pointant directement vers un fichier multicanal
Les pistes stéréo exportées "en mono séparé" (Piste.L / Piste.R) sont des clips
"mono" que l'analyse appaire ensuite (voir analysis.pair_split_stereo).
"""

from dataclasses import dataclass, field
from fractions import Fraction
import os
import re

import aaf2
from aaf2.components import (Sequence, SourceClip, Filler, OperationGroup,
                             Transition, Timecode, Selector)
from aaf2.mobs import MasterMob, SourceMob, CompositionMob
from aaf2 import essence as aaf_essence


class Unresolved(Exception):
    pass


@dataclass
class ChannelRef:
    """Un canal audio d'un clip, rattaché à un fichier média."""
    mob_id: str          # SourceMob "fichier"
    phys: int            # numéro de canal physique déclaré (1..n) ou 0 si inconnu
    sample_offset: int   # échantillon du fichier correspondant au début du clip
    sample_rate: int
    master_id: str = ""  # MasterMob intermédiaire (pour appairer L/R)
    master_slot: int = 0
    channel: int = -1    # canal réel dans le fichier, fixé à la lecture du média

    def key(self):
        return (self.mob_id, self.channel if self.channel >= 0 else self.phys)


@dataclass(eq=False)
class Clip:
    uid: int
    track: "Track"
    index: int            # position dans la Sequence de la piste
    position: int         # en unités d'édition de la piste
    length: int
    name: str
    kind: str             # mono / combiner / multi / unknown
    channels: list = field(default_factory=list)
    error: str = ""
    component: object = None   # objet pyaaf2 (vivant)
    combiner: object = None    # OperationGroup combiner, si kind == combiner
    wrappers: list = field(default_factory=list)  # OperationGroups autour (ext -> int)
    muted: bool = False        # déjà muté (clip désactivé Avid)
    # rempli par l'analyse
    partner: "Clip" = None     # stéréo "split" : clip de l'autre canal
    side: str = ""             # "L" / "R" pour les paires split
    sr: int = 0
    s0: int = 0                # début en échantillons (timeline)
    s1: int = 0                # fin en échantillons
    votes: dict = None         # contenu détecté (classify.py)

    @property
    def nch(self):
        return len(self.channels)

    def label(self):
        return f"{self.track.name} / {self.name}"


@dataclass(eq=False)
class Track:
    index: int
    slot: object
    slot_id: int
    name: str
    edit_rate: Fraction
    phys: int
    sequence: object
    clips: list = field(default_factory=list)


@dataclass
class Composition:
    mob: object
    name: str
    tracks: list
    tc_start: int      # en images
    tc_fps: int
    tc_rate: Fraction  # edit rate du slot timecode
    drop: bool = False

    def all_clips(self):
        for t in self.tracks:
            yield from t.clips


def frac(r):
    return Fraction(r.numerator, r.denominator) if hasattr(r, "numerator") else Fraction(r)


def prop(obj, name, default=None):
    try:
        if name in obj.keys():
            v = obj[name].value
            return default if v is None else v
    except Exception:
        pass
    return default


FILE_DESCRIPTORS = (aaf_essence.WAVEDescriptor, aaf_essence.AIFCDescriptor,
                    aaf_essence.SoundDescriptor, aaf_essence.MultipleDescriptor)


def is_file_mob(mob):
    if not isinstance(mob, SourceMob):
        return False
    d = mob.descriptor
    if d is None:
        return False
    if isinstance(d, (aaf_essence.TapeDescriptor, aaf_essence.ImportDescriptor)):
        return False
    return isinstance(d, aaf_essence.FileDescriptor)


def descriptor_rate(d, fallback):
    for k in ("SampleRate", "AudioSamplingRate"):
        v = prop(d, k)
        if v:
            try:
                r = frac(v)
                if r > 1000:
                    return r
            except Exception:
                pass
    return fallback


def resolve_sourceclip(sc, rate_from):
    """Suit SourceClip -> MasterMob -> ... -> SourceMob fichier.
    Retourne un ChannelRef (offset relatif au début du clip)."""
    mob = sc.mob
    slot_id = sc.slot_id
    start = Fraction(sc.start or 0)
    rate = rate_from
    phys = 0
    master_id, master_slot = "", 0
    for _ in range(12):
        if mob is None:
            raise Unresolved("media missing from the AAF (mob not found)")
        try:
            slot = mob.slot_at(slot_id)
        except Exception:
            raise Unresolved("media track not found")
        srate = frac(slot.edit_rate)
        start_s = start * srate / rate
        if isinstance(mob, MasterMob) and not master_id:
            master_id, master_slot = str(mob.mob_id), slot_id
            phys = prop(slot, "PhysicalTrackNumber", 0) or 0
        if is_file_mob(mob):
            d = mob.descriptor
            sr = descriptor_rate(d, srate)
            if not phys:
                phys = prop(slot, "PhysicalTrackNumber", 0) or 0
            off = start_s * sr / srate
            return ChannelRef(str(mob.mob_id), int(phys), int(round(off)), int(round(sr)),
                              master_id, master_slot)
        seg = slot.segment
        comp, comp_pos = seg, 0
        if isinstance(seg, Sequence):
            comp = None
            for _i, p, c in seg.positions():
                if isinstance(c, Transition):
                    continue
                if p <= start_s < p + c.length or (comp is None and p >= start_s):
                    comp, comp_pos = c, p
                    if p <= start_s < p + c.length:
                        break
            if comp is None:
                raise Unresolved("empty media sequence")
        if not isinstance(comp, SourceClip):
            raise Unresolved("media not linked (%s)" % type(comp).__name__)
        start = Fraction(comp.start or 0) + (start_s - comp_pos)
        mob, slot_id, rate = comp.mob, comp.slot_id, srate
    raise Unresolved("media chain too deep")


def is_combiner(og):
    try:
        name = (og.operation.name or "").lower()
    except Exception:
        name = ""
    if "combiner" in name:
        return True
    segs = list(og.segments)
    if len(segs) >= 2 and "dissolve" not in name and "gain" not in name and "pan" not in name:
        return all(isinstance(unwrap(s)[0], SourceClip) for s in segs)
    return False


def tagged(comp, name):
    try:
        for tv in comp["ComponentAttributeList"].value or []:
            if tv.name == name:
                return tv.value
    except Exception:
        pass
    return None


def is_disabled(comp):
    return isinstance(comp, Selector) and tagged(comp, "_DISABLE_CLIP_FLAG") == 1


def unwrap(seg, info=None):
    """Retire les OperationGroups 'enveloppes' (gain de clip, etc.) à 1 entrée
    et les Selectors (clip désactivé Avid : on regarde le clip d'origine)."""
    wrappers = []
    while True:
        if isinstance(seg, Selector):
            if is_disabled(seg):
                if info is not None:
                    info["muted"] = True
                alts = list(seg["Alternates"].value or [])
                if not alts:
                    break
                seg = alts[0]
            else:
                seg = seg["Selected"].value
            continue
        if isinstance(seg, OperationGroup) and not is_combiner(seg):
            segs = list(seg.segments)
            if len(segs) != 1:
                break
            wrappers.append(seg)
            seg = segs[0]
            continue
        break
    return seg, wrappers


def clip_name(sc):
    n = prop(sc, "Name")
    if n:
        return n
    try:
        m = sc.mob
        if m is not None and m.name:
            return m.name
    except Exception:
        pass
    return "(sans nom)"


def build_clip(uid, track, index, pos, comp):
    info = {}
    inner, wrappers = unwrap(comp, info)
    clip = Clip(uid, track, index, int(pos), int(comp.length), "", "unknown",
                component=comp, wrappers=wrappers, muted=bool(info.get("muted")))
    try:
        if isinstance(inner, SourceClip):
            clip.name = clip_name(inner)
            clip.kind = "mono"
            clip.channels = [resolve_sourceclip(inner, track.edit_rate)]
        elif isinstance(inner, OperationGroup) and is_combiner(inner):
            clip.kind = "combiner"
            clip.combiner = inner
            names = []
            for s in inner.segments:
                sc, _w = unwrap(s)
                if not isinstance(sc, SourceClip):
                    raise Unresolved("unsupported channel combiner input")
                names.append(clip_name(sc))
                clip.channels.append(resolve_sourceclip(sc, track.edit_rate))
            clip.name = names[0] if names else "(combiner)"
        else:
            clip.name = type(inner).__name__
            clip.error = "unsupported clip type (%s)" % type(inner).__name__
    except Unresolved as e:
        clip.error = str(e)
        if not clip.name:
            clip.name = "(clip)"
    return clip


def find_timecode(mob):
    for slot in mob.slots:
        seg = slot.segment
        comps = list(seg.components) if isinstance(seg, Sequence) else [seg]
        for c in comps:
            if isinstance(c, Timecode):
                return int(c.start or 0), int(c.fps or 25), frac(slot.edit_rate), bool(c.drop)
    return 0, 25, Fraction(25), False


def sound_slots(mob):
    out = []
    for slot in mob.slots:
        seg = slot.segment
        try:
            kind = seg.media_kind
        except Exception:
            kind = None
        if kind and kind.lower() == "sound":
            out.append(slot)
    return out


def list_compositions(f):
    comps = [m for m in f.content.toplevel() if isinstance(m, CompositionMob)]
    if not comps:
        comps = list(f.content.compositionmobs())
    comps.sort(key=lambda m: -len(sound_slots(m)))
    return comps


def track_sequence(slot):
    """La Sequence d'une piste, sous d'éventuels effets de piste (Audio Pan...)."""
    seg = slot.segment
    while isinstance(seg, OperationGroup) and not is_combiner(seg) and len(list(seg.segments)) == 1:
        seg = list(seg.segments)[0]
    return seg


def set_track_sequence(slot, new_seq):
    """Remplace la Sequence d'une piste en gardant les effets de piste."""
    parent, seg = None, slot.segment
    while isinstance(seg, OperationGroup) and not is_combiner(seg) and len(list(seg.segments)) == 1:
        parent, seg = seg, list(seg.segments)[0]
    if parent is None:
        slot.segment = new_seq
    else:
        parent.segments[0] = new_seq


def wrap_in_sequence(slot):
    seg = track_sequence(slot)
    length = seg.length
    copy = seg.copy()
    seq = slot.root.create.Sequence("sound")
    seq["Components"].value = []
    set_track_sequence(slot, seq)
    seq.components.append(copy)
    seq.length = length
    return seq


def read_composition(f, mob, normalize=False):
    """Construit le modèle. normalize=True (écriture) : garantit que chaque piste
    contient une Sequence."""
    tc_start, fps, tc_rate, drop = find_timecode(mob)
    tracks, uid = [], 0
    for i, slot in enumerate(sound_slots(mob)):
        seg = track_sequence(slot)
        if not isinstance(seg, Sequence) and normalize:
            seg = wrap_in_sequence(slot)
        t = Track(i, slot, slot.slot_id, slot.name or f"Audio {i + 1}",
                  frac(slot.edit_rate), prop(slot, "PhysicalTrackNumber", i + 1), seg)
        if isinstance(seg, Sequence):
            items = list(seg.positions())
        else:
            items = [(0, 0, seg)]
        for index, pos, comp in items:
            if isinstance(comp, (Filler, Transition)):
                continue
            if isinstance(comp, (SourceClip, OperationGroup, Selector)):
                t.clips.append(build_clip(uid, t, index, pos, comp))
                uid += 1
        tracks.append(t)
    return Composition(mob, mob.name or "Composition", tracks, tc_start, fps, tc_rate, drop)


def timecode(comp, track, pos):
    """Position (unités d'édition de la piste) -> 'HH:MM:SS:FF'."""
    frames = comp.tc_start + int(Fraction(pos) * comp.tc_fps / track.edit_rate)
    fps = comp.tc_fps or 25
    ff = frames % fps
    s = frames // fps
    return "%02d:%02d:%02d:%02d" % (s // 3600 % 24, s // 60 % 60, s % 60, ff)


def duration_str(track, length):
    sec = float(Fraction(length) / track.edit_rate)
    m, s = divmod(sec, 60)
    return "%d:%06.3f" % (m, s)


LR_RE = re.compile(r"^(.*?)[\s._\-](L|R|Left|Right|G|D)$", re.IGNORECASE)


def lr_split(name):
    """'Dial.L' -> ('dial', 'L') ; sinon (name, '')."""
    if not name:
        return "", ""
    base = os.path.splitext(name)[0] if re.search(r"\.(wav|aif|aiff|mxf)$", name, re.I) else name
    m = LR_RE.match(base)
    if not m or not m.group(1):
        return base.lower(), ""
    side = m.group(2).upper()
    side = {"LEFT": "L", "G": "L", "RIGHT": "R", "D": "R"}.get(side, side)
    return m.group(1).lower().rstrip(" ._-"), side
