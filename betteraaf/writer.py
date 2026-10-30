"""Écriture : on travaille toujours sur une COPIE de l'AAF d'origine."""

import os
import shutil
from fractions import Fraction

import numpy as np
import aaf2
from aaf2.components import Sequence, OperationGroup, Filler

from . import model, media

MONO_GAIN_AUID = "9d2ea894-0968-11d3-8a38-0050040ef7d2"   # OperationDef_MonoAudioGain
AMPLITUDE_AUID = "e4962321-2267-11d3-8a4c-0050040ef7d2"   # ParameterDef_Amplitude
MUTE_AMPLITUDE = Fraction(34, 536870912)  # environ -144 dB, le minimum du gain de clip Pro Tools


class Plan:
    """Ce que l'utilisateur a choisi.
    dup_action / near_action : 'delete' | 'mute' | 'none'
    dm_action : 'mono_delete' | 'mono_mute' | 'none'
    mute_method : 'avid' (clip désactivé, comme le mute de Media Composer)
                | 'gain' (gain de clip -inf) | 'track' (déplacé sur une piste MUTE)
    *_uids : clips cochés (uid du modèle)."""

    def __init__(self, dup_action="delete", near_action="mute", dm_action="mono_delete",
                 mute_method="track", exact_uids=(), near_uids=(), dm_uids=()):
        self.dup_action, self.near_action, self.dm_action = dup_action, near_action, dm_action
        self.mute_method = mute_method
        self.exact_uids, self.near_uids, self.dm_uids = set(exact_uids), set(near_uids), set(dm_uids)


