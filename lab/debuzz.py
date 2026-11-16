"""Take the VAE's frame buzz out of the highs, HERESY 1030.

YuE2's VAE writes sound in frames of 1920 samples (48000 / 1920 = 25 a second). Folding the
4–12 kHz envelope of a take on exactly 1920 samples shows a shape that repeats in every frame,
~20 % deep, with its peak at the seam between frames; folding on 1900 or 1940 shows 5 % (noise).
Measured on one take decoded by standard, legacy and blend VAEs alike (30.09.2026). That is a
gain ripple locked to the frame clock: heard as a buzz at 25 Hz and its multiples in the highs.

The cure follows from the measure: split at CROSS Hz (linear phase), learn the in-frame gain
shape of the highs over a sliding window of WIN frames, divide STRENGTH of it out (bounded to
±LIMIT dB), add the lows back untouched. Only the part locked to the frame clock is removed:
the music's own movement averages out of the fold, because it is not locked to 1920 samples.
Viktor's ear, 30.09.2026: 100 % thinned the opening; 80 % was right there.

The split and the 4–12 kHz envelope are overlap-adds of short linear-phase FIRs (the same
curves the full-file FFT used; the gain stays within a few thousandths of a dB of that). Working
memory is one block plus the window of frames, not several copies of the take. The per-frame
profiles sit in a temporary memmap, so a long song does not hold its whole waveform.

The fold is measured before and after, against control periods (1900, 1940): a track not made
by this VAE (an import) shows no more at 1920 than at the controls, and is said to have none.

    debuzz.py IN OUT.flac [STRENGTH=0.8] [CROSS=2000] [WIN=50] [LIMIT=3] [PERIOD=1920] [RATE=48000] [SUBTYPE=PCM_24]
    PERIOD and RATE are the model's (HERESY 1055: the page sends sample_rate / frame_rate from /props)
    SUBTYPE is the soundfile subtype of OUT (PCM_24, the Refiner's; PCM_16 or FLOAT when the take's own format asks)
    prints one line of JSON: the fold depth in, out and at the controls, the gain range

debuzz_file() is the same cure, importable. The engine calls this script on a new WAV take
(debuzz default true): audio.wav becomes the cleaned file, audio.raw.wav keeps the peak-whole one.
FLOAT (a WAV32 take) is written at the level the cure left: no whole-file peak scale. PCM still
comes down to 0.999 when a peak would not fit.
"""
import json, os, sys, tempfile, time
import numpy as np
import soundfile as sf

_BLOCK = 16384
_CROSS_FIR = 4096
_BRICK_FIR = 8192


def _mag_cross(F, cross):
    lo = cross / 2.0 ** (1.0 / 6.0)
    hi = cross * 2.0 ** (1.0 / 6.0)
    g = np.clip((np.log2(np.maximum(F, 1.0)) - np.log2(lo)) / (np.log2(hi) - np.log2(lo)), 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * g)


def _mag_brick(F):
    return ((F >= 4000.0) & (F < 12000.0)).astype(np.float64)


def _fir(rate, nfft, mag):
    freqs = np.fft.rfftfreq(nfft, 1.0 / rate)
    return np.fft.fftshift(np.fft.irfft(mag(freqs), nfft)).astype(np.float64)


def _smooth(p, w=48):
    return np.convolve(np.r_[p[-w:], p, p[:w]], np.ones(w) / w, "same")[w:-w]


class _OLA:
    """Overlap-add of one linear-phase FIR. push() returns samples aligned with the input."""

    def __init__(self, ir, channels, block=_BLOCK):
        self.L = len(ir)
        self.block = block
        self.ch = channels
        self.nfft = 1
        while self.nfft < block + self.L - 1:
            self.nfft *= 2
        self.H = np.fft.rfft(np.asarray(ir, dtype=np.float64), self.nfft)
        self.delay = self.L // 2
        self.acc = np.zeros((self.nfft, channels), dtype=np.float64)
        self.hold = np.zeros((0, channels), dtype=np.float64)
        self.primed = 0

    def _raw(self, chunk):
        b = chunk.shape[0]
        buf = np.zeros((self.block, self.ch), dtype=np.float64)
        buf[:b] = chunk
        filtered = np.fft.irfft(np.fft.rfft(buf, self.nfft, axis=0) * self.H[:, None], self.nfft, axis=0)
        self.acc += filtered
        out = self.acc[:b].copy()
        self.acc[:-b] = self.acc[b:]
        self.acc[-b:] = 0
        return out

    def push(self, chunk):
        raw = self._raw(chunk)
        if self.hold.shape[0]:
            raw = np.concatenate([self.hold, raw], 0)
            self.hold = np.zeros((0, self.ch), dtype=np.float64)
        if self.primed < self.delay:
            need = self.delay - self.primed
            if raw.shape[0] <= need:
                self.primed += raw.shape[0]
                return np.zeros((0, self.ch), dtype=np.float64)
            self.primed = self.delay
            raw = raw[need:]
        return raw

    def finish(self, remain):
        if remain <= 0:
            return np.zeros((0, self.ch), dtype=np.float64)
        tail = self.push(np.zeros((self.delay, self.ch), dtype=np.float64))
        if tail.shape[0] < remain:
            raise RuntimeError("debuzz overlap-add ended %d samples short" % (remain - tail.shape[0]))
        return tail[:remain]


