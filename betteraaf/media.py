"""Accès aux échantillons audio : média embarqué dans l'AAF ou fichiers liés
(WAV/BWF/AIFF via soundfile, MXF et autres via ffmpeg si disponible)."""

import os
import shutil
import struct
import subprocess
import tempfile
from urllib.parse import urlparse, unquote

import numpy as np
import soundfile as sf

from aaf2 import essence as aaf_essence
from .model import prop

AUDIO_EXT = (".wav", ".bwf", ".aif", ".aiff", ".aifc", ".mxf")


def find_ffmpeg():
    for p in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "ffmpeg"), os.path.join(here, "..", "ffmpeg")):
        if os.path.exists(p):
            return p
    return None


def url_to_path(url):
    if not url:
        return ""
    if url.lower().startswith("file:"):
        p = urlparse(url)
        path = unquote(p.path)
        if p.netloc and p.netloc.lower() not in ("", "localhost"):
            path = "/" + p.netloc + path
        # chemins Windows "file:///C:/..."
        if len(path) > 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return unquote(url)


def basename_any(path):
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


class Source:
    """Un fichier média ouvert (lecture aléatoire, float32)."""
    nch = 1
    sr = 48000
    frames = 0

    def read(self, start, n):  # -> array (n, nch)
        raise NotImplementedError


class SoundFileSource(Source):
    def __init__(self, path):
        self.path = path
        self.f = sf.SoundFile(path)
        self.nch, self.sr, self.frames = self.f.channels, self.f.samplerate, self.f.frames

    def read(self, start, n):
        out = np.zeros((n, self.nch), dtype=np.float32)
        a, b = max(start, 0), min(start + n, self.frames)
        if b > a:
            self.f.seek(a)
            data = self.f.read(b - a, dtype="float32", always_2d=True)
            out[a - start:a - start + len(data)] = data
        return out


class RawSource(Source):
    """PCM brut (embarqué dans l'AAF, ou décodé par ffmpeg)."""

    def __init__(self, stream_factory, nch, sr, bits, big_endian=False, is_float=False, total=None):
        self.stream_factory = stream_factory
        self.nch, self.sr, self.bits = nch, sr, bits
        self.big, self.is_float = big_endian, is_float
        self.bpf = nch * bits // 8
        self.frames = total if total is not None else None
        self._stream = None

    def stream(self):
        if self._stream is None:
            self._stream = self.stream_factory()
        return self._stream

    def read(self, start, n):
        out = np.zeros((n, self.nch), dtype=np.float32)
        a = max(start, 0)
        if self.frames is not None:
            b = min(start + n, self.frames)
        else:
            b = start + n
        if b <= a:
            return out
        s = self.stream()
        s.seek(a * self.bpf)
        raw = s.read((b - a) * self.bpf)
        cnt = len(raw) // self.bpf
        if cnt <= 0:
            return out
        raw = raw[:cnt * self.bpf]
        out[a - start:a - start + cnt] = decode_pcm(raw, self.bits, self.big, self.is_float).reshape(cnt, self.nch)
        return out


def decode_pcm(raw, bits, big=False, is_float=False):
    e = ">" if big else "<"
    if is_float:
        return np.frombuffer(raw, dtype=e + ("f4" if bits == 32 else "f8")).astype(np.float32)
    if bits == 8:
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    if bits == 16:
        return np.frombuffer(raw, dtype=e + "i2").astype(np.float32) / 32768.0
    if bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        if big:
            v = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]
        else:
            v = (b[:, 2] << 16) | (b[:, 1] << 8) | b[:, 0]
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        return v.astype(np.float32) / float(1 << 23)
    if bits == 32:
        return np.frombuffer(raw, dtype=e + "i4").astype(np.float32) / 2147483648.0
    raise ValueError("résolution non supportée : %d bits" % bits)


def parse_wave_summary(summary):
    """Summary d'un WAVEDescriptor = en-tête RIFF ; on y lit le chunk fmt."""
    b = bytes(summary)
    i = b.find(b"fmt ")
    if i < 0:
        return None
    fmt, nch, sr, _bps, _ba, bits = struct.unpack("<HHIIHH", b[i + 8:i + 24])
    return dict(nch=nch, sr=sr, bits=bits, is_float=(fmt == 3))


def parse_aifc_summary(summary):
    b = bytes(summary)
    i = b.find(b"COMM")
    if i < 0:
        return None
    nch, _frames, bits = struct.unpack(">hIh", b[i + 8:i + 16])
    # sample rate en 80 bits IEEE extended
    exp, mant = struct.unpack(">HQ", b[i + 16:i + 26])
    sr = int(mant * 2.0 ** (exp - 16383 - 63)) if exp else 48000
    return dict(nch=nch, sr=sr, bits=bits, is_float=False, big=True)


