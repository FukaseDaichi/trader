"""Synthesize the promo soundtrack (numpy only) so the video has no third-party audio.

Usage: python promo/scripts/bgm.py promo/bgm.wav
Cue times mirror the scene table `T` in promo/index.html.
"""

import sys
import wave

import numpy as np

SR = 48000
DUR = 104.0
T = {
    "s1": 0,
    "s2": 7,
    "s3": 14.5,
    "s4": 26,
    "s5": 41,
    "s6": 49.5,
    "s7": 60,
    "s8": 68.5,
    "s9": 76,
    "s10": 91,
    "s11": 97,
}
BPM = 120
BEAT = 60 / BPM

n = int(SR * DUR)
t = np.arange(n) / SR
L = np.zeros(n)
R = np.zeros(n)
rng = np.random.default_rng(3)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def place(buf, start, sig):
    i = int(start * SR)
    if i >= n:
        return
    j = min(n, i + len(sig))
    buf[i:j] += sig[: j - i]


def env_adsr(length, a, r):
    m = int(length * SR)
    e = np.ones(m)
    ai, ri = int(a * SR), int(r * SR)
    e[:ai] = np.linspace(0, 1, ai) ** 2
    e[m - ri :] *= np.linspace(1, 0, ri) ** 2
    return e


# --- pads: Am9 / Fmaj7 / Cmaj7 / G6, 4 beats * 2 each --------------------
chords = [
    [57, 60, 64, 67, 71],
    [53, 57, 60, 64, 69],
    [48, 55, 59, 64, 67],
    [55, 59, 62, 64, 71],
]
bar = BEAT * 8
k = 0
start = 0.0
while start < DUR:
    length = bar + 1.5
    m = int(length * SR)
    tt = np.arange(m) / SR
    sig_l = np.zeros(m)
    sig_r = np.zeros(m)
    for note in chords[k % 4]:
        f = hz(note)
        for det, pan in ((-0.12, 0.8), (0.12, 0.2)):
            ff = f * 2 ** (det / 12)
            w = (
                np.sin(2 * np.pi * ff * tt)
                + 0.25 * np.sin(4 * np.pi * ff * tt)
                + 0.08 * np.sin(6 * np.pi * ff * tt)
            )
            sig_l += w * pan
            sig_r += w * (1 - pan)
    e = env_adsr(length, 1.4, 1.6)
    place(L, start, sig_l * e * 0.022)
    place(R, start, sig_r * e * 0.022)
    start += bar
    k += 1

# --- sub bass on chord roots from the title onward ------------------------
k = 0
start = 0.0
while start < DUR:
    if start >= T["s2"] - 0.01:
        root = chords[k % 4][0] - 24
        m = int(bar * SR)
        tt = np.arange(m) / SR
        s = np.sin(2 * np.pi * hz(root) * tt) * env_adsr(bar, 0.3, 0.8) * 0.11
        place(L, start, s)
        place(R, start, s)
    start += bar
    k += 1


# --- soft kick + hat on the explainer chapters ----------------------------
def kick():
    m = int(0.35 * SR)
    tt = np.arange(m) / SR
    f = 110 * np.exp(-tt * 18) + 42
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-tt * 9) * 0.32


def hat():
    m = int(0.06 * SR)
    x = rng.standard_normal(m)
    x = x - lowpass(x, 6000)
    return x * np.exp(-np.arange(m) / SR * 70) * 0.035


beat_t = T["s3"]
while beat_t < T["s11"] - 0.2:
    gain = 0.6 if beat_t < T["s4"] else 1.0
    place(L, beat_t, kick() * gain)
    place(R, beat_t, kick() * gain)
    h = hat()
    place(L, beat_t + BEAT / 2, h * 0.9)
    place(R, beat_t + BEAT / 2, h * 1.1)
    beat_t += BEAT

# --- arpeggio plucks through the "how it learns" chapters ------------------
arp_t = T["s4"]
step = BEAT / 2
i = 0
while arp_t < T["s9"]:
    chord = chords[int(arp_t // bar) % 4]
    note = chord[[0, 2, 4, 3, 1, 2, 4, 3][i % 8]] + 12
    m = int(0.5 * SR)
    tt = np.arange(m) / SR
    s = (
        (np.sin(2 * np.pi * hz(note) * tt) + 0.3 * np.sin(4 * np.pi * hz(note) * tt))
        * np.exp(-tt * 7)
        * 0.045
    )
    pan = 0.5 + 0.35 * np.sin(i * 0.7)
    place(L, arp_t, s * pan)
    place(R, arp_t, s * (1 - pan))
    arp_t += step
    i += 1


# --- risers and impacts on key cuts ---------------------------------------
def riser(length):
    m = int(length * SR)
    x = rng.standard_normal(m)
    x = lowpass(x, 2500)
    return x * np.linspace(0, 1, m) ** 3 * 0.12


def impact():
    m = int(2.5 * SR)
    tt = np.arange(m) / SR
    boom = (
        np.sin(2 * np.pi * (55 * np.exp(-tt * 3) + 30) * tt) * np.exp(-tt * 2.2) * 0.4
    )
    air = lowpass(rng.standard_normal(m), 1800) * np.exp(-tt * 4) * 0.12
    return boom + air


def chime(at, notes, gain=0.05):
    for j, note in enumerate(notes):
        m = int(2.0 * SR)
        tt = np.arange(m) / SR
        s = (
            (
                np.sin(2 * np.pi * hz(note) * tt)
                + 0.2 * np.sin(6 * np.pi * hz(note) * tt)
            )
            * np.exp(-tt * 2.5)
            * gain
        )
        place(L, at + j * 0.07, s)
        place(R, at + j * 0.07, s)


# clock ticks before 06:00
for s in (0.4, 1.27, 2.13):
    m = int(0.03 * SR)
    tick = rng.standard_normal(m) * np.exp(-np.arange(m) / SR * 200) * 0.06
    place(L, s, tick)
    place(R, s, tick)
place(L, 1.6, riser(1.4))
place(R, 1.6, riser(1.4))
for buf in (L, R):
    place(buf, 3.0, impact() * 0.8)
chime(3.0, [69, 76, 81, 88])
for at in (T["s2"], T["s3"], T["s9"], T["s11"]):
    place(L, at - 1.2, riser(1.2))
    place(R, at - 1.2, riser(1.2))
    place(L, at, impact())
    place(R, at, impact())
chime(T["s2"] + 0.6, [57, 64, 69, 72, 76, 81])
chime(T["s6"] + 4.6, [72, 76, 79, 84], 0.04)  # gate pass
chime(T["s11"] + 3.2, [57, 64, 69, 71, 76, 81, 88], 0.045)

# --- master ----------------------------------------------------------------
fade_in = np.clip(t / 0.4, 0, 1)
fade_out = np.clip((DUR - t) / 2.5, 0, 1)
mix = np.stack([L, R], axis=1) * (fade_in * fade_out)[:, None]
mix = np.tanh(mix * 1.6) / np.tanh(1.6)
mix /= max(1e-9, np.abs(mix).max()) / 0.89

out = sys.argv[1] if len(sys.argv) > 1 else "promo/bgm.wav"
with wave.open(out, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print("wrote", out)
