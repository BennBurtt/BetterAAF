"""Médias embarqués : fichiers WAV/WAVEX/AIFF/AIFC complets dans le flux (cas Pro Tools),
PCM 16/24 bits et float 32 bits. Échoue si un format est mal lu."""
import io
import os
import sys
import tempfile

import aaf2
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from betteraaf import model, media, analysis  # noqa: E402

SR = 48000
X = (0.25 * np.sin(2 * np.pi * 440 * np.arange(SR) / SR)).astype(np.float32)
CASES = [("WAV", "PCM_24", "wave"), ("WAV", "PCM_16", "wave"), ("WAV", "FLOAT", "wave"),
         ("WAVEX", "FLOAT", "wave"), ("WAVEX", "PCM_24", "wave"), ("RF64", "PCM_24", "wave"),
         ("AIFF", "PCM_24", "aifc"), ("AIFF", "PCM_16", "aifc"), ("AIFF", "FLOAT", "aifc")]


def build(path, data, desc_kind):
    with aaf2.open(path, "w") as f:
        sm = f.create.SourceMob("m")
        f.content.mobs.append(sm)
        d = f.create.WAVEDescriptor() if desc_kind == "wave" else f.create.AIFCDescriptor()
        d["Summary"].value = data[:64]
        d["SampleRate"].value = SR
        d["Length"].value = len(X)
        sm.descriptor = d
        sl = sm.create_empty_slot(SR, media_kind="sound")
        sl.segment.length = len(X)
        e = f.create.EssenceData()
        e.mob_id = sm.mob_id
        f.content.essencedata.append(e)
        e.open("w").write(data)
        mm = f.create.MasterMob("m")
        f.content.mobs.append(mm)
        ms = mm.create_timeline_slot(SR)
        ms.segment = sm.create_source_clip(sl.slot_id, 0, len(X))
        c = f.create.CompositionMob("C")
        f.content.mobs.append(c)
        s2 = c.create_sound_slot(SR)
        s2.segment.components.append(mm.create_source_clip(1, 0, len(X)))
        s2.segment.length = len(X)


def main():
    bad = 0
    tmp = tempfile.mkdtemp()
    for fmt, sub, kind in CASES:
        b = io.BytesIO()
        sf.write(b, X, SR, subtype=sub, format=fmt)
        p = os.path.join(tmp, f"{fmt}_{sub}.aaf")
        build(p, b.getvalue(), kind)
        with aaf2.open(p) as f:
            comp = model.read_composition(f, model.list_compositions(f)[0])
            pool = media.MediaPool(f, p, log=lambda *a: None)
            c = analysis.prepare_clips(comp, pool, lambda *a: None)[0]
            y = pool.read(c.channels[0], 0, len(X))
            err = float(np.max(np.abs(y - X)))
            ok = err < 1e-3
            bad += not ok
            print(f"{'OK ' if ok else 'ERR'} {fmt:6} {sub:7} error {err:.6f}  {pool.summary()}")
    print("MEDIA FORMATS", "OK" if not bad else f"{bad} FAILED")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