class Editor:
    def __init__(self, f, comp, log=print):
        self.f, self.comp, self.log = f, comp, log
        self.new_tracks = {}   # (track.index, suffix) -> liste [(pos, component)]
        self.gain_def = None
        self.amp_def = None

    # ---------- définitions de gain ----------
    def _gain_defs(self):
        if self.gain_def is None:
            d = self.f.dictionary
            try:
                self.amp_def = d.lookup_parameterdef(AMPLITUDE_AUID)
            except Exception:
                self.amp_def = None
            if self.amp_def is None:
                self.amp_def = self.f.create.ParameterDef(AMPLITUDE_AUID, "Amplitude", "", "Rational")
                d.register_def(self.amp_def)
            try:
                self.gain_def = d.lookup_operationdef(MONO_GAIN_AUID)
            except Exception:
                self.gain_def = None
            if self.gain_def is None:
                op = self.f.create.OperationDef(MONO_GAIN_AUID, "Mono Audio Gain", "")
                op.media_kind = "sound"
                d.register_def(op)
                op["IsTimeWarp"].value = False
                op["NumberInputs"].value = 1
                op["OperationCategory"].value = "OperationCategory_Effect"
                op["ParametersDefined"].append(self.amp_def)
                self.gain_def = op
        return self.gain_def, self.amp_def

    def muted(self, component, method="gain"):
        if method == "avid":
            return self.disabled(component)
        return self.gained(component)

    def disabled(self, component):
        """Clip désactivé façon Media Composer : Selector dont la sélection est
        du vide et dont l'alternative est le clip d'origine (_DISABLE_CLIP_FLAG)."""
        length = component.length
        sel = self.f.create.Selector()
        sel.media_kind = "sound"
        sel.length = length
        sel["Selected"].value = self.filler(length)
        sel["Alternates"].append(component)
        attrs = sel["ComponentAttributeList"]
        attrs.append(self.f.create.TaggedValue("_DISABLE_CLIP_FLAG", 1, "aafInt32"))
        attrs.append(self.f.create.TaggedValue("_AAF_SELECTED", 0, "aafInt32"))
        return sel

    def gained(self, component):
        """Retourne `component` enveloppé dans un gain de clip à -inf (amplitude 0)."""
        gain_def, amp_def = self._gain_defs()
        og = self.f.create.OperationGroup(gain_def, component.length)
        og.media_kind = "sound"
        og.segments.append(component)
        cv = self.f.create.ConstantValue(amp_def, MUTE_AMPLITUDE)
        og.parameters.append(cv)
        return og

    # ---------- manipulations de base ----------
    def filler(self, length):
        return self.f.create.Filler("sound", length)

    def blank(self, clip):
        """Remplace le clip par du vide dans sa piste. Retourne l'objet retiré."""
        seq = clip.track.sequence
        comp = seq.components[clip.index]
        copy = comp.copy()
        seq.components[clip.index] = self.filler(comp.length)
        return copy

    def replace(self, clip, new_component):
        seq = clip.track.sequence
        seq.components[clip.index] = new_component

    def mono_component(self, clip, ch):
        """Fabrique un clip mono à partir du canal `ch` d'un clip combiner,
        en conservant les enveloppes (gain de clip...)."""
        inputs = list(clip.combiner.segments)
        seg = inputs[ch].copy()
        seg.length = clip.length
        for w in reversed(clip.wrappers):
            og = self.f.create.OperationGroup(w.operation, clip.length)
            og.media_kind = "sound"
            og.segments.append(seg)
            for p in w.parameters:
                og.parameters.append(p.copy())
            seg = og
        return seg

    def add_to_new_track(self, track, suffix, pos, component):
        self.new_tracks.setdefault((track.index, suffix), []).append((pos, component))

    # ---------- création / remplacement de pistes ----------
    def build_sequence(self, items):
        seq = self.f.create.Sequence("sound")
        seq["Components"].value = []
        cur = 0
        for pos, comp in sorted(items, key=lambda x: x[0]):
            if pos < cur:
                self.log("Warning: overlap ignored on a new track")
                continue
            if pos > cur:
                seq.components.append(self.filler(pos - cur))
            seq.components.append(comp)
            cur = pos + comp.length
        seq.length = cur
        return seq

    # ---------- attributs de piste (Avid / Resolve) ----------
    def tv(self, name, value):
        return self.f.create.TaggedValue(name, value, "aafInt32")

    def set_track_attrs(self, slot, src_slot, stereo_format=None, muted=False):
        """Attributs de piste façon Avid/Resolve : _TRACK_FORMAT absent = mono,
        2 = stéréo ; AudioMixerCompMute = mute de piste.
        - slot is src_slot : piste convertie en mono sur place, on retire juste le format ;
        - sinon : nouvelle piste, on recopie les attributs de la source."""
        if slot is src_slot:
            if "TimelineMobAttributeList" in slot.keys():
                lst = slot["TimelineMobAttributeList"]
                for i in reversed(range(len(lst))):
                    if lst[i].name == "_TRACK_FORMAT":
                        lst.pop(i)
            return
        target = slot["TimelineMobAttributeList"]
        if src_slot is not None and "TimelineMobAttributeList" in src_slot.keys():
            for tv in src_slot["TimelineMobAttributeList"].value or []:
                if tv.name not in ("_TRACK_FORMAT", "AudioMixerCompMute"):
                    target.append(tv.copy())
        if stereo_format and stereo_format >= 2:
            target.append(self.tv("_TRACK_FORMAT", int(stereo_format)))
        target.append(self.tv("AudioMixerCompMute", 1 if muted else 0))

    def make_slot(self, track, name, seq, slot_id, stereo_format=None, muted=False):
        slot = self.f.create.TimelineMobSlot(slot_id, edit_rate=track.slot.edit_rate)
        slot.name = name
        slot.segment = seq
        self.set_track_attrs(slot, track.slot, stereo_format, muted)
        return slot

    @staticmethod
    def track_format(slot):
        v = None
        if "TimelineMobAttributeList" in slot.keys():
            for tv in slot["TimelineMobAttributeList"].value or []:
                if tv.name == "_TRACK_FORMAT":
                    v = tv.value
        return v

    # ---------- nettoyage des séquences ----------
    def tidy(self, seq):
        """Fusionne les vides consécutifs et retire les fondus coincés entre deux vides
        (laissés par une suppression ou un déplacement de clip)."""
        changed = True
        while changed:
            changed = False
            comps = list(seq.components)
            for i in range(len(comps) - 1):
                a, b = comps[i], comps[i + 1]
                if isinstance(a, Filler) and isinstance(b, Filler):
                    seq.components[i] = self.filler(a.length + b.length)
                    seq.components.pop(i + 1)
                    changed = True
                    break
                if (i + 2 < len(comps) and isinstance(a, Filler) and
                        type(b).__name__ == "Transition" and isinstance(comps[i + 2], Filler)):
                    c = comps[i + 2]
                    seq.components[i] = self.filler(a.length + c.length - b.length)
                    seq.components.pop(i + 2)
                    seq.components.pop(i + 1)
                    changed = True
                    break
            comps = list(seq.components)
            # fondu en tout début / fin de piste sans voisin
            if comps and type(comps[0]).__name__ == "Transition":
                seq.components.pop(0)
                changed = True
            elif comps and type(comps[-1]).__name__ == "Transition":
                seq.components.pop(len(comps) - 1)
                changed = True

    def commit_tracks(self, mono_tracks):
        """mono_tracks : index des pistes converties en mono en place.
        self.new_tracks : nouvelles pistes à insérer juste après leur piste source."""
        mob = self.comp.mob
        slots = list(mob.slots)
        used_ids = {s.slot_id for s in slots}
        next_id = max(used_ids) + 1 if used_ids else 1

        for t in self.comp.tracks:
            self.tidy(t.sequence)
        orig_fmt = {t.index: self.track_format(t.slot) for t in self.comp.tracks}
        for tindex in mono_tracks:
            t = self.comp.tracks[tindex]
            self.set_track_attrs(t.slot, t.slot, None, False)

        order = []  # pistes son dans l'ordre d'affichage (PhysicalTrackNumber)
        new_ids = set()
        suffix_rank = {"MONO": 0, "MONO R MUTE": 1, "R MUTE": 2, "MUTE": 3}
        for t in sorted(self.comp.tracks, key=lambda t: (t.phys or 0, t.index)):
            order.append(("old", t))
            mine = [(s, items) for (ti, s), items in self.new_tracks.items() if ti == t.index]
            mine.sort(key=lambda x: suffix_rank.get(x[0], 9))
            src_fmt = orig_fmt[t.index]
            for suffix, items in mine:
                seq = self.build_sequence(items)
                name = f"{t.name} {suffix}"
                # "MUTE" reçoit les clips d'origine : même format que la source ;
                # les autres reçoivent des clips mono
                fmt = src_fmt if suffix == "MUTE" else None
                slot = self.make_slot(t, name, seq, next_id, fmt, muted="MUTE" in suffix)
                new_ids.add(next_id)
                next_id += 1
                pos_in_vec = list(mob.slots).index(t.slot) + 1
                # garder les nouvelles pistes groupées après la source
                while pos_in_vec < len(mob.slots) and mob.slots[pos_in_vec].slot_id in new_ids:
                    pos_in_vec += 1
                mob.slots.insert(pos_in_vec, slot)
                order.append(("new", slot))
                self.log(f"New track: {name}" + (" (muted)" if "MUTE" in suffix else ""))
        n = 1
        for kind, x in order:
            slot = x.slot if kind == "old" else x
            if "PhysicalTrackNumber" in slot.keys() or kind == "new":
                slot["PhysicalTrackNumber"].value = n
            n += 1


