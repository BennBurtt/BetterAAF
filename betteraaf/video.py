"""Video engine for the player (PyAV / FFmpeg).

Formats: MOV, MP4, MXF with ProRes (all flavours), DNxHD/DNxHR, H.264 and H.265.
H.264/H.265 use VideoToolbox hardware decoding on macOS when available.

Sync: the video follows the audio clock (the sample actually reaching the speakers), and
frames are chosen by time from the AAF edit rates and the file's own frame rate, so a
23.976 file in a 24 fps timeline, or 29.97 material, lands on the right frame.

Decoding strategy:
  - playing at x1-x2: frames are decoded in order a few frames ahead (no seeking);
  - intra codecs (ProRes, DNx) at any speed and after a jump: direct seek to the frame;
  - long-GOP codecs (H.264/H.265): short jumps decode forward, long jumps seek to the
    previous keyframe and decode up to the exact frame; at x4/x10 only keyframes are shown.
"""

import collections
import math
import os
import sys
import threading
import time
from fractions import Fraction

import numpy as np

try:
    import av
except Exception:  # noqa
    av = None

from . import model

INTRA = {"prores", "dnxhd", "mjpeg", "png", "v210", "rawvideo", "dvvideo", "jpeg2000", "qtrle", "cfhd"}
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv")
AHEAD = 3
GOP_FORWARD_MAX = 90      # frames decoded forward before preferring a seek


def available():
    return av is not None


class VideoFile:
    def __init__(self, path, hw=True, log=print):
        self.path = path
        self.log = log
        self.hw_used = False
        self._open(hw)

    def _open(self, hw):
        self.c = None
        codec_name = None
        probe = av.open(self.path)
        st = probe.streams.video[0]
        codec_name = st.codec_context.name
        probe.close()
        self.codec = codec_name
        self.intra = codec_name in INTRA
        c = None
        if hw and sys.platform == "darwin" and codec_name in ("h264", "hevc"):
            try:
                from av.codec.hwaccel import HWAccel
                c = av.open(self.path, hwaccel=HWAccel(device_type="videotoolbox", allow_software_fallback=True))
                self.hw_used = True
            except Exception as e:  # noqa
                self.log(f"Video: hardware decoding unavailable for {os.path.basename(self.path)} ({e})")
                c = None
        if c is None:
            c = av.open(self.path)
            self.hw_used = False
        self.c = c
        self.st = c.streams.video[0]
        self.st.thread_type = "AUTO"
        self.tb = self.st.time_base
        rate = self.st.average_rate or self.st.guessed_rate or self.st.base_rate or Fraction(25)
        self.fps = Fraction(rate)
        self.t0 = self.st.start_time or 0
        self.width = self.st.codec_context.width
        self.height = self.st.codec_context.height
        sar = self.st.sample_aspect_ratio or self.st.codec_context.sample_aspect_ratio
        self.dar = (self.width * (float(sar) if sar else 1.0)) / max(1, self.height)
        n = self.st.frames
        if not n and self.st.duration:
            n = int(round(float(self.st.duration * self.tb) * self.fps))
        if not n and c.duration:
            n = int(round(c.duration / 1e6 * float(self.fps)))
        self.nframes = n or 10 ** 9
        self._gen = None
        self.next_idx = None     # index of the next frame the decoder will output
        self.cur = None          # (idx, av.VideoFrame) last decoded
        self.keyframe_only = False

    def describe(self):
        prof = ""
        try:
            prof = self.st.codec_context.profile or ""
        except Exception:
            pass
        return (f"{os.path.basename(self.path)} · {self.codec}{(' ' + prof) if prof else ''} · "
                f"{self.width}×{self.height} · {float(self.fps):.3f} fps" + (" · HW" if self.hw_used else ""))

    def close(self):
        try:
            self.c.close()
        except Exception:
            pass

    # ------------------------------------------------------------
    def idx_of(self, frame):
        pts = frame.pts if frame.pts is not None else frame.dts
        if pts is None:
            return (self.cur[0] + 1) if self.cur else 0
        return int(round(float((pts - self.t0) * self.tb) * self.fps))

    def _restart(self, idx):
        target = self.t0 + int(Fraction(idx) / self.fps / self.tb)
        self.c.seek(max(self.t0, target), stream=self.st, backward=True, any_frame=False)
        self._gen = self.c.decode(self.st)
        self.next_idx = None

    def _next(self):
        if self._gen is None:
            self._gen = self.c.decode(self.st)
        try:
            fr = next(self._gen)
        except StopIteration:
            return None
        except av.error.EOFError:
            return None
        idx = self.idx_of(fr)
        self.cur = (idx, fr)
        self.next_idx = idx + 1
        return fr

    def set_keyframe_only(self, on):
        on = bool(on) and not self.intra
        if on != self.keyframe_only:
            self.keyframe_only = on
            try:
                self.st.codec_context.skip_frame = "NONKEY" if on else "DEFAULT"
            except Exception:
                pass
            self._gen = None
            self.next_idx = None

    def frame(self, idx):
        """-> av.VideoFrame showing at index idx (exact, or the last keyframe <= idx in
        keyframe-only mode)."""
        idx = max(0, min(int(idx), self.nframes - 1))
        if self.cur and self.cur[0] == idx:
            return self.cur
        if self.keyframe_only:
            self._restart(idx)
            fr = self._next()
            return self.cur if fr is not None else None
        forward_ok = (self.next_idx is not None and idx >= self.next_idx and
                      (idx - self.next_idx) <= (0 if self.intra else GOP_FORWARD_MAX))
        if not forward_ok:
            # avec des images B (H.264/H.265), la recherche peut tomber sur l'image clé
            # qui suit la cible : on recule alors davantage et on recommence
            back = 0
            for attempt in range(8):
                self._restart(max(0, idx - back))
                fr = self._next()
                if fr is None or self.cur[0] <= idx or idx - back <= 0:
                    break
                back = int(max(back * 2, float(self.fps)))
            if fr is None:
                return None
            if self.cur[0] >= idx:
                return self.cur
        last = self.cur if forward_ok is False else None
        for _ in range(100000):
            fr = self._next()
            if fr is None:
                break
            last = self.cur
            if self.cur[0] >= idx:
                break
        return last

    @staticmethod
    def to_rgb(frame, width):
        h = max(2, int(round(frame.height * width / frame.width / 2)) * 2)
        try:
            return frame.reformat(width=width, height=h, format="rgb24",
                                  interpolation="BILINEAR").to_ndarray()
        except Exception:
            return frame.to_ndarray(format="rgb24")


