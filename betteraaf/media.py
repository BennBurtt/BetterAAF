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

AUDIO_EXT = (".wav", ".bwf", ".aif", ".aiff", ".aifc", ".mxf", ".mov", ".mp4", ".m4a", ".mp3",
             ".flac", ".caf", ".ogg", ".aac", ".mts", ".m2ts")


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


class AVSource(Source):
    """Any audio FFmpeg can read (MXF OP-Atom / OP1a, MOV, MP4, M4A, MP3...), through PyAV.
    All audio streams are flattened into one channel list (stream 1 ch 1, stream 1 ch 2,
    stream 2 ch 1...), which matches how NLEs number the tracks of camera files.
    Random access: the file is decoded in 4-second chunks around the request, kept in a
    small cache; sample positions come from the packet timestamps (sample accurate)."""

    CHUNK_SEC = 4.0

    def __init__(self, path):
        import av
        from collections import OrderedDict
        self.av = av
        self.path = path
        self.c = av.open(path)
        self.streams = list(self.c.streams.audio)
        if not self.streams:
            raise IOError("no audio stream")
        self.map = []
        for si, st in enumerate(self.streams):
            n = st.codec_context.channels or 1
            for ch in range(n):
                self.map.append((si, ch))
        self.nch = len(self.map)
        self.sr = int(self.streams[0].sample_rate or self.streams[0].codec_context.sample_rate)
        st0 = self.streams[0]
        if st0.duration and st0.time_base:
            self.frames = int(round(float(st0.duration * st0.time_base) * self.sr))
        elif self.c.duration:
            self.frames = int(round(self.c.duration / 1e6 * self.sr))
        else:
            self.frames = 10 ** 12
        self.offsets = []
        for st in self.streams:
            stt = st.start_time or 0
            self.offsets.append(stt if stt > 0 else 0)   # échantillon 0 = début du média
        self.chunk = int(self.CHUNK_SEC * self.sr)
        self.cache = OrderedDict()
        self.kind = "file via FFmpeg · " + ", ".join(
            f"{st.codec_context.name} {st.codec_context.channels}ch" for st in self.streams) + f" · {self.sr} Hz"

    def _decode_chunk(self, k):
        s0, s1 = k * self.chunk, (k + 1) * self.chunk
        buf = np.zeros((self.chunk, self.nch), dtype=np.float32)
        first_col = {}
        col = 0
        for si, st in enumerate(self.streams):
            first_col[si] = col
            col += st.codec_context.channels or 1
        st0 = self.streams[0]
        target = int((max(0, s0 - self.sr // 2) / self.sr) / float(st0.time_base)) + self.offsets[0]
        try:
            self.c.seek(target, stream=st0, backward=True, any_frame=False)
        except Exception:
            self.c.seek(0)
        done = set()
        for pkt in self.c.demux(*self.streams):
            si = self.streams.index(pkt.stream)
            if si in done:
                if len(done) == len(self.streams):
                    break
                continue
            try:
                frames = pkt.decode()
            except Exception:
                continue
            for fr in frames:
                if fr.pts is None:
                    continue
                st = self.streams[si]
                start = int(round(float((fr.pts - self.offsets[si]) * st.time_base) * self.sr))
                x = fr.to_ndarray()
                nchs = st.codec_context.channels or 1
                if x.ndim == 2 and x.shape[0] == 1 and nchs > 1 and not fr.format.is_planar:
                    x = x.reshape(-1, nchs).T                     # entrelacé -> (canaux, échantillons)
                x = _to_float(x, fr.format.name)
                n = x.shape[1]
                a, b = max(start, s0), min(start + n, s1)
                if b > a:
                    buf[a - s0:b - s0, first_col[si]:first_col[si] + nchs] = x[:, a - start:b - start].T
                if start >= s1:
                    done.add(si)
            if len(done) == len(self.streams):
                break
        return buf

    def read(self, start, n):
        out = np.zeros((n, self.nch), dtype=np.float32)
        a, b = max(start, 0), min(start + n, self.frames)
        pos = a
        while pos < b:
            k = pos // self.chunk
            chunk = self.cache.get(k)
            if chunk is None:
                chunk = self._decode_chunk(k)
                self.cache[k] = chunk
                while len(self.cache) > 24:
                    self.cache.popitem(last=False)
            else:
                self.cache.move_to_end(k)
            c0 = pos - k * self.chunk
            take = min(b - pos, self.chunk - c0)
            out[pos - start:pos - start + take] = chunk[c0:c0 + take]
            pos += take
        return out


def _to_float(x, fmt):
    """Échantillons PyAV (s16, s32, flt, dbl, u8, planaires ou non) -> float32."""
    f = fmt.rstrip("p")
    if f == "s16":
        return x.astype(np.float32) / 32768.0
    if f == "s32":
        return x.astype(np.float32) / 2147483648.0
    if f == "u8":
        return (x.astype(np.float32) - 128.0) / 128.0
    if f == "s64":
        return (x.astype(np.float64) / 9.223372036854776e18).astype(np.float32)
    return x.astype(np.float32)


class RawSource(Source):
    """PCM brut (embarqué dans l'AAF, ou décodé par ffmpeg)."""

    def __init__(self, stream_factory, nch, sr, bits, big_endian=False, is_float=False, total=None, base=0):
        self.stream_factory = stream_factory
        self.nch, self.sr, self.bits = nch, sr, bits
        self.big, self.is_float = big_endian, is_float
        self.bpf = nch * bits // 8
        self.frames = total if total is not None else None
        self.base = base            # début des échantillons dans le flux (après un éventuel en-tête)
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
        s.seek(self.base + a * self.bpf)
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
    raise ValueError("unsupported bit depth: %d bits" % bits)


def parse_wave_fmt(b):
    """Contenu d'un chunk 'fmt ' -> dict (gère WAVE_FORMAT_EXTENSIBLE)."""
    fmt, nch, sr, _bps, _ba, bits = struct.unpack("<HHIIHH", b[:16])
    if fmt == 0xFFFE and len(b) >= 26:
        fmt = struct.unpack("<H", b[24:26])[0]       # sous-format (GUID)
    return dict(nch=nch, sr=sr, bits=bits, is_float=(fmt == 3))


def parse_wave_summary(summary):
    """Summary d'un WAVEDescriptor = en-tête RIFF ; on y lit le chunk fmt."""
    b = bytes(summary)
    i = b.find(b"fmt ")
    if i < 0:
        return None
    size = struct.unpack("<I", b[i + 4:i + 8])[0]
    return parse_wave_fmt(b[i + 8:i + 8 + max(16, size)])


def _ext80(b):
    exp, mant = struct.unpack(">HQ", b[:10])
    return int(round(mant * 2.0 ** ((exp & 0x7FFF) - 16383 - 63))) if exp else 48000


def parse_aifc_comm(b, aifc=True):
    nch, frames, bits = struct.unpack(">hIh", b[:8])
    sr = _ext80(b[8:18])
    comp = b[18:22] if aifc and len(b) >= 22 else b"NONE"
    info = dict(nch=nch, sr=sr, bits=bits, is_float=False, big=True, frames=frames)
    if comp in (b"sowt",):
        info["big"] = False
    elif comp in (b"fl32", b"FL32"):
        info.update(is_float=True, bits=32)
    elif comp in (b"fl64", b"FL64"):
        info.update(is_float=True, bits=64)
    return info


def parse_aifc_summary(summary):
    b = bytes(summary)
    i = b.find(b"COMM")
    if i < 0:
        return None
    return parse_aifc_comm(b[i + 8:], aifc=b"AIFC" in b[:12] or b.find(b"AIFC") >= 0)


def parse_container(stream):
    """Si le flux embarqué contient un fichier WAV/RF64/AIFF complet (cas de Pro Tools),
    renvoie son format et la position des échantillons ; sinon None (PCM brut)."""
    try:
        stream.seek(0)
        head = stream.read(12)
    except Exception:
        return None
    if len(head) < 12:
        return None
    if head[:4] in (b"RIFF", b"RF64", b"BW64") and head[8:12] == b"WAVE":
        pos, info = 12, None
        for _ in range(64):
            stream.seek(pos)
            h = stream.read(8)
            if len(h) < 8:
                break
            cid, size = h[:4], struct.unpack("<I", h[4:])[0]
            if cid == b"fmt ":
                info = parse_wave_fmt(stream.read(max(16, size)))
            elif cid == b"data":
                if info is None:
                    return None
                info["base"] = pos + 8
                if size not in (0, 0xFFFFFFFF):
                    info["frames"] = size // max(1, info["nch"] * info["bits"] // 8)
                return info
            pos += 8 + size + (size & 1)
        return None
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        aifc = head[8:12] == b"AIFC"
        pos, info = 12, None
        for _ in range(64):
            stream.seek(pos)
            h = stream.read(8)
            if len(h) < 8:
                break
            cid, size = h[:4], struct.unpack(">I", h[4:])[0]
            if cid == b"COMM":
                info = parse_aifc_comm(stream.read(size), aifc)
            elif cid == b"SSND":
                if info is None:
                    return None
                off = struct.unpack(">I", stream.read(4))[0]
                info["base"] = pos + 16 + off
                return info
            pos += 8 + size + (size & 1)
    return None


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
        self.exts = AUDIO_EXT
        self.suspect = set()

    def add_extensions(self, exts):
        new = tuple(e for e in exts if e not in self.exts)
        if new:
            self.exts = self.exts + new
            self._index = None

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
                        if name.lower().endswith(self.exts):
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
            self.errors[mob_id] = "media not found"
        self.sources[mob_id] = src
        return src

    def _open(self, mob_id):
        mob = None
        for m in self.f.content.mobs:
            if str(m.mob_id) == mob_id:
                mob = m
                break
        if mob is None:
            raise IOError("file mob missing")
        d = mob.descriptor
        ess = self.f.content.essencedata.get(mob.mob_id, None)
        if ess is not None:
            return self._embedded(d, ess)
        path = self.locate(mob)
        if not path:
            raise IOError("media file not found (%s)" % (mob.name or mob_id))
        self.paths[mob_id] = path
        try:
            src = SoundFileSource(path)
            src.kind = f"file · {src.f.format} {src.f.subtype} · {src.nch} ch · {src.sr} Hz"
            return src
        except Exception:
            pass
        try:
            return AVSource(path)          # MXF, MOV, MP4, M4A, MP3... (FFmpeg intégré)
        except Exception as e:  # noqa
            first = e
        try:
            src = self._ffmpeg(path)
            src.kind = f"file via ffmpeg · {src.nch} ch · {src.sr} Hz"
            return src
        except Exception:
            raise IOError(f"cannot read {os.path.basename(path)} ({first})")

    def _embedded(self, d, ess):
        # 1) le flux contient-il un fichier complet (en-tête RIFF/AIFF) ?
        st = ess.open("r")
        info = parse_container(st)
        kind = "embedded file"
        if info is None:
            kind = "embedded PCM"
            if isinstance(d, aaf_essence.WAVEDescriptor):
                info = parse_wave_summary(prop(d, "Summary", b""))
            elif isinstance(d, aaf_essence.AIFCDescriptor):
                info = parse_aifc_summary(prop(d, "Summary", b""))
        if info is None:
            info = dict(nch=prop(d, "Channels", 1) or 1,
                        sr=int(round(float(prop(d, "AudioSamplingRate", 0) or prop(d, "SampleRate", 48000)))),
                        bits=prop(d, "QuantizationBits", 24) or 24, is_float=False)
        total = info.get("frames") if kind == "embedded file" else None
        src = RawSource(lambda st=st: st, info["nch"], info["sr"], info["bits"],
                        info.get("big", False), info.get("is_float", False), total=total,
                        base=info.get("base", 0))
        src.kind = (f"{kind} · {'float' if info.get('is_float') else 'PCM'} {info['bits']}-bit · "
                    f"{info['nch']} ch · {info['sr']} Hz" + (" · big-endian" if info.get("big") else ""))
        return src

    def _ffmpeg(self, path):
        if not self.ffmpeg:
            raise IOError("format needs ffmpeg to be read (%s)" % os.path.basename(path))
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
            raise IOError("ffmpeg could not decode %s" % os.path.basename(path))
        size = os.path.getsize(tmp)
        return RawSource(lambda: open(tmp, "rb"), nch, sr, 32, False, True, size // (4 * nch))

    def summary(self):
        """Résumé des types de médias lus (pour le journal)."""
        from collections import Counter
        c = Counter(getattr(src, "kind", "?") for src in self.sources.values() if src is not None)
        return [f"{n} × {k}" for k, n in c.most_common()]

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
            raise IOError(self.errors.get(ch.mob_id, "unreadable media"))
        return src.read(ch.sample_offset + offset, n)[:, ch.channel]
