"""Read-only playback engine for the mini player.

A feeder thread renders each track (stereo, gain/automation/fades/pan applied, muted
clips skipped) a little ahead of time; the audio callback applies solo/mute, sums the
tracks and measures per-track peaks for the meters. Solo/mute therefore react at once.
"""

import bisect
import collections
import math
import threading
import time

import aaf2
import numpy as np

from . import model, media, analysis, levels

BLOCK = 1024
AHEAD = 10          # blocks rendered in advance (~0.2 s at 48 kHz)


class NullStream:
    """Silent output used when no audio device is available (keeps the clock running)."""

    def __init__(self, sr, callback):
        self.sr, self.callback = sr, callback
        self._run = False
        self._t = None

    def start(self):
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        out = np.zeros((BLOCK, 2), dtype=np.float32)
        nxt = time.perf_counter()
        while self._run:
            self.callback(out, BLOCK, None, None)
            nxt += BLOCK / self.sr
            time.sleep(max(0.0, nxt - time.perf_counter()))

    def stop(self):
        self._run = False

    def close(self):
        self.stop()


def open_stream(sr, callback, log):
    """Stereo (L/R) stream on the output currently selected in macOS.
    PortAudio is re-initialised so a change of output in System Settings is picked up."""
    try:
        import sounddevice as sd
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        dev = sd.query_devices(kind="output")
        st = sd.OutputStream(samplerate=sr, channels=2, blocksize=BLOCK, dtype="float32",
                             callback=callback, latency="low")
        st.device_name = dev.get("name", "default output")
        return st, True
    except Exception as e:  # noqa
        log(f"Player: no audio output available ({e}). Playback runs silently.")
        st = NullStream(sr, callback)
        st.device_name = "no audio output"
        return st, False