def read_test_code(rgb, bits=16, ref_w=1280, ref_h=720):
    """Frame number written by tests/make_test_videos.py (black/white blocks, top row)."""
    h, w = rgb.shape[:2]
    block = ref_w // bits
    cy = int(block / 2 * h / ref_h)
    n = 0
    for b in range(bits):
        if rgb[cy, int((b + 0.5) * w / bits)].mean() > 127:
            n |= 1 << b
    return n


def self_test(folder, log=print):
    """Decodes every test video at sequential, random and backward positions and checks
    the frame number written in the picture. Returns the number of errors."""
    import glob
    import random
    random.seed(3)
    errors = 0
    files = sorted(glob.glob(os.path.join(folder, "*.*")))
    for path in files:
        vf = VideoFile(path, log=log)
        n = vf.nframes
        idxs = list(range(min(n, 30))) + [random.randrange(n) for _ in range(20)] + list(range(n - 1, n - 15, -2))
        bad = 0
        t = time.perf_counter()
        for i in idxs:
            got = vf.frame(i)
            if got is None or read_test_code(VideoFile.to_rgb(got[1], 640)) != i:
                bad += 1
        ms = (time.perf_counter() - t) * 1000 / len(idxs)
        log(f"  {os.path.basename(path):18} {vf.codec:7} {float(vf.fps):7.3f} fps  "
            f"{'HW' if vf.hw_used else 'SW'}  errors {bad}/{len(idxs)}  {ms:.1f} ms/frame")
        errors += bad
        vf.close()
    return errors


class VClip:
    __slots__ = ("start", "end", "path", "src", "name", "rate", "error")

    def __init__(self, start, end, path, src, name, rate, error=""):
        self.start, self.end, self.path, self.src, self.name, self.rate, self.error = (
            start, end, path, src, name, rate, error)