class _Depth:
    """Fold depth of a streaming envelope: the mean in-frame shape, smoothed, as a percent."""

    def __init__(self, period):
        self.period = int(period)
        self.hold = np.zeros(0, dtype=np.float64)
        self.acc = np.zeros(self.period, dtype=np.float64)
        self.count = 0

    def push(self, samples):
        if samples.size == 0:
            return
        buf = np.concatenate([self.hold, np.asarray(samples, dtype=np.float64)])
        per = self.period
        usable = (buf.size // per) * per
        if usable:
            frames = buf[:usable].reshape(-1, per)
            self.acc += (frames / (frames.mean(1, keepdims=True) + 1e-12)).sum(0)
            self.count += frames.shape[0]
        self.hold = buf[usable:]

    def depth(self):
        if self.count == 0:
            return 0.0
        prof = _smooth(self.acc / self.count)
        return float(100.0 * (prof.max() - prof.min()))


def _frame_gain(profiles, quiet, i, win, strength, limit):
    k = quiet.shape[0]
    half = win // 2
    a, b = max(0, i - half), min(k, i + half + 1)
    heard = ~quiet[a:b]
    m = int(heard.sum())
    if m < 8:
        return np.ones(profiles.shape[1], dtype=np.float64)
    prof = _smooth(np.asarray(profiles[a:b][heard], dtype=np.float64).mean(0))
    prof /= prof.mean()
    return np.clip(prof ** -strength, 10 ** (-limit / 20.0), 10 ** (limit / 20.0))


def debuzz_file(src, dst, strength=0.8, cross=2000.0, win=50, limit=3.0, period=1920, rate=48000, subtype="PCM_24"):
    """The frame-locked gain out of the highs. Writes dst. Returns the measure (before, after, found, gain)."""
    strength = float(strength)
    cross = float(cross)
    win = int(win)
    limit = float(limit)
    P = int(period)
    RATE = int(rate)
    src, dst = str(src), str(dst)
    t0 = time.time()
    info = sf.info(src)
    if info.samplerate != RATE:
        raise SystemExit("frames of %d samples are a %d Hz fact: this file is %d Hz" % (P, RATE, info.samplerate))
    n, ch = int(info.frames), int(info.channels)
    if n <= 0 or ch <= 0:
        raise SystemExit("debuzz: empty audio")
    k = n // P
    C1, C2 = P - round(P / 96), P + round(P / 96)
    fir_cross = _fir(RATE, _CROSS_FIR, lambda F: _mag_cross(F, cross))
    fir_brick = _fir(RATE, _BRICK_FIR, _mag_brick)
    preserve = subtype == "FLOAT"

    fd, prof_path = tempfile.mkstemp(prefix="ruach-debuzz-", suffix=".f32")
    os.close(fd)
    part = None
    scaled = None
    profiles = np.memmap(prof_path, dtype=np.float32, mode="w+", shape=(max(k, 1), P))
    quiet = np.ones(k, dtype=bool)
    before_frame, before_c1, before_c2 = _Depth(P), _Depth(C1), _Depth(C2)
    brick = _OLA(fir_brick, 1)
    env_hold = np.zeros(0, dtype=np.float64)
    filled = 0
    pos = 0
    try:
        with sf.SoundFile(src) as src_file:
            while pos < n:
                block = src_file.read(min(_BLOCK, n - pos), dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                mono = block.mean(axis=1)
                end = pos + mono.shape[0]
                i0, i1 = pos // P, (end - 1) // P
                for i in range(i0, min(i1, k - 1) + 1):
                    a, b = max(0, i * P - pos), min(mono.shape[0], (i + 1) * P - pos)
                    if np.max(np.abs(mono[a:b])) >= 1e-4:
                        quiet[i] = False
                env = np.abs(brick.push(mono[:, None])[:, 0])
                if env.size:
                    before_frame.push(env)
                    before_c1.push(env)
                    before_c2.push(env)
                    env_hold = np.concatenate([env_hold, env])
                    while env_hold.size >= P and filled < k:
                        frame = env_hold[:P]
                        env_hold = env_hold[P:]
                        profiles[filled] = (frame / (frame.mean() + 1e-12)).astype(np.float32)
                        filled += 1
                pos += block.shape[0]
        held = before_frame.count * P + int(before_frame.hold.size)
        remain = n - held
        if remain > 0:
            env = np.abs(brick.finish(remain)[:, 0])
            before_frame.push(env)
            before_c1.push(env)
            before_c2.push(env)
            env_hold = np.concatenate([env_hold, env])
            while env_hold.size >= P and filled < k:
                frame = env_hold[:P]
                env_hold = env_hold[P:]
                profiles[filled] = (frame / (frame.mean() + 1e-12)).astype(np.float32)
                filled += 1
        profiles.flush()
        before = {"frame": before_frame.depth(), "control": max(before_c1.depth(), before_c2.depth())}

        ext = os.path.splitext(dst)[1].lower() or ".wav"
        part = dst + ".writing" + ext
        cross = _OLA(fir_cross, ch)
        out_brick = _OLA(fir_brick, 1)
        after_frame, after_c1, after_c2 = _Depth(P), _Depth(C1), _Depth(C2)
        buf = np.zeros((0, ch), dtype=np.float64)
        written = 0
        gain_min, gain_max = 1.0, 1.0
        peak = 0.0
        cache_i, cache_g = -1, None

        def consume(high):
            nonlocal buf, written, gain_min, gain_max, peak, cache_i, cache_g
            if high.shape[0] == 0:
                return
            take = high.shape[0]
            xpart = buf[:take]
            buf = buf[take:]
            y = np.empty_like(xpart)
            idx = 0
            while idx < take:
                at = written + idx
                frame_i = at // P
                if frame_i >= k:
                    y[idx:] = xpart[idx:]
                    idx = take
                    break
                if frame_i != cache_i:
                    cache_g = _frame_gain(profiles, quiet, frame_i, win, strength, limit)
                    cache_i = frame_i
                    gain_min = min(gain_min, float(cache_g.min()))
                    gain_max = max(gain_max, float(cache_g.max()))
                off = at % P
                ntake = min(take - idx, P - off)
                g = cache_g[off:off + ntake, None]
                y[idx:idx + ntake] = xpart[idx:idx + ntake] + high[idx:idx + ntake] * (g - 1.0)
                idx += ntake
            peak = max(peak, float(np.max(np.abs(y))))
            out_file.write(y.astype(np.float32))
            env = np.abs(out_brick.push(y.mean(axis=1)[:, None])[:, 0])
            after_frame.push(env)
            after_c1.push(env)
            after_c2.push(env)
            written += take

        with sf.SoundFile(src) as src_file, sf.SoundFile(part, "w", samplerate=RATE, channels=ch, subtype=subtype) as out_file:
            seen = 0
            while seen < n:
                block = src_file.read(min(_BLOCK, n - seen), dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                seen += block.shape[0]
                block64 = block.astype(np.float64)
                buf = np.concatenate([buf, block64], 0)
                consume(cross.push(block64))
            consume(cross.finish(buf.shape[0]))
            held = after_frame.count * P + int(after_frame.hold.size)
            remain = written - held
            if remain > 0:
                env = np.abs(out_brick.finish(remain)[:, 0])
                after_frame.push(env)
                after_c1.push(env)
                after_c2.push(env)
        if written != n:
            raise RuntimeError("debuzz wrote %d samples of %d" % (written, n))

        scale = 1.0
        if not preserve and peak > 0.999:
            scale = 0.999 / peak
            scaled = dst + ".scaling" + ext
            with sf.SoundFile(part) as src_file, sf.SoundFile(scaled, "w", samplerate=RATE, channels=ch, subtype=subtype) as out_file:
                while True:
                    block = src_file.read(_BLOCK, dtype="float32", always_2d=True)
                    if block.shape[0] == 0:
                        break
                    out_file.write(block * np.float32(scale))
            os.replace(scaled, part)
        os.replace(part, dst)
        after = {"frame": after_frame.depth(), "control": max(after_c1.depth(), after_c2.depth())}
        return {"before": {key: round(val, 2) for key, val in before.items()},
                "after": {key: round(val, 2) for key, val in after.items()},
                "found": before["frame"] > 1.6 * before["control"], "strength": strength,
                "gain_db": [round(20 * float(np.log10(max(gain_min, 1e-12))), 2),
                            round(20 * float(np.log10(max(gain_max, 1e-12))), 2)],
                "scaled_db": round(20 * float(np.log10(scale)), 2) if scale < 1.0 else 0.0,
                "seconds": round(n / RATE, 2), "took": round(time.time() - t0, 1)}
    finally:
        del profiles
        for leftover in (prof_path, part, scaled):
            if leftover and leftover != dst and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass


def main(argv):
    src, dst = argv[1], argv[2]
    strength = float(argv[3]) if len(argv) > 3 else 0.8
    cross = float(argv[4]) if len(argv) > 4 else 2000
    win = int(argv[5]) if len(argv) > 5 else 50
    limit = float(argv[6]) if len(argv) > 6 else 3
    period = int(argv[7]) if len(argv) > 7 else 1920
    rate = int(argv[8]) if len(argv) > 8 else 48000
    subtype = argv[9] if len(argv) > 9 else "PCM_24"
    print(json.dumps(debuzz_file(src, dst, strength, cross, win, limit, period, rate, subtype)))


if __name__ == "__main__":
    main(sys.argv)
