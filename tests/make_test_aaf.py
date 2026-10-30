"""Fabrique un AAF de test couvrant tous les cas de BetterAAF."""
import os
import sys
import uuid

import numpy as np
import soundfile as sf
import aaf2

SR = 48000
out = sys.argv[1] if len(sys.argv) > 1 else "test"
os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(1)


def tone(sec, f0, seed):
    r = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * f0 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t))
            + 0.05 * r.standard_normal(len(t))).astype(np.float32)


def wav(name, data):
    p = os.path.abspath(os.path.join(out, name))
    sf.write(p, data, SR, subtype="PCM_24")
    return p


dial = tone(10, 220, 1)
files = {
    "dial": wav("dial.wav", dial),
    "dial_copy": wav("dial_copy.wav", dial),
    "dial_near": wav("dial_near.wav", dial + (10 ** (-85 / 20) * rng.standard_normal(len(dial))).astype(np.float32)),
    "other": wav("other.wav", tone(10, 330, 2)),
    "dm": wav("dm.wav", tone(10, 440, 3)),
    "stL": wav("st_L.wav", tone(10, 500, 4)),
    "stR": wav("st_R.wav", tone(10, 600, 5)),
    "fx": wav("fx.wav", tone(10, 700, 6)),
    "mus": wav("mus.wav", tone(10, 800, 7)),
    "linked": wav("linked_stereo_dm.wav", np.stack([tone(10, 900, 8)] * 2, axis=1)),
}

aaf_path = os.path.join(out, "test.aaf")
with aaf2.open(aaf_path, "w") as f:
    masters = {}
    for k in ("dial", "dial_copy", "dial_near", "other", "dm", "stL", "stR", "fx", "mus"):
        m = f.create.MasterMob(k)
        f.content.mobs.append(m)
        m.import_audio_essence(files[k], SR)   # média embarqué
        masters[k] = m

    # média lié (externe) stéréo : un MasterMob à 2 slots -> 1 fichier 2 canaux
    sm = f.create.SourceMob("linked_stereo_dm")
    desc = f.create.WAVEDescriptor()
    import struct
    fmt = struct.pack("<4sI4s4sIHHIIHH", b"RIFF", 0, b"WAVE", b"fmt ", 16, 1, 2, SR, SR * 6, 6, 24)
    desc["Summary"].value = fmt
    desc["SampleRate"].value = SR
    desc["Length"].value = 10 * SR
    loc = f.create.NetworkLocator()
    loc["URLString"].value = "file:///Volumes/Ailleurs/Audio%20Files/linked_stereo_dm.wav"  # chemin cassé -> relink
    desc["Locator"].append(loc)
    sm.descriptor = desc
    s = sm.create_empty_slot(SR, media_kind="sound")
    s.segment.length = 10 * SR
    f.content.mobs.append(sm)
    lm = f.create.MasterMob("linked")
    f.content.mobs.append(lm)
    for ch in (1, 2):
        ms = lm.create_timeline_slot(SR)
        ms.segment = sm.create_source_clip(s.slot_id, 0, 10 * SR)
        ms["PhysicalTrackNumber"].value = ch

    comb = f.create.OperationDef(str(uuid.uuid4()), "Audio Channel Combiner", "")
    comb.media_kind = "sound"
    f.dictionary.register_def(comb)
    comb["NumberInputs"].value = -1

    comp = f.create.CompositionMob("TEST_BOBINE")
    f.content.mobs.append(comp)
    tc = comp.create_timeline_slot(25)
    tc.segment = f.create.Timecode(25, length=10 * 25)
    tc.segment.start = 10 * 3600 * 25

    def sc(key, start, length, slot=1):
        m = masters[key] if isinstance(key, str) else key
        return m.create_source_clip(slot, start, length)

    def stereo(kl, kr, start, length, sl=1, sr_=1):
        og = f.create.OperationGroup(comb, length)
        og.media_kind = "sound"
        og.segments.append(sc(kl, start, length, sl))
        og.segments.append(sc(kr, start, length, sr_))
        return og

    def track(name, items, phys):
        slot = comp.create_sound_slot(SR)
        slot.name = name
        slot["PhysicalTrackNumber"].value = phys
        if " ST" in name:   # piste stéréo, étiquetée comme le font Avid / Resolve
            slot["TimelineMobAttributeList"].append(f.create.TaggedValue("_TRACK_FORMAT", 2, "aafInt32"))
        seq = slot.segment
        cur = 0
        for pos, c in items:
            if pos > cur:
                seq.components.append(f.create.Filler("sound", pos - cur))
            seq.components.append(c)
            cur = pos + c.length
        seq.length = cur

    S = SR
    track("DIAL", [(0, sc("dial", 0, 5 * S))], 1)
    track("DIAL 2", [(0, sc("dial", 0, 5 * S)), (6 * S, sc("other", 0, 3 * S))], 2)
    track("DIAL 3", [(0, sc("dial_copy", 0, 5 * S)), (6 * S, sc("dial_near", 0, 2 * S))], 3)
    track("DIAL 4", [(S, sc("dial_near", S, 2 * S)), (6 * S, sc("dial", 0, 2 * S))], 4)
    track("AMB ST", [(0, stereo("dm", "dm", 0, 4 * S)), (5 * S, stereo("stL", "stR", 0, 4 * S))], 5)
    track("FX ST", [(0, stereo("fx", "fx", 0, 3 * S)), (4 * S, stereo(lm, lm, 0, 3 * S, 1, 2))], 6)
    track("MUS.L", [(0, sc("mus", 0, 4 * S)), (5 * S, sc("stL", 0, 2 * S))], 7)
    track("MUS.R", [(0, sc("mus", 0, 4 * S)), (5 * S, sc("stR", 0, 2 * S))], 8)

for k in list(files):
    if k != "linked":
        os.remove(files[k])          # ces médias sont embarqués
os.makedirs(os.path.join(out, "Medias"), exist_ok=True)
os.replace(files["linked"], os.path.join(out, "Medias", "linked_stereo_dm.wav"))
print(aaf_path)