def _resolve_picture(sc, rate_from, pool):
    """SourceClip -> (path, start seconds in the file, name) through the master mob."""
    mob, slot_id, start, rate = sc.mob, sc.slot_id, Fraction(sc.start or 0), rate_from
    name = (mob.name if mob is not None else "") or ""
    for _ in range(10):
        if mob is None:
            return None, 0.0, name, "media missing from the AAF"
        slot = mob.slot_at(slot_id)
        srate = model.frac(slot.edit_rate)
        start_s = start * srate / rate
        if model.is_file_mob(mob):
            path = pool.locate(mob)
            if not path:
                return None, 0.0, name, "video file not found"
            return path, float(start_s / srate), name, ""
        seg = slot.segment
        comp, comp_pos = seg, 0
        if isinstance(seg, model.Sequence):
            comp = None
            for _i, p, c in seg.positions():
                if isinstance(c, model.Transition):
                    continue
                if p <= start_s < p + c.length:
                    comp, comp_pos = c, p
                    break
        if not isinstance(comp, model.SourceClip):
            return None, 0.0, name, "video not linked"
        start = Fraction(comp.start or 0) + (start_s - comp_pos)
        mob, slot_id, rate = comp.mob, comp.slot_id, srate
    return None, 0.0, name, "video chain too deep"


def build_video_track(comp, pool, log=print):
    """Topmost picture track of the composition -> list of VClip (timeline seconds)."""
    slots = []
    for slot in comp.mob.slots:
        try:
            if (slot.segment.media_kind or "").lower() == "picture":
                slots.append(slot)
        except Exception:
            pass
    if not slots:
        return []
    slots.sort(key=lambda s: -(model.prop(s, "PhysicalTrackNumber", 1) or 1))
    pool.add_extensions(VIDEO_EXT)
    out = []
    for slot in slots:
        rate = model.frac(slot.edit_rate)
        seg = model.track_sequence(slot)
        items = list(seg.positions()) if isinstance(seg, model.Sequence) else [(0, 0, seg)]
        clips = []
        for _i, pos, c in items:
            inner, _w = model.unwrap(c)
            if not isinstance(inner, model.SourceClip):
                continue
            path, src, name, err = _resolve_picture(inner, rate, pool)
            clips.append(VClip(float(Fraction(pos) / rate), float(Fraction(pos + c.length) / rate),
                               path, src, name, rate, err))
        if clips:
            out.append(clips)
    # piste du dessus d'abord ; les suivantes servent là où elle est vide
    return out


