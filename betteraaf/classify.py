"""Content detection (dialogue / music / FX / ...) with Google's YAMNet model.

YAMNet (AudioSet, 521 classes) runs here in pure numpy: same log-mel features and
same MobileNet network as the official TensorFlow code, using weights exported by
tools/export_yamnet.py (checked against TensorFlow in CI).

The 521 AudioSet classes are folded into a few post-production groups using the
AudioSet ontology.
"""

import csv
import json
import math
import os
import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

SR = 16000
WIN, HOP, NFFT = 400, 160, 512
PATCH_FRAMES, PATCH_HOP, MEL_BANDS = 96, 48, 64
PATCH_SAMPLES = WIN + (PATCH_FRAMES - 1) * HOP          # 15600 samples = 0.975 s
MAX_PATCHES_PER_CLIP = 8
SILENCE_RMS = 10 ** (-70 / 20)

# fine groups -> label, and which optional category they belong to
FINE = ["speech", "vocal", "singing", "music", "walla", "foley", "animals", "vehicles",
        "impacts", "fx", "ambience", "background"]

BASE_OF = {   # where a fine group goes when its optional category is off
    "speech": "Dialogue", "vocal": "Dialogue", "singing": "Music", "music": "Music",
    "walla": "Others", "foley": "FX", "animals": "FX", "vehicles": "FX", "impacts": "FX",
    "fx": "FX", "ambience": "Others", "background": "Others",
}

# optional categories: label -> fine groups it takes over
OPTIONAL = {
    "Singing": ["singing"],
    "Vocal sounds": ["vocal"],
    "Walla": ["walla"],
    "Foley": ["foley"],
    "Vehicles": ["vehicles"],
    "Animals": ["animals"],
    "Impacts": ["impacts"],
    "Ambience": ["ambience", "background"],
}
OPTIONAL_HELP = {
    "Singing": "Singing, choir, humming (otherwise Music)",
    "Vocal sounds": "Laughs, cries, breaths, efforts (otherwise Dialogue)",
    "Walla": "Crowds, chatter, applause (otherwise Others)",
    "Foley": "Footsteps, hands, doors, objects (otherwise FX)",
    "Vehicles": "Cars, engines, planes, trains (otherwise FX)",
    "Animals": "Animals and birds (otherwise FX)",
    "Impacts": "Hits, crashes, gunshots, explosions (otherwise FX)",
    "Ambience": "Wind, rain, water, room tone, city (otherwise Others)",
}
BASE_CATEGORIES = ["Dialogue", "Music", "FX", "Others"]


def resource_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "betteraaf", "models")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def available():
    return os.path.exists(os.path.join(resource_dir(), "yamnet.npz"))


def category_of(fine, enabled):
    """fine group -> displayed category, given the enabled optional categories."""
    for label in enabled:
        if fine in OPTIONAL.get(label, ()):
            return label
    return BASE_OF.get(fine, "Others")


def summarize(votes, enabled):
    """votes {fine: weight} -> {category: share} sorted, share in 0..1."""
    tot = sum(votes.values())
    out = {}
    if tot <= 0:
        return {}
    for k, v in votes.items():
        c = category_of(k, enabled)
        out[c] = out.get(c, 0.0) + v / tot
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------- features
def hertz_to_mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def mel_matrix(num_mel=MEL_BANDS, nbins=NFFT // 2 + 1, sr=SR, lo=125.0, hi=7500.0):
    """Same as tf.signal.linear_to_mel_weight_matrix."""
    lin = np.linspace(0.0, sr / 2.0, nbins)[1:]
    bins_mel = hertz_to_mel(lin)[:, None]
    edges = np.linspace(hertz_to_mel(lo), hertz_to_mel(hi), num_mel + 2)
    lower, center, upper = edges[:-2], edges[1:-1], edges[2:]
    lower_s = (bins_mel - lower) / (center - lower)
    upper_s = (upper - bins_mel) / (upper - center)
    m = np.maximum(0.0, np.minimum(lower_s, upper_s))
    return np.pad(m, [[1, 0], [0, 0]]).astype(np.float64)


_MEL = mel_matrix()
_HANN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN) / WIN))


def pad_waveform(w):
    """Same padding as YAMNet's features.pad_waveform."""
    min_n = int((0.96 + 0.025 - 0.010) * SR)
    n = len(w)
    pad = max(0, min_n - n)
    n = max(n, min_n)
    after = n - min_n
    hop = int(0.48 * SR)
    hops = int(math.ceil(after / hop))
    pad += hop * hops - after
    return np.pad(w, (0, pad))


