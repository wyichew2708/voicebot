"""Small operations on 16-bit mono PCM.

Only what joining two clips needs. Anything larger belongs in the backend.
"""
from __future__ import annotations

import array

# Below this a sample is room tone or codec noise, not speech. Generous on
# purpose: trimming a hair too little leaves an imperceptible gap, trimming
# too much clips the front of a word.
_FLOOR = 300


def trim(pcm: bytes, head: bool = True, tail: bool = True,
         keep_ms: int = 20, sample_rate: int = 16000) -> bytes:
    """Drop silence from the edges, leaving `keep_ms` of it behind.

    TTS clips arrive with a third to half a second of padding at the end. Two
    of those joined back to back put three quarters of a second of dead air in
    the middle of one sentence, which sounds like the line dropped.
    """
    a = array.array("h")
    a.frombytes(pcm)
    if not a:
        return pcm
    keep = int(sample_rate * keep_ms / 1000)
    start, end = 0, len(a)
    if head:
        start = next((i for i, v in enumerate(a) if abs(v) > _FLOOR), len(a))
        start = max(0, start - keep)
    if tail:
        back = next((i for i, v in enumerate(reversed(a)) if abs(v) > _FLOOR), len(a))
        end = min(len(a), len(a) - back + keep)
    if start >= end:
        return pcm                      # all quiet: not ours to judge, hand it back
    return a[start:end].tobytes()


def silence(ms: int, sample_rate: int) -> bytes:
    return bytes(int(sample_rate * ms / 1000) * 2)


def stretch(data: bytes, rate: float, sample_rate: int = 16000) -> bytes:
    """Slow speech down (rate < 1) without moving its pitch.

    WSOLA: overlap-add, but each output frame is taken from wherever inside a
    small search window it best continues the previous one. Plain overlap-add
    is half the code and sounds like a robot in a tunnel — the phase jumps
    between frames are exactly what the ear picks up in speech.

    Resampling is not an option: it would make the agent sound like a deeper,
    different person, which is the opposite of what a caller asking you to
    slow down wants.
    """
    if abs(rate - 1.0) < 0.001 or not data:
        return data
    import numpy as np

    x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    frame = int(sample_rate * 0.030)          # 30 ms
    hop_out = frame // 2
    hop_in = max(1, int(round(hop_out * rate)))
    search = int(sample_rate * 0.010)         # +/- 10 ms to find the best join
    if len(x) < frame + 2 * search:
        return data

    win = np.hanning(frame).astype(np.float32)
    n_out = int(len(x) / rate) + frame
    out = np.zeros(n_out, dtype=np.float32)
    norm = np.zeros(n_out, dtype=np.float32)

    chosen = 0                                 # where the last frame was taken from
    write = 0
    k = 0
    while True:
        nominal = k * hop_in
        if nominal + frame >= len(x) or write + frame >= n_out:
            break
        if k == 0:
            best = 0
        else:
            # The segment that would have followed the frame we just used is
            # what the next one has to continue from.
            tgt = chosen + hop_out
            if tgt + frame > len(x):
                break
            template = x[tgt:tgt + frame]
            lo = max(0, nominal - search)
            hi = min(len(x) - frame, nominal + search)
            if hi <= lo:
                best = max(0, min(nominal, len(x) - frame))
            else:
                cands = np.lib.stride_tricks.sliding_window_view(
                    x[lo:hi + frame], frame)
                # Normalised cross-correlation, not a raw dot product. The
                # raw score is proportional to candidate energy, so the search
                # picks whichever frame is loudest rather than whichever one
                # lines up — which drops or repeats pitch periods and moves
                # the pitch. A pure tone hides it (uniform energy); speech
                # does not: a 170 Hz clip stretched by 1.15 came out at 154 Hz.
                energy = np.sqrt(np.einsum("ij,ij->i", cands, cands)) + 1e-6
                best = lo + int(np.argmax((cands @ template) / energy))
        out[write:write + frame] += x[best:best + frame] * win
        norm[write:write + frame] += win
        chosen = best
        write += hop_out
        k += 1

    out, norm = out[:write + frame], norm[:write + frame]
    out = np.divide(out, norm, out=np.zeros_like(out), where=norm > 1e-6)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


_STRETCHED: dict[tuple[int, int, float], bytes] = {}


def stretch_cached(data: bytes, rate: float, sample_rate: int = 16000) -> bytes:
    """`stretch`, remembered. A caller who asks us to slow down keeps the
    slower rate for the rest of the call, so the same seven lines would
    otherwise be re-stretched on every turn of every call."""
    if abs(rate - 1.0) < 0.001 or not data:
        return data
    k = (hash(data), len(data), round(rate, 3))
    hit = _STRETCHED.get(k)
    if hit is None:
        if len(_STRETCHED) > 32:               # a handful of lines, not a store
            _STRETCHED.clear()
        hit = _STRETCHED[k] = stretch(data, rate, sample_rate)
    return hit