def encode_pcm(block, bits):
    """float32 (n, nch) -> octets PCM entrelacés little-endian."""
    if bits == 16:
        v = np.clip(np.round(block * 32768.0), -32768, 32767).astype("<i2")
        return v.tobytes()
    v = np.clip(np.round(block.astype(np.float64) * 8388608.0), -8388608, 8388607).astype("<i4")
    b = v.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes()


def embed_media(f, comp_mob, src_aaf_path, media_dirs, log, progress=None):
    """Copie dans l'AAF tous les fichiers son liés encore utilisés par la composition
    -> un AAF autonome."""
    comp = model.read_composition(f, comp_mob)
    ids = []
    for c in comp.all_clips():
        for ch in c.channels:
            if ch.mob_id not in ids:
                ids.append(ch.mob_id)
    pool = media.MediaPool(f, src_aaf_path, media_dirs, log)
    mobs = {str(m.mob_id): m for m in f.content.mobs}
    done, missing, total_bytes = 0, [], 0
    for i, mid in enumerate(ids):
        mob = mobs.get(mid)
        if mob is None or f.content.essencedata.get(mob.mob_id, None) is not None:
            continue  # déjà embarqué
        src = pool.source(mid)
        if src is None:
            missing.append(mob.name or mid)
            continue
        bits = 16
        try:
            sub = getattr(getattr(src, "f", None), "subtype", "") or ""
            bits = 16 if sub == "PCM_16" else 24
        except Exception:
            bits = 24
        if progress:
            progress(i / max(1, len(ids)), "Embedding " + (mob.name or ""))
        old = mob.descriptor
        old_rate = model.prop(old, "SampleRate", src.sr)
        d = f.create.PCMDescriptor()
        d["Channels"].value = src.nch
        d["BlockAlign"].value = src.nch * bits // 8
        d["SampleRate"].value = old_rate
        d["AverageBPS"].value = src.sr * src.nch * bits // 8
        d["QuantizationBits"].value = bits
        d["AudioSamplingRate"].value = src.sr
        d.length = src.frames
        try:
            d["ContainerFormat"].value = f.dictionary.lookup_containerdef("AAF")
        except Exception:
            pass
        mob.descriptor = d
        ess = f.create.EssenceData()
        ess.mob_id = mob.mob_id
        f.content.essencedata.append(ess)
        stream = ess.open("w")
        pos, step = 0, src.sr * 30
        while pos < src.frames:
            n = min(step, src.frames - pos)
            data = encode_pcm(src.read(pos, n), bits)
            stream.write(data)
            total_bytes += len(data)
            pos += n
        done += 1
    pool.close()
    if missing:
        log("Media not found, left as external links: " + ", ".join(missing[:20])
            + (" ..." if len(missing) > 20 else ""))
    log(f"{done} audio file(s) embedded in the AAF ({total_bytes / 1e6:.0f} MB)")
    if total_bytes > 2e9:
        log("Warning: the AAF is larger than 2 GB, some Pro Tools versions refuse embedded AAFs this large.")
    return done, missing


