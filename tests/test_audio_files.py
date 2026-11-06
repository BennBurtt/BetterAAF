"""Linked audio files as exported or referenced by Avid, Premiere and Resolve:
MXF OP-Atom, camera MXF OP1a with several mono tracks, MOV (PCM 16/24/float, with video),
MP4/M4A (AAC, ALAC), MP3, FLAC, WAV/AIFF. Files are written with PyAV, read back through
the same code as the app, and compared sample by sample (lossy codecs: SNR)."""
import os
import random
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from betteraaf import media  # noqa: E402

SR = 48000
N = SR * 8
T = np.arange(N) / SR
CH = [(0.2 * np.sin(2 * np.pi * (300 + 170 * k) * T) * (0.6 + 0.4 * np.sin(2 * np.pi * (0.3 + 0.1 * k) * T)))
      .astype(np.float32) for k in range(4)]


def write_av(path, fmt, streams, video=False):
    """streams: list of (codec, [channel indexes])"""
    import av
    c = av.open(path, "w", format=fmt)
    vs = None
    if video:
        vs = c.add_stream("mpeg2video" if fmt == "mxf" else "libx264", rate=25)
        vs.width, vs.height, vs.pix_fmt = 320, 180, "yuv420p"
    outs = []
    for codec, chans in streams:
        st = c.add_stream(codec, rate=SR)
        st.layout = "mono" if len(chans) == 1 else "stereo"
        outs.append((st, chans))
    if vs is not None:
        for i in range(int(N / SR * 25)):
            fr = __import__("av").VideoFrame.from_ndarray(np.zeros((180, 320, 3), np.uint8), format="rgb24")
            fr.pts = i
            for p in vs.encode(fr):
                c.mux(p)
        for p in vs.encode():
            c.mux(p)
    for st, chans in outs:
        layout = "mono" if len(chans) == 1 else "stereo"
        rs = av.AudioResampler(format=st.codec_context.format.name, layout=layout, rate=SR)
        step = 1024
        for i in range(0, N, step):
            x = np.stack([CH[k][i:i + step] for k in chans]).astype(np.float32)
            fr = av.AudioFrame.from_ndarray(x, format="fltp", layout=layout)
            fr.sample_rate = SR
            fr.pts = i
            for f2 in rs.resample(fr):
                for p in st.encode(f2):
                    c.mux(p)
        for f2 in rs.resample(None):
            for p in st.encode(f2):
                c.mux(p)
        for p in st.encode():
            c.mux(p)
    c.close()


def main():
    import av
    d = tempfile.mkdtemp()
    cases = []

    def add(name, fn, chans, lossless=True):
        path = os.path.join(d, name)
        try:
            fn(path)
        except Exception as e:  # noqa
            print(f"SKIP {name}: cannot write test file here ({e})")
            return
        cases.append((name, path, chans, lossless))

    add("avid_opatom.mxf", lambda p: write_av(p, "mxf_opatom", [("pcm_s24le", [0])]), [0])
    add("camera_op1a_4mono.mxf", lambda p: write_av(p, "mxf", [("pcm_s24le", [k]) for k in range(4)], video=True),
        [0, 1, 2, 3])
    add("mov_pcm24.mov", lambda p: write_av(p, "mov", [("pcm_s24le", [0, 1])]), [0, 1])
    add("mov_pcm16be.mov", lambda p: write_av(p, "mov", [("pcm_s16be", [0, 1])]), [0, 1])
    add("mov_float.mov", lambda p: write_av(p, "mov", [("pcm_f32le", [0, 1])]), [0, 1])
    add("mov_video_2mono.mov", lambda p: write_av(p, "mov", [("pcm_s24le", [0]), ("pcm_s24le", [1])], video=True),
        [0, 1])
    add("mp4_aac.mp4", lambda p: write_av(p, "mp4", [("aac", [0, 1])]), [0, 1], lossless=False)
    add("m4a_alac.m4a", lambda p: write_av(p, "ipod", [("alac", [0, 1])]), [0, 1])
    add("mp3.mp3", lambda p: write_av(p, "mp3", [("libmp3lame", [0, 1])]), [0, 1], lossless=False)
    add("flac.flac", lambda p: sf.write(p, np.stack(CH[:2], 1), SR), [0, 1])
    add("wav24.wav", lambda p: sf.write(p, np.stack(CH[:2], 1), SR, subtype="PCM_24"), [0, 1])
    add("aiff24.aif", lambda p: sf.write(p, np.stack(CH[:2], 1), SR, subtype="PCM_24", format="AIFF"), [0, 1])

    random.seed(0)
    bad = 0
    for name, path, chans, lossless in cases:
        pool = media.MediaPool(None, path, log=lambda *a: None)
        try:
            src = media.SoundFileSource(path)
        except Exception:
            src = media.AVSource(path)
        ok, worst = src.nch == len(chans), None
        for _ in range(10):
            st = random.randrange(0, N - SR)
            n = random.choice([1024, 4096, 30000])
            y = src.read(st, n)
            for ci, k in enumerate(chans):
                ref = CH[k][st:st + n]
                if lossless:
                    e = float(np.max(np.abs(y[:, ci] - ref)))
                    worst = e if worst is None else max(worst, e)
                    ok &= e < 1e-3
                else:
                    snr = 10 * np.log10(np.sum(ref ** 2) / max(1e-12, np.sum((y[:, ci] - ref) ** 2)))
                    worst = snr if worst is None else min(worst, snr)
                    ok &= snr > 20
        bad += not ok
        print(f"{'OK ' if ok else 'ERR'} {name:24} {src.nch} ch  " +
              (f"max error {worst:.6f}" if lossless else f"min SNR {worst:.1f} dB") +
              f"  [{getattr(src, 'kind', 'soundfile')}]")
    print("AUDIO FILES", "OK" if not bad else f"{bad} FAILED")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
