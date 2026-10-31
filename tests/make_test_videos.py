"""Génère des vidéos de test dont chaque image porte son numéro codé en blocs noirs/blancs
(16 bits, ligne du haut), dans tous les formats visés. Usage : make_test_videos.py dossier"""
import os
import subprocess
import sys

import numpy as np

W, H = 1280, 720
BITS = 16
BLOCK = W // BITS


def frame(i):
    x = np.zeros((H, W, 3), dtype=np.uint8)
    # contenu : dégradé qui bouge (pour que les codecs inter aient du travail)
    xx = np.arange(W)[None, :]
    yy = np.arange(H)[:, None]
    x[..., 0] = ((xx + i * 7) % 256)
    x[..., 1] = ((yy * 2 + i * 3) % 256)
    x[..., 2] = ((xx + yy + i * 11) % 256)
    for b in range(BITS):
        v = 255 if (i >> b) & 1 else 0
        x[0:BLOCK, b * BLOCK:(b + 1) * BLOCK] = v
    return x


def read_code(rgb):
    """rgb : image décodée (n'importe quelle taille) -> numéro d'image."""
    h, w = rgb.shape[:2]
    bw = w / BITS
    cy = int(BLOCK / 2 * h / H)
    n = 0
    for b in range(BITS):
        cx = int((b + 0.5) * bw)
        if rgb[cy, cx].mean() > 127:
            n |= 1 << b
    return n


FORMATS = [
    # nom, extension, fps, args encodeur
    ("prores_proxy", "mov", "25", ["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le"]),
    ("prores_422hq", "mov", "24000/1001", ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]),
    ("prores_4444", "mov", "30000/1001", ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"]),
    ("dnxhr_lb", "mov", "25", ["-c:v", "dnxhd", "-profile:v", "dnxhr_lb", "-pix_fmt", "yuv422p"]),
    ("dnxhr_hqx", "mxf", "50", ["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx", "-pix_fmt", "yuv422p10le"]),
    ("h264_gop50", "mp4", "25", ["-c:v", "libx264", "-g", "50", "-bf", "2", "-pix_fmt", "yuv420p", "-crf", "23"]),
    ("h264_2997", "mov", "30000/1001", ["-c:v", "libx264", "-g", "60", "-bf", "3", "-pix_fmt", "yuv420p", "-crf", "23"]),
    ("hevc_gop48", "mp4", "24", ["-c:v", "libx265", "-x265-params", "keyint=48:log-level=error", "-tag:v", "hvc1",
                                 "-pix_fmt", "yuv420p10le", "-crf", "28"]),
    ("hevc_25", "mov", "25", ["-c:v", "libx265", "-x265-params", "keyint=50:log-level=error", "-tag:v", "hvc1",
                              "-pix_fmt", "yuv420p", "-crf", "28"]),
]


PYAV_FORMATS = [
    # nom, extension, fps, codec, pix_fmt, options, codec_tag
    ("prores_proxy", "mov", "25", "prores_ks", "yuv422p10le", {"profile": "0"}, None),
    ("prores_422hq", "mov", "24000/1001", "prores_ks", "yuv422p10le", {"profile": "3"}, None),
    ("prores_4444", "mov", "30000/1001", "prores_ks", "yuva444p10le", {"profile": "4"}, None),
    ("dnxhr_lb", "mov", "25", "dnxhd", "yuv422p", {"profile": "dnxhr_lb"}, None),
    ("dnxhr_hqx", "mxf", "50", "dnxhd", "yuv422p10le", {"profile": "dnxhr_hqx"}, None),
    ("h264_gop50", "mp4", "25", "libx264", "yuv420p", {"g": "50", "bf": "2", "crf": "23"}, None),
    ("h264_2997", "mov", "30000/1001", "libx264", "yuv420p", {"g": "60", "bf": "3", "crf": "23"}, None),
    ("hevc_gop48", "mp4", "24", "libx265", "yuv420p10le", {"x265-params": "keyint=48:log-level=error", "crf": "28"}, "hvc1"),
    ("hevc_25", "mov", "25", "libx265", "yuv420p", {"x265-params": "keyint=50:log-level=error", "crf": "28"}, "hvc1"),
]


def make_pyav(out_dir, seconds=3):
    """Same test videos, encoded with PyAV (no ffmpeg command line needed)."""
    import av
    from fractions import Fraction
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for name, ext, fps, codec, pix, opts, tag in PYAV_FORMATS:
        rate = Fraction(fps)
        n = int(round(seconds * rate))
        path = os.path.join(out_dir, f"{name}.{ext}")
        c = av.open(path, "w")
        st = c.add_stream(codec, rate=rate)
        st.width, st.height, st.pix_fmt = W, H, pix
        st.options = dict(opts)
        if tag:
            st.codec_tag = tag
        for i in range(n):
            fr = av.VideoFrame.from_ndarray(frame(i), format="rgb24")
            fr.pts = i
            fr.time_base = 1 / rate
            for pkt in st.encode(fr):
                c.mux(pkt)
        for pkt in st.encode():
            c.mux(pkt)
        c.close()
        out.append((name, path, float(rate), n))
        print(name, path, n, "frames")
    return out


def make(out_dir, seconds=8):
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for name, ext, fps, args in FORMATS:
        num, den = (int(x) for x in (fps.split("/") + ["1"])[:2])
        n = int(round(seconds * num / den))
        path = os.path.join(out_dir, f"{name}.{ext}")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", fps, "-i", "-"] + args + [path]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for i in range(n):
            p.stdin.write(frame(i).tobytes())
        p.stdin.close()
        p.wait()
        out.append((name, path, num / den, n))
        print(name, path, n, "frames")
    return out


if __name__ == "__main__":
    if "--pyav" in sys.argv:
        make_pyav([a for a in sys.argv[1:] if a != "--pyav"][0])
    else:
        make(sys.argv[1] if len(sys.argv) > 1 else "testvideos")