class Engine:
    def __init__(self, log=print):
        self.log = log
        self.f = None
        self.comp = None
        self.pool = None
        self.tracks = []
        self.clips_by_track = []
        self.starts_by_track = []
        self.sr = 48000
        self.start = 0
        self.end = 0
        self.pos = 0.0             # position heard (samples, timeline)
        self.feed_pos = 0.0
        self.speed = 1.0
        self.playing = False
        self.solo = set()
        self.mute = set()
        self.peaks = np.zeros(0)
        self.queue = collections.deque()
        self.lock = threading.Lock()
        self.stream = None
        self.has_audio = False
        self._feeder = None
        self._gen = 0
        self.reached_end = False

    # ------------------------------------------------------------ loading
    def load(self, path, comp_index=0, media_dirs=()):
        self.stop()
        self.close_file()
        self.f = aaf2.open(path, "r")
        mob = model.list_compositions(self.f)[comp_index]
        comp = model.read_composition(self.f, mob)
        self.pool = media.MediaPool(self.f, path, media_dirs, log=self.log)
        clips = analysis.prepare_clips(comp, self.pool, self.log)
        self.comp = comp
        self.tracks = comp.tracks
        playable = [c for c in clips if not c.muted and not analysis.is_muted(c)]
        rates = collections.Counter(c.sr for c in playable)
        self.sr = rates.most_common(1)[0][0] if rates else 48000
        self.clips_by_track = []
        self.starts_by_track = []
        for t in self.tracks:
            cl = sorted((c for c in playable if c.track is t and c.sr == self.sr), key=lambda c: c.s0)
            self.clips_by_track.append(cl)
            self.starts_by_track.append([c.s0 for c in cl])
        allc = [c for c in clips if c.sr == self.sr] or clips
        self.start = min((c.s0 for c in allc), default=0)
        self.end = max((c.s1 for c in allc), default=self.sr * 10)
        self.mute = {t.index for t in self.tracks if t.muted}
        self.solo = set()
        self.peaks = np.zeros(len(self.tracks))
        self.pos = self.feed_pos = float(self.start)
        return comp

    def close_file(self):
        if self.pool is not None:
            self.pool.close()
        if self.f is not None:
            try:
                self.f.close()
            except Exception:
                pass
        self.f = self.pool = self.comp = None

    def close(self):
        self.stop()
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.close_file()

    # ------------------------------------------------------------ rendering
    def render(self, s0, n_out, speed):
        """-> (ntracks, n_out, 2) float32, starting at timeline sample s0."""
        span = int(math.ceil(n_out * speed)) + 2
        s0i = int(math.floor(s0))
        out = np.zeros((len(self.tracks), n_out, 2), dtype=np.float32)
        for ti, clips in enumerate(self.clips_by_track):
            if not clips:
                continue
            buf = None
            starts = self.starts_by_track[ti]
            i = max(0, bisect.bisect_right(starts, s0i + span) - 1)
            # remonter tant que des clips peuvent encore chevaucher (fondus enchaînés)
            j = i
            while j > 0 and clips[j - 1].s1 > s0i:
                j -= 1
            for c in clips[j:i + 1]:
                a, e = max(s0i, c.s0), min(s0i + span, c.s1)
                if e <= a:
                    continue
                if buf is None:
                    buf = np.zeros((span, 2), dtype=np.float32)
                spe = float(self.sr / c.track.edit_rate)
                env = levels.envelope(c, a - c.s0, e - c.s0, spe)
                dst = slice(a - s0i, e - s0i)
                try:
                    if c.nch == 1:
                        x = self.pool.read(c.channels[0], a - c.s0, e - a) * env
                        if c.side == "L":
                            buf[dst, 0] += x
                        elif c.side == "R":
                            buf[dst, 1] += x
                        else:
                            gl, gr = levels.pan_gains(c, a - c.s0, e - c.s0, spe)
                            buf[dst, 0] += x * gl
                            buf[dst, 1] += x * gr
                    else:
                        buf[dst, 0] += self.pool.read(c.channels[0], a - c.s0, e - a) * env
                        buf[dst, 1] += self.pool.read(c.channels[1], a - c.s0, e - a) * env
                except Exception:  # noqa
                    continue
            if buf is None:
                continue
            if speed == 1.0 and s0 == s0i:
                out[ti] = buf[:n_out]
            else:
                pos = (s0 - s0i) + np.arange(n_out) * speed
                k = pos.astype(np.int64)
                fr = (pos - k).astype(np.float32)[:, None]
                out[ti] = buf[k] * (1 - fr) + buf[np.minimum(k + 1, span - 1)] * fr
        return out

    def _feed(self, gen):
        while self.playing and gen == self._gen:
            with self.lock:
                need = len(self.queue) < AHEAD
                fp, sp = self.feed_pos, self.speed
            if not need:
                time.sleep(0.005)
                continue
            data = self.render(fp, BLOCK, sp)
            with self.lock:
                if gen != self._gen:
                    break
                self.queue.append((fp, sp, data))
                self.feed_pos = fp + BLOCK * sp

    def audible(self):
        n = len(self.tracks)
        mask = np.ones(n, dtype=bool)
        for i in self.mute:
            if i < n:
                mask[i] = False
        if self.solo:
            solo = np.zeros(n, dtype=bool)
            for i in self.solo:
                if i < n:
                    solo[i] = True
            mask &= solo
        return mask

    def _callback(self, outdata, frames, t, status):
        with self.lock:
            item = self.queue.popleft() if self.queue else None
        if item is None or frames != BLOCK:
            outdata[:] = 0
            return
        fp, sp, data = item
        mask = self.audible()
        if len(self.tracks):
            mix = data[mask].sum(axis=0) if mask.any() else np.zeros((BLOCK, 2), np.float32)
            self.peaks = np.where(mask, np.abs(data).max(axis=(1, 2)), 0.0)
        else:
            mix = np.zeros((BLOCK, 2), np.float32)
        outdata[:] = np.clip(mix, -1.0, 1.0)
        self.pos = fp + BLOCK * sp
        if self.pos >= self.end:
            self.reached_end = True

    # ------------------------------------------------------------ transport
    def play(self):
        if self.comp is None or self.playing:
            return
        if self.pos >= self.end:
            self.pos = float(self.start)
        self.reached_end = False
        with self.lock:
            self._gen += 1
            self.queue.clear()
            self.feed_pos = self.pos
        self.playing = True
        self._feeder = threading.Thread(target=self._feed, args=(self._gen,), daemon=True)
        self._feeder.start()
        # nouveau flux à chaque lecture : suit la sortie choisie dans macOS
        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream, self.has_audio = open_stream(self.sr, self._callback, self.log)
        try:
            self.stream.start()
        except Exception as e:  # noqa
            self.log(f"Player: audio output failed ({e}). Playback runs silently.")
            self.stream, self.has_audio = NullStream(self.sr, self._callback), False
            self.stream.device_name = "no audio output"
            self.stream.start()

    @property
    def device_name(self):
        return getattr(self.stream, "device_name", "")

    def stop(self):
        if not self.playing:
            return
        self.playing = False
        with self.lock:
            self._gen += 1
            self.queue.clear()
        try:
            self.stream.stop()
        except Exception:
            pass
        self.peaks = np.zeros(len(self.tracks))

    def seek(self, sample):
        sample = float(min(max(sample, self.start), self.end))
        with self.lock:
            self._gen += 1 if self.playing else 0
            self.queue.clear()
            self.pos = self.feed_pos = sample
        if self.playing:
            gen = self._gen
            self._feeder = threading.Thread(target=self._feed, args=(gen,), daemon=True)
            self._feeder.start()

    def set_speed(self, speed):
        self.speed = float(speed)
        if self.playing:
            self.seek(self.pos)

    def toggle_solo(self, ti):
        self.solo ^= {ti}

    def toggle_mute(self, ti):
        self.mute ^= {ti}