def peak(data: bytes) -> float:
    """Loudest sample, 0-1. A level meter reads the microphone; this reads
    what was actually written down."""
    import numpy as np

    if not data:
        return 0.0
    a = np.frombuffer(data, dtype=np.int16)
    return float(np.abs(a).max()) / 32768.0


def median_f0(data: bytes, sample_rate: int = 16000) -> float:
    """Median voiced pitch, or nan when the clip carries none.

    YIN's difference function with cumulative-mean normalisation, not plain
    autocorrelation. Plain autocorrelation picks whichever lag has the highest
    peak, and for speech that is regularly *twice* the true period — it read a
    182 Hz line as 107 Hz, and the pitch normaliser then shifted it the wrong
    way. Taking the first lag under an absolute threshold is the standard
    guard against exactly that.
    """
    import numpy as np

    frames = _f0_frames(data, sample_rate)
    return float(np.median(frames)) if frames else float("nan")


def _f0_frames(data: bytes, sample_rate: int = 16000) -> list[float]:
    """Per-frame pitch for every voiced frame. See `median_f0` for the method."""
    import numpy as np

    x = np.frombuffer(data, dtype=np.int16).astype(np.float64)
    # 32 ms hop, not 64: a one-second line has to yield enough voiced frames
    # for its median to mean something, and half of any short line is
    # consonants and gaps.
    win, hop = 2048, 512
    lo, hi = max(2, sample_rate // 350), min(win // 2, sample_rate // 70)
    found: list[float] = []
    for i in range(0, max(0, len(x) - win), hop):
        w = x[i:i + win]
        if np.sqrt((w * w).mean()) < 500:          # silence between words
            continue
        w = w - w.mean()
        # d(tau) = sum (w[j] - w[j+tau])^2, via autocorrelation and running
        # energy so the whole lag range costs one FFT.
        n = 1 << (2 * win - 1).bit_length()
        spec = np.fft.rfft(w, n)
        acf = np.fft.irfft(spec * np.conj(spec), n)[:hi + 1]
        power = np.concatenate(([0.0], np.cumsum(w * w)))
        total = power[win]
        taus = np.arange(hi + 1)
        head = total - (power[win] - power[win - taus])   # sum of w[0:win-tau]^2
        tail = total - power[taus]                        # sum of w[tau:win]^2
        d = head + tail - 2 * acf
        d[0] = 0.0
        run = np.cumsum(d[1:])
        dn = np.ones(hi + 1)
        dn[1:] = d[1:] * np.arange(1, hi + 1) / np.maximum(run, 1e-9)
        band = dn[lo:hi + 1]
        under = np.flatnonzero(band < 0.15)
        tau = lo + (int(under[0]) if under.size else int(np.argmin(band)))
        if dn[tau] >= 0.5:                         # not periodic: unvoiced frame
            continue
        # The threshold crossing is on the way down, not at the bottom. Walk
        # to the local minimum first — interpolating around a point that is
        # not one gives a worse answer than not interpolating at all.
        while tau + 1 <= hi and dn[tau + 1] < dn[tau]:
            tau += 1
        # The true period falls between lags. Without this the estimate reads
        # a few percent high on every clip — harmless for comparing one
        # rendering against another, but it makes the configured target read
        # like a number nobody could measure.
        period = float(tau)
        if lo < tau < hi:
            a, b, c_ = dn[tau - 1], dn[tau], dn[tau + 1]
            denom = a - 2 * b + c_
            if abs(denom) > 1e-12:
                period += 0.5 * (a - c_) / denom
        found.append(sample_rate / period)
    return found


def f0_stats(data: bytes, sample_rate: int = 16000) -> tuple[float, int, float]:
    """(median pitch, voiced frames, relative spread) — how much to trust it.

    A clip with a handful of voiced frames, or one whose pitch sweeps across a
    falling intonation, has a median that does not represent it. Correcting
    such a clip toward a target moves it somewhere the measurement never
    justified, and can leave it further from the voice than it started.
    """
    import numpy as np

    frames = _f0_frames(data, sample_rate)
    if not frames:
        return float("nan"), 0, float("nan")
    a = np.array(frames)
    med = float(np.median(a))
    q1, q3 = np.percentile(a, [25, 75])
    return med, len(a), float((q3 - q1) / med) if med else float("nan")


def pitch_shift(data: bytes, ratio: float, sample_rate: int = 16000) -> bytes:
    """Move the pitch by `ratio` and put the duration back.

    Time-stretch by 1/ratio, then resample by ratio: the stretch changes the
    length without touching the pitch, and the resample changes both, so the
    length cancels out and the pitch does not.
    """
    if abs(ratio - 1.0) < 0.005 or not data:
        return data
    import numpy as np

    stretched = stretch(data, 1.0 / ratio, sample_rate)
    x = np.frombuffer(stretched, dtype=np.int16).astype(np.float32)
    n = int(len(x) / ratio)
    if n < 2:
        return data
    idx = np.arange(n, dtype=np.float32) * ratio
    out = np.interp(idx, np.arange(len(x), dtype=np.float32), x)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()