def log_mel_patches(w16):
    """16 kHz waveform -> (num_patches, 96, 64) log-mel patches."""
    w16 = pad_waveform(np.asarray(w16, dtype=np.float64))
    frames = sliding_window_view(w16, WIN)[::HOP]
    spec = np.abs(np.fft.rfft(frames * _HANN, NFFT))
    logmel = np.log(spec @ _MEL + 0.001)
    patches = sliding_window_view(logmel, PATCH_FRAMES, axis=0)[::PATCH_HOP]
    return np.transpose(patches, (0, 2, 1)).astype(np.float32)


def resample_to_16k(x, sr):
    x = np.asarray(x, dtype=np.float64)
    if sr == SR:
        return x
    # low-pass (windowed sinc) then decimate / interpolate
    cutoff = 0.95 * (SR / 2) / sr
    n = 101
    t = np.arange(n) - (n - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * t) * np.blackman(n)
    h /= h.sum()
    y = np.convolve(x, h, mode="same")
    if sr % SR == 0:
        return y[::sr // SR]
    pos = np.arange(0, len(y) - 1, sr / SR)
    return np.interp(pos, np.arange(len(y)), y)


# ---------------------------------------------------------------- network
def _same_pad(n, k, s):
    out = -(-n // s)
    total = max((out - 1) * s + k - n, 0)
    return out, total // 2, total - total // 2


class Net:
    def __init__(self, path):
        z = np.load(path, allow_pickle=False)
        self.spec = json.loads(str(z["spec"]))
        self.w = {k: z[k] for k in z.files if k != "spec"}

    def __call__(self, x):
        x = x[..., None].astype(np.float32)                    # (B, 96, 64, 1)
        for i, L in enumerate(self.spec):
            t = L["type"]
            g = lambda name: self.w[f"{i}:{name}"]  # noqa
            if t in ("Conv2D", "DepthwiseConv2D"):
                k = g("depthwise_kernel" if t == "DepthwiseConv2D" else "kernel")
                s = L["strides"][0]
                kh, kw = k.shape[0], k.shape[1]
                B, H, W, C = x.shape
                Ho, pt, pb = _same_pad(H, kh, s)
                Wo, pl, pr = _same_pad(W, kw, s)
                xp = np.pad(x, ((0, 0), (pt, pb), (pl, pr), (0, 0)))
                if t == "DepthwiseConv2D":
                    out = np.zeros((B, Ho, Wo, C), dtype=np.float32)
                    for a in range(kh):
                        for b in range(kw):
                            out += xp[:, a:a + Ho * s:s, b:b + Wo * s:s, :] * k[a, b, :, 0]
                    x = out
                elif kh == 1 and kw == 1 and s == 1:
                    x = (xp.reshape(-1, C) @ k[0, 0]).reshape(B, Ho, Wo, -1)
                else:
                    cols = [xp[:, a:a + Ho * s:s, b:b + Wo * s:s, :] for a in range(kh) for b in range(kw)]
                    cols = np.concatenate(cols, axis=-1)                      # (B,Ho,Wo,kh*kw*C)
                    x = (cols.reshape(-1, kh * kw * C) @ k.reshape(kh * kw * C, -1)).reshape(B, Ho, Wo, -1)
            elif t == "BatchNormalization":
                mean, var = g("moving_mean"), g("moving_variance")
                inv = 1.0 / np.sqrt(var + L["epsilon"])
                if f"{i}:gamma" in self.w:
                    inv = inv * g("gamma")
                x = (x - mean) * inv
                if f"{i}:beta" in self.w:
                    x = x + g("beta")
            elif t == "ReLU":
                x = np.maximum(x, 0)
            elif t == "GlobalAveragePooling2D":
                x = x.mean(axis=(1, 2))
            elif t == "Dense":
                x = x @ g("kernel") + g("bias")
            elif t == "Activation":
                if L.get("activation") == "sigmoid":
                    x = 1.0 / (1.0 + np.exp(-x))
        return x


# ---------------------------------------------------------------- class groups
def build_groups(class_map_csv, ontology_json):
    with open(class_map_csv, newline="") as fh:
        rows = list(csv.reader(fh))[1:]
    mids = [r[1] for r in rows]
    names = [r[2] for r in rows]
    with open(ontology_json) as fh:
        onto = json.load(fh)
    by_id = {o["id"]: o for o in onto}
    parents = {}
    for o in onto:
        for c in o.get("child_ids", []):
            parents.setdefault(c, []).append(o["id"])

    def ancestors(mid):
        seen, stack = set(), [mid]
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            stack.extend(parents.get(m, []))
        return {by_id[m]["name"] for m in seen if m in by_id}

    rules = [
        ("silence", {"Silence"}),
        ("singing", {"Singing", "Humming", "Choir", "Yodeling", "Chant", "Rapping"}),
        ("speech", {"Speech", "Shout", "Screaming", "Whispering"}),
        ("walla", {"Human group actions"}),
        ("vocal", {"Human voice", "Respiratory sounds"}),
        ("foley", {"Human locomotion", "Hands", "Digestive", "Heart sounds, heartbeat",
                   "Domestic sounds, home sounds"}),
        ("music", {"Music"}),
        ("animals", {"Animal"}),
        ("vehicles", {"Vehicle", "Engine"}),
        ("impacts", {"Explosion", "Generic impact sounds", "Glass", "Wood", "Breaking"}),
        ("ambience", {"Natural sounds"}),
        ("background", {"Channel, environment and background"}),
    ]
    group = []
    for mid in mids:
        anc = ancestors(mid)
        g = "fx"
        for name, keys in rules:
            if anc & keys:
                g = name
                break
        group.append(g)
    return names, group


class Classifier:
    def __init__(self):
        d = resource_dir()
        self.net = Net(os.path.join(d, "yamnet.npz"))
        self.names, self.group = build_groups(os.path.join(d, "yamnet_class_map.csv"),
                                              os.path.join(d, "ontology.json"))
        self.fine_index = {g: [i for i, x in enumerate(self.group) if x == g] for g in FINE}

    def scores(self, patches):
        out = []
        for i in range(0, len(patches), 64):
            out.append(self.net(patches[i:i + 64]))
        return np.concatenate(out) if out else np.zeros((0, len(self.names)), np.float32)

    def votes(self, scores):
        """per-patch scores -> {fine group: weight} (argmax group per patch)."""
        votes = {}
        for s in scores:
            best, bw = None, 0.0
            for g, idx in self.fine_index.items():
                if idx:
                    v = float(s[idx].max())
                    if v > bw:
                        best, bw = g, v
            if best:
                votes[best] = votes.get(best, 0.0) + bw
        return votes

    def clip_votes(self, read, n_samples, sr):
        """read(offset, n) -> mono float array at `sr`. Samples up to
        MAX_PATCHES_PER_CLIP windows across the clip."""
        win = int(math.ceil(PATCH_SAMPLES * sr / SR)) + 64
        if n_samples <= win:
            starts = [0]
        else:
            k = min(MAX_PATCHES_PER_CLIP, max(1, n_samples // win))
            starts = np.linspace(0, n_samples - win, k).astype(int).tolist()
        patches = []
        for st in starts:
            x = read(st, min(win, n_samples - st))
            if len(x) == 0 or np.sqrt(np.mean(np.square(x))) < SILENCE_RMS:
                continue
            y = resample_to_16k(x, sr)[:PATCH_SAMPLES]
            patches.append(log_mel_patches(y)[0])
        if not patches:
            return {}
        return self.votes(self.scores(np.stack(patches)))


def classify_composition(comp_clips, pool, progress=None, stop=None, log=print):
    """Fills clip.votes for each analyzable clip. Returns True if the model ran."""
    if not available():
        log("Content detection unavailable (model missing).")
        return False
    clf = Classifier()
    clips = [c for c in comp_clips if not c.error and c.channels and c.sr]
    for i, c in enumerate(clips):
        if stop and stop():
            raise InterruptedError
        if progress:
            progress(i / max(1, len(clips)), "Listening: " + c.name)
        ch = c.channels[0]

        def read(off, n, ch=ch):
            return pool.read(ch, off, n)
        try:
            c.votes = clf.clip_votes(read, c.s1 - c.s0, c.sr)
        except Exception as e:  # noqa
            c.votes = {}
            log(f"{c.label()}: content detection failed ({e})")
    return True


def track_categories(tracks, enabled):
    """-> list of (track, {category: share}) using duration-weighted clip votes."""
    out = []
    for t in tracks:
        agg = {}
        for c in t.clips:
            v = getattr(c, "votes", None) or {}
            tot = sum(v.values())
            if tot <= 0:
                continue
            dur = max(1, c.s1 - c.s0)
            for k, w in v.items():
                agg[k] = agg.get(k, 0.0) + dur * w / tot
        out.append((t, summarize(agg, enabled)))
    return out


def proposed_names(track_cats):
    """Track names: 'Dialogue', or 'Dialogue 1', 'Dialogue 2'... when several."""
    main = [next(iter(cats), None) for _t, cats in track_cats]
    counts = {}
    for m in main:
        if m:
            counts[m] = counts.get(m, 0) + 1
    seen, names = {}, []
    for (t, cats), m in zip(track_cats, main):
        if not m:
            names.append(t.name)
            continue
        seen[m] = seen.get(m, 0) + 1
        names.append(m if counts[m] == 1 else f"{m} {seen[m]}")
    return names