def export(src_path, dst_path, comp_index, results, plan, log=print, progress=None,
           embed=False, media_dirs=()):
    """Applique le plan sur une copie de l'AAF. results = analyse faite sur l'original
    (les uid sont identiques car la lecture est déterministe)."""
    if src_path == dst_path:
        raise ValueError("The output file must be different from the original.")
    shutil.copyfile(src_path, dst_path)
    stats = dict(deleted=0, muted=0, mono=0, new_tracks=0, skipped=0)
    with aaf2.open(dst_path, "rw") as f:
        mob = model.list_compositions(f)[comp_index]
        comp = model.read_composition(f, mob, normalize=True)
        clips = {c.uid: c for c in comp.all_clips()}
        ed = Editor(f, comp, log)

        def live(c):
            return clips.get(c.uid)

        removed = set()   # uid supprimés
        muted_uids = set()
        rebuilt = {}

        def do_mute(c):
            if plan.mute_method == "track":
                comp_copy = ed.blank(c)
                ed.add_to_new_track(c.track, "MUTE", c.position, comp_copy)
            else:
                comp_obj = c.track.sequence.components[c.index]
                cp = comp_obj.copy()
                ed.replace(c, ed.muted(cp, plan.mute_method))
            muted_uids.add(c.uid)
            stats["muted"] += 1

        # ----- 1. doublons et quasi-doublons
        for findings, action, uids in ((results.exact, plan.dup_action, plan.exact_uids),
                                       (results.near, plan.near_action, plan.near_uids)):
            if action == "none":
                continue
            for fd in findings:
                if fd.clip.uid not in uids:
                    continue
                c = live(fd.clip)
                if c is None:
                    continue
                if action == "delete":
                    ed.blank(c)
                    removed.add(c.uid)
                    stats["deleted"] += 1
                else:
                    do_mute(c)

        # ----- 2. double mono
        if plan.dm_action != "none":
            mute_r = plan.dm_action == "mono_mute"
            per_track = {}
            for fd in results.dualmono:
                if fd.clip.uid not in plan.dm_uids or not fd.convertible:
                    continue
                c = live(fd.clip)
                if c is None or c.uid in removed or c.uid in muted_uids:
                    continue
                if fd.clip.side == "L" and fd.clip.partner is not None:
                    # paire L/R sur deux pistes mono : on garde L, on traite R
                    r = live(fd.clip.partner)
                    if r is None or r.uid in removed:
                        continue
                    if mute_r:
                        if r.uid not in muted_uids:
                            do_mute(r)
                    else:
                        ed.blank(r)
                        removed.add(r.uid)
                        stats["deleted"] += 1
                    stats["mono"] += 1
                    continue
                if c.kind != "combiner":
                    stats["skipped"] += 1
                    continue
                per_track.setdefault(c.track.index, []).append(c)

            for tindex, conv in per_track.items():
                t = comp.tracks[tindex]
                conv_ids = {c.uid for c in conv}
                others = [c for c in t.clips if c.uid not in conv_ids and c.uid not in removed
                          and not (plan.mute_method == "track" and c.uid in muted_uids)]
                if not others:
                    # toute la piste est double mono : la piste devient mono
                    # (clips remplacés sur place : les fondus sont conservés)
                    for c in conv:
                        mono = ed.mono_component(c, 0)
                        if mute_r:
                            r = ed.mono_component(c, 1)
                            if plan.mute_method != "track":
                                r = ed.muted(r, plan.mute_method)
                            ed.add_to_new_track(t, "R MUTE", c.position, r)
                        ed.replace(c, mono)
                    rebuilt[tindex] = True
                    log(f"Track \"{t.name}\" converted to mono ({len(conv)} clips)")
                else:
                    # au moins un clip réellement stéréo : les clips double mono
                    # partent sur une nouvelle piste mono
                    for c in conv:
                        ed.add_to_new_track(t, "MONO", c.position, ed.mono_component(c, 0))
                        if mute_r:
                            r = ed.mono_component(c, 1)
                            if plan.mute_method != "track":
                                r = ed.muted(r, plan.mute_method)
                            ed.add_to_new_track(t, "MONO R MUTE", c.position, r)
                        ed.blank(c)
                    log(f"Track \"{t.name}\": {len(conv)} dual-mono clips moved to \"{t.name} MONO\" "
                        f"({len(others)} stereo clips kept)")
                stats["mono"] += len(conv)

        stats["new_tracks"] = len(ed.new_tracks)
        ed.commit_tracks(set(rebuilt))
        if embed:
            n, missing = embed_media(f, mob, src_path, media_dirs, log, progress)
            stats["embedded"] = n
            stats["missing_media"] = len(missing)
    return stats