class MediaPool:
    def __init__(self, f, aaf_path, media_dirs=(), log=print):
        self.f = f
        self.aaf_dir = os.path.dirname(os.path.abspath(aaf_path))
        self.media_dirs = [d for d in media_dirs if d and os.path.isdir(d)]
        self.log = log
        self.sources = {}
        self.errors = {}
        self.paths = {}
        self._index = None
        self._tmp = []
        self.ffmpeg = find_ffmpeg()

    # ---------- recherche des fichiers ----------
    def index(self):
        if self._index is None:
            self._index = {}
            roots = list(self.media_dirs) + [self.aaf_dir]
            count = 0
            for root in roots:
                for dp, dn, fn in os.walk(root):
                    dn[:] = [d for d in dn if not d.startswith(".")]
                    for name in fn:
                        if name.lower().endswith(AUDIO_EXT):
                            self._index.setdefault(name.lower(), os.path.join(dp, name))
                            count += 1
                    if count > 200000:
                        break
        return self._index

    def locate(self, mob):
        d = mob.descriptor
        cands = []
        try:
            for loc in (prop(d, "Locator", []) or []):
                u = prop(loc, "URLString") or prop(loc, "Name")
                if u:
                    cands.append(url_to_path(u))
        except Exception:
            pass
        for p in cands:
            if p and os.path.exists(p):
                return p
        idx = self.index()
        names = [basename_any(p) for p in cands if p]
        if mob.name:
            names += [mob.name, mob.name + ".wav", mob.name + ".aif", mob.name + ".mxf"]
        for n in names:
            hit = idx.get(n.lower())
            if hit:
                return hit
        return None

    # ---------- ouverture ----------
    def source(self, mob_id):
        if mob_id in self.sources:
            return self.sources[mob_id]
        if mob_id in self.errors:
            return None
        try:
            src = self._open(mob_id)
        except Exception as e:  # noqa
            src = None
            self.errors[mob_id] = str(e)
        if src is None and mob_id not in self.errors:
            self.errors[mob_id] = "média introuvable"
        self.sources[mob_id] = src
        return src

    def _open(self, mob_id):
        mob = None
        for m in self.f.content.mobs:
            if str(m.mob_id) == mob_id:
                mob = m
                break
        if mob is None:
            raise IOError("mob fichier absent")
        d = mob.descriptor
        ess = self.f.content.essencedata.get(mob.mob_id, None)
        if ess is not None:
            return self._embedded(d, ess)
        path = self.locate(mob)
        if not path:
            raise IOError("fichier média introuvable (%s)" % (mob.name or mob_id))
        self.paths[mob_id] = path
        try:
            return SoundFileSource(path)
        except Exception:
            return self._ffmpeg(path)

    def _embedded(self, d, ess):
        info = None
        if isinstance(d, aaf_essence.WAVEDescriptor):
            info = parse_wave_summary(prop(d, "Summary", b""))
        elif isinstance(d, aaf_essence.AIFCDescriptor):
            info = parse_aifc_summary(prop(d, "Summary", b""))
        if info is None:
            info = dict(nch=prop(d, "Channels", 1) or 1,
                        sr=int(round(float(prop(d, "AudioSamplingRate", 0) or prop(d, "SampleRate", 48000)))),
                        bits=prop(d, "QuantizationBits", 24) or 24, is_float=False)
        return RawSource(lambda: ess.open("r"), info["nch"], info["sr"], info["bits"],
                         info.get("big", False), info.get("is_float", False))

    def _ffmpeg(self, path):
        if not self.ffmpeg:
            raise IOError("format non lisible sans ffmpeg (%s)" % os.path.basename(path))
        probe = subprocess.run([self.ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True)
        import re
        m = re.search(r"Audio:.*?(\d+) Hz, ([^,]+),", probe.stderr)
        sr = int(m.group(1)) if m else 48000
        layout = m.group(2).strip() if m else "mono"
        nch = {"mono": 1, "stereo": 2}.get(layout)
        if nch is None:
            m2 = re.match(r"(\d+)", layout)
            nch = int(m2.group(1)) if m2 else 1
        fd, tmp = tempfile.mkstemp(suffix=".f32")
        os.close(fd)
        self._tmp.append(tmp)
        r = subprocess.run([self.ffmpeg, "-v", "error", "-y", "-i", path, "-map", "0:a:0",
                            "-f", "f32le", "-acodec", "pcm_f32le", tmp], capture_output=True)
        if r.returncode != 0:
            raise IOError("ffmpeg n'a pas pu décoder %s" % os.path.basename(path))
        size = os.path.getsize(tmp)
        return RawSource(lambda: open(tmp, "rb"), nch, sr, 32, False, True, size // (4 * nch))

    def close(self):
        for p in self._tmp:
            try:
                os.remove(p)
            except OSError:
                pass

    # ---------- lecture d'un canal ----------
    def prepare(self, ch):
        """Fixe ch.channel (canal réel du fichier). Retourne la Source ou None."""
        src = self.source(ch.mob_id)
        if src is None:
            return None
        if ch.channel < 0:
            if src.nch == 1:
                ch.channel = 0
            elif ch.phys and ch.phys <= src.nch:
                ch.channel = ch.phys - 1
            else:
                ch.channel = 0
        return src

    def read(self, ch, offset, n):
        """n échantillons du canal, à partir de offset (relatif au début du clip)."""
        src = self.prepare(ch)
        if src is None:
            raise IOError(self.errors.get(ch.mob_id, "média illisible"))
        return src.read(ch.sample_offset + offset, n)[:, ch.channel]