class VideoEngine:
    """Background decoder. clock() -> (timeline seconds, playing, speed)."""

    def __init__(self, tracks, clock, width=960, log=print, hw=True):
        self.tracks = tracks
        self.clock = clock
        self.width = width
        self.log = log
        self.hw = hw
        self.files = collections.OrderedDict()
        self.cache = collections.OrderedDict()     # key (path, idx) -> (rgb, dar)
        self.lock = threading.Lock()
        self.info = ""
        self.errors = {}
        self.decode_ms = 0.0
        self._run = True
        self._was_playing = False
        self._wake = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def close(self):
        self._run = False
        self._wake.set()
        self._t.join(timeout=1.0)
        for f in self.files.values():
            f.close()
        self.files.clear()

    def poke(self):
        self._wake.set()

    def set_width(self, w):
        w = int(max(320, min(1920, w)))
        if abs(w - self.width) > 64:
            self.width = w
            with self.lock:
                self.cache.clear()
            self.poke()

    # ------------------------------------------------------------
    def at(self, t):
        """timeline seconds -> (VClip, file index) or (VClip with error, None) or (None, None)."""
        for clips in self.tracks:
            for vc in clips:
                if vc.start <= t < vc.end:
                    if vc.path is None:
                        return vc, None
                    f = self.file(vc.path)
                    if f is None:
                        return vc, None
                    ft = vc.src + (t - vc.start)
                    return vc, int(math.floor(ft * float(f.fps) + 1e-6))
        return None, None

    def file(self, path):
        f = self.files.get(path)
        if f is None:
            if path in self.errors:
                return None
            try:
                f = VideoFile(path, hw=self.hw, log=self.log)
            except Exception as e:  # noqa
                self.errors[path] = str(e)
                self.log(f"Video: cannot open {os.path.basename(path)} ({e})")
                return None
            self.files[path] = f
            while len(self.files) > 4:
                _p, old = self.files.popitem(last=False)
                old.close()
        else:
            self.files.move_to_end(path)
        return f

    def frame_for(self, t):
        """Frame to display at timeline time t: (rgb, dar, key) or (None, None, status)."""
        vc, idx = self.at(t)
        if vc is None:
            return None, None, "no video"
        if idx is None:
            return None, None, vc.error or "offline"
        key = (vc.path, idx)
        with self.lock:
            item = self.cache.get(key)
            if item is None:
                # la plus proche image déjà décodée avant la cible (même fichier)
                best = None
                for (p, i), it in self.cache.items():
                    if p == vc.path and i <= idx and (best is None or i > best[0]):
                        best = (i, it)
                _t, _pl, sp = self.clock()
                if best and idx - best[0] <= max(2, int(2 * sp)):
                    item = best[1]
                    key = (vc.path, best[0])
        if item is None:
            return None, None, "decoding"
        return item[0], item[1], key

    def _decode(self, vc, idx, keyframe_only):
        f = self.file(vc.path)
        if f is None:
            return
        f.set_keyframe_only(keyframe_only)
        t = time.perf_counter()
        got = f.frame(idx)
        if got is None:
            return
        gidx, fr = got
        rgb = VideoFile.to_rgb(fr, self.width)
        self.decode_ms = 0.8 * self.decode_ms + 0.2 * (time.perf_counter() - t) * 1000
        with self.lock:
            self.cache[(vc.path, idx)] = (rgb, f.dar)
            if gidx != idx:
                self.cache[(vc.path, gidx)] = (rgb, f.dar)
            while len(self.cache) > 12:
                self.cache.popitem(last=False)
        self.info = f.describe()

    def _lag(self, vc, idx, f):
        """How many frames the decoder is behind the clock for this file."""
        if f is None or f.cur is None or self.files.get(vc.path) is not f:
            return 0
        return max(0, idx - f.cur[0])

    def _loop(self):
        while self._run:
            try:
                t, playing, speed = self.clock()
                todo = []
                vc, idx = self.at(t)
                if vc is not None and idx is not None:
                    f = self.files.get(vc.path)
                    fps = float(f.fps) if f else 25.0
                    if playing:
                        # images clés seules uniquement si le décodage décroche vraiment
                        now = time.perf_counter()
                        last_t = getattr(self, "_last_t", None)
                        if last_t is None or not self._was_playing or abs(t - last_t) > 0.5 * speed + 0.25:
                            self._grace = now + 0.6          # démarrage ou saut : pas de décrochage
                            self._kf_until = 0
                        self._last_t = t
                        lag = self._lag(vc, idx, f)
                        if f is not None and not f.intra:
                            if lag > fps * 0.75 and now > getattr(self, "_grace", 0):
                                self._kf_until = now + 1.0
                            kf = time.perf_counter() < getattr(self, "_kf_until", 0)
                        else:
                            kf = False
                        if speed >= 3:
                            # on vise l'image qui sera à l'écran quand le décodage sera fini
                            ahead = min(0.25, self.decode_ms / 1000.0 * 1.5 + 0.012)
                            leads = [ahead, ahead + 0.02]
                            for dt in leads:
                                v2, i2 = self.at(t + dt * speed)
                                if v2 is not None and i2 is not None:
                                    todo.append((v2, i2, kf))
                        else:
                            # en avance du temps de décodage, puis quelques images d'avance
                            step = speed / fps
                            ahead = min(0.2, self.decode_ms / 1000.0 * 1.3 + 0.005) * speed
                            for k in range(AHEAD + 1):
                                v2, i2 = self.at(t + ahead + k * step)
                                if v2 is not None and i2 is not None:
                                    todo.append((v2, i2, kf))
                    else:
                        todo.append((vc, idx, False))
                self._was_playing = playing
                did = False
                for v2, i2, kf in todo:
                    with self.lock:
                        have = (v2.path, i2) in self.cache
                    if not have:
                        # si on a pris du retard, inutile de décoder une image déjà passée
                        tnow, pl, _sp = self.clock()
                        cur = self.at(tnow)
                        if pl and cur[0] is v2 and cur[1] is not None and i2 < cur[1]:
                            continue
                        self._decode(v2, i2, kf)
                        did = True
                if not did:
                    self._wake.wait(0.004)
                    self._wake.clear()
            except Exception as e:  # noqa
                self.log(f"Video error: {e}")
                time.sleep(0.05)
