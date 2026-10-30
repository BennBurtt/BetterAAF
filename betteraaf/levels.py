"""Levels of the whole AAF played together (dBFS and LUFS, ITU-R BS.1770-4 / EBU R128).

The mix is rendered block by block in stereo:
  - muted clips and muted tracks are skipped,
  - constant clip gain is applied, fades/crossfades are applied as linear ramps,
  - stereo clips go L/R, L/R track pairs go to their side, mono clips are centred (-3 dB pan law).
Track volume/pan automation is not taken into account.
"""

import math
from collections import Counter

import numpy as np
from scipy.signal import lfilter, lfilter_zi, resample_poly

from . import analysis

PAN_MONO = 10 ** (-3 / 20)
ABS_GATE = -70.0


def k_filter(sr):
    """K-weighting biquads (same formulas as libebur128)."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return (np.array(b1), np.array(a1)), (np.array(b2), np.array(a2))


def lufs(ms):
    return -0.691 + 10 * math.log10(ms) if ms > 0 else -math.inf


def db(x):
    return 10 * math.log10(x) if x > 0 else -math.inf


def envelope(c, a, b, track_sr_per_eu):
    """Gain ramp for samples [a, b) relative to the clip start."""
    n = b - a
    env = np.full(n, c.gain, dtype=np.float32)
    total = c.s1 - c.s0
    fi = int(round(c.fade_in * track_sr_per_eu))
    fo = int(round(c.fade_out * track_sr_per_eu))
    idx = np.arange(a, b)
    if fi > 0:
        m = idx < fi
        env[m] *= (idx[m] + 0.5) / fi
    if fo > 0:
        m = idx >= total - fo
        env[m] *= (total - idx[m] - 0.5) / fo
    return env


def measure(comp, pool, progress=None, stop=None, log=print):
    clips = [c for c in comp.all_clips()
             if not c.error and c.channels and c.sr and not c.muted and not analysis.is_muted(c)
             and not c.track.muted]
    if not clips:
        return None
    sr = Counter(c.sr for c in clips).most_common(1)[0][0]
    skipped = [c for c in clips if c.sr != sr]
    if skipped:
        log(f"Levels: {len(skipped)} clip(s) at another sample rate were left out.")
    clips = sorted((c for c in clips if c.sr == sr), key=lambda c: c.s0)
    start, end = min(c.s0 for c in clips), max(c.s1 for c in clips)

    hop = sr // 10                      # 100 ms sub-blocks
    block = hop * 100                   # 10 s render blocks
    (b1, a1), (b2, a2) = k_filter(sr)
    zi1 = [lfilter_zi(b1, a1) * 0.0 for _ in range(2)]
    zi2 = [lfilter_zi(b2, a2) * 0.0 for _ in range(2)]
    z_sub, u_sub = [], []               # K-weighted / plain mean square per 100 ms
    peak = tpeak = 0.0
    total_blocks = max(1, math.ceil((end - start) / block))
    first = 0
    for bi, b0 in enumerate(range(start, end, block)):
        if stop and stop():
            raise InterruptedError
        if progress:
            progress(bi / total_blocks, "Measuring levels…")
        b1_ = min(b0 + block, end)
        n = b1_ - b0
        mix = np.zeros((n, 2), dtype=np.float32)
        while first < len(clips) and clips[first].s1 <= b0:
            first += 1
        for c in clips[first:]:
            if c.s0 >= b1_:
                break
            if c.s1 <= b0:
                continue
            a = max(b0, c.s0)
            e = min(b1_, c.s1)
            env = envelope(c, a - c.s0, e - c.s0, sr / c.track.edit_rate)
            dst = slice(a - b0, e - b0)
            try:
                if c.nch == 1:
                    x = pool.read(c.channels[0], a - c.s0, e - a) * env
                    if c.side == "L":
                        mix[dst, 0] += x
                    elif c.side == "R":
                        mix[dst, 1] += x
                    else:
                        mix[dst, 0] += x * PAN_MONO
                        mix[dst, 1] += x * PAN_MONO
                else:
                    mix[dst, 0] += pool.read(c.channels[0], a - c.s0, e - a) * env
                    mix[dst, 1] += pool.read(c.channels[1], a - c.s0, e - a) * env
            except Exception:  # noqa
                continue
        peak = max(peak, float(np.max(np.abs(mix))) if n else 0.0)
        for ch in range(2):
            up = resample_poly(mix[:, ch], 4, 1)
            tpeak = max(tpeak, float(np.max(np.abs(up))) if len(up) else 0.0)
        kw = np.empty_like(mix, dtype=np.float64)
        for ch in range(2):
            y, zi1[ch] = lfilter(b1, a1, mix[:, ch].astype(np.float64), zi=zi1[ch])
            kw[:, ch], zi2[ch] = lfilter(b2, a2, y, zi=zi2[ch])
        m = n // hop
        if m:
            k2 = (kw[:m * hop] ** 2).reshape(m, hop, 2).mean(axis=1).sum(axis=1)      # sum of channels
            u2 = (mix[:m * hop].astype(np.float64) ** 2).reshape(m, hop, 2).mean(axis=1).mean(axis=1)
            z_sub.extend(k2.tolist())
            u_sub.extend(u2.tolist())

    z = np.array(z_sub)
    u = np.array(u_sub)
    res = dict(duration=(end - start) / sr, sample_rate=sr, clips=len(clips))

    # --- LUFS
    if len(z) >= 4:
        mom = np.convolve(z, np.ones(4) / 4, mode="valid")[::1]          # 400 ms, 100 ms hop
        gated = mom[[lufs(v) > ABS_GATE for v in mom]]
        if len(gated):
            rel = lufs(gated.mean()) - 10
            gated2 = gated[[lufs(v) > rel for v in gated]]
            res["integrated"] = lufs(gated2.mean()) if len(gated2) else -math.inf
        else:
            res["integrated"] = -math.inf
        res["momentary_max"] = max(lufs(v) for v in mom)
    if len(z) >= 30:
        st = np.convolve(z, np.ones(30) / 30, mode="valid")              # 3 s
        st_l = np.array([lufs(v) for v in st])
        ok = st_l[st_l > ABS_GATE]
        res["short_max"] = float(st_l.max())
        res["short_min"] = float(ok.min()) if len(ok) else -math.inf
    else:
        whole = lufs(z.mean()) if len(z) else -math.inf
        res.setdefault("integrated", whole)
        res["short_max"] = res["short_min"] = whole

    # --- dBFS
    res["peak"] = 20 * math.log10(peak) if peak > 0 else -math.inf
    res["true_peak"] = 20 * math.log10(tpeak) if tpeak > 0 else -math.inf
    loud = u[[db(v) > ABS_GATE for v in u]] if len(u) else u
    res["rms_avg"] = db(loud.mean()) if len(loud) else -math.inf
    if len(u) >= 30:
        win = np.convolve(u, np.ones(30) / 30, mode="valid")
        w_db = np.array([db(v) for v in win])
        ok = w_db[w_db > ABS_GATE]
        res["rms_min"] = float(ok.min()) if len(ok) else -math.inf
        res["rms_max"] = float(w_db.max())
    else:
        res["rms_min"] = res["rms_max"] = res["rms_avg"]
    return res


def fmt(v, unit=""):
    if v is None:
        return "–"
    if v == -math.inf:
        return "−∞"
    return f"{v:.1f}{unit}".replace("-", "−")
