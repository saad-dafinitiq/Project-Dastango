"""Render the film music library (DST-39): original tracks, two per mood.

Every track is composed and synthesised by this script (chords, a bass line, a
soft arpeggio, pad and plucks from plain oscillators, a little reverb), so the
files in assets/music/ are original work of this project, released as CC0, and
there is no third-party licence to track. Re-running it reproduces them
bit for bit (fixed seeds). assets/music/library.json is written alongside.

    python scripts/make_music.py            # writes assets/music/*.mp3 + library.json

Not a substitute for a human composer: the tracks are quiet beds that sit under
dialogue. Replacing or adding to them with licensed recordings is a matter of
dropping files in assets/music/ and adding a row to library.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 32000
LOOP_S = 40.0
OUT = Path(__file__).resolve().parent.parent / "assets" / "music"

# Scales as semitone steps from the root.
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
DORIAN = [0, 2, 3, 5, 7, 9, 10]
LYDIAN = [0, 2, 4, 6, 7, 9, 11]
PHRYGIAN = [0, 1, 3, 5, 7, 8, 10]

# mood -> list of variants: (title, root midi, scale, bpm, progression (scale degrees),
#                            brightness 0..1, pulse (soft beat), arp 'up'|'down'|'none')
MOODS = {
    "ambient": [("Open Air", 57, MAJOR, 60, [0, 4, 5, 3], 0.45, False, "up"),
                ("Slow Light", 50, LYDIAN, 54, [0, 1, 4, 0], 0.5, False, "none")],
    "neutral": [("Plain Day", 55, MAJOR, 66, [0, 3, 4, 0], 0.4, False, "up"),
                ("Quiet Room", 52, DORIAN, 60, [0, 3, 0, 4], 0.35, False, "none")],
    "tense": [("Held Breath", 45, MINOR, 84, [0, 0, 5, 4], 0.3, True, "none"),
              ("Wire", 48, PHRYGIAN, 96, [0, 1, 0, 5], 0.35, True, "down")],
    "ominous": [("Low Tide", 38, PHRYGIAN, 52, [0, 1, 5, 1], 0.2, False, "none"),
                ("Under the Floor", 41, MINOR, 48, [0, 5, 0, 3], 0.18, False, "none")],
    "mysterious": [("Half Seen", 52, DORIAN, 66, [0, 4, 3, 5], 0.4, False, "down"),
                   ("Lantern", 57, LYDIAN, 60, [0, 5, 1, 4], 0.5, False, "up")],
    "sad": [("Late Letter", 50, MINOR, 58, [0, 5, 3, 4], 0.35, False, "up"),
            ("Empty Chair", 53, MINOR, 52, [0, 3, 5, 4], 0.3, False, "none")],
    "joyful": [("Open Window", 60, MAJOR, 112, [0, 4, 5, 3], 0.8, True, "up"),
               ("Good News", 62, MAJOR, 120, [0, 3, 4, 5], 0.85, True, "up")],
    "energetic": [("Run", 57, DORIAN, 126, [0, 3, 4, 0], 0.8, True, "up"),
                  ("Lift Off", 59, MAJOR, 132, [0, 5, 3, 4], 0.85, True, "down")],
    "epic": [("Horizon", 48, MINOR, 78, [0, 5, 3, 4], 0.55, True, "up"),
             ("Gathering", 45, DORIAN, 72, [0, 3, 5, 4], 0.6, True, "none")],
    "ethereal": [("Glass", 64, LYDIAN, 56, [0, 4, 1, 5], 0.7, False, "up"),
                 ("Afterglow", 60, MAJOR, 50, [0, 5, 0, 3], 0.65, False, "down")],
}


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def degree_note(root: int, scale: list, degree: int) -> int:
    octave, idx = divmod(degree, 7)
    return root + scale[idx] + 12 * octave


def chord(root: int, scale: list, degree: int) -> list:
    return [degree_note(root, scale, degree + k) for k in (0, 2, 4)]


def lowpass_fast(x: np.ndarray, cutoff: float) -> np.ndarray:
    """The same filter via FFT (the loop above is only fine for short buffers)."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    h = 1 / (1 + (f / cutoff) ** 4)
    return np.fft.irfft(np.fft.rfft(x) * h, n)


def tone(freq: float, dur: float, kind: str, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    if kind == "pad":  # two detuned saws and a sine
        out = np.zeros_like(t)
        for det in (-0.004, 0.004):
            out += 2 * ((t * freq * (1 + det)) % 1) - 1
        out = out * 0.25 + np.sin(2 * np.pi * freq * t) * 0.5
    elif kind == "pluck":
        out = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) * np.exp(-t * 5)
        out *= np.exp(-t * 3.2)
    else:  # bass
        out = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)
    return out


def envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a, r = int(attack * SR), int(release * SR)
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def add(buf: np.ndarray, sig: np.ndarray, at: int) -> None:
    end = min(len(buf), at + len(sig))
    if end > at:
        buf[at:end] += sig[: end - at]


def reverb(x: np.ndarray, mix: float = 0.22) -> np.ndarray:
    """A few feedback delays, enough to take the dryness off."""
    wet = np.zeros_like(x)
    for delay_s, gain in ((0.097, 0.5), (0.151, 0.4), (0.233, 0.33), (0.337, 0.25)):
        d = int(delay_s * SR)
        wet[d:] += x[:-d] * gain
    return x * (1 - mix) + wet * mix


def render(title: str, root: int, scale: list, bpm: int, prog: list, bright: float,
           pulse: bool, arp: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = beat * 4
    bars = int(LOOP_S // bar)
    total = int(bars * bar * SR)
    pad = np.zeros(total)
    low = np.zeros(total)
    mid = np.zeros(total)
    kick = np.zeros(total)
    for b in range(bars):
        deg = prog[b % len(prog)]
        notes = chord(root, scale, deg)
        start = int(b * bar * SR)
        for n in notes:
            sig = tone(hz(n), bar + 0.6, "pad", rng)
            sig *= envelope(len(sig), 0.5, 0.9)
            add(pad, sig, start)
        bass = tone(hz(notes[0] - 12), bar, "bass", rng)
        bass *= envelope(len(bass), 0.03, 0.25)
        add(low, bass, start)
        if arp != "none":
            seq = notes + [notes[0] + 12, notes[1] + 12]
            seq = seq if arp == "up" else seq[::-1]
            for k in range(8):
                n = seq[k % len(seq)] + 12
                sig = tone(hz(n), beat, "pluck", rng) * (0.5 + 0.3 * (k % 2 == 0))
                add(mid, sig, start + int(k * beat * SR / 2))
        if pulse:
            for k in range(4):
                t = np.arange(int(0.25 * SR)) / SR
                thump = np.sin(2 * np.pi * (48 + 60 * np.exp(-t * 28)) * t) * np.exp(-t * 11)
                add(kick, thump * (0.9 if k % 2 == 0 else 0.5), start + int(k * beat * SR))
    pad = lowpass_fast(pad, 600 + 3200 * bright)
    mix = pad * 0.5 + low * 0.55 + mid * (0.22 + 0.2 * bright) + kick * 0.45
    mix = reverb(mix)
    # Short fades at both ends so a loop's seam never clicks (the music is also cut and
    # joined per scene by shared/music.py, which fades again at every join).
    fade = int(0.05 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(mix)) or 1.0
    return (mix / peak * 0.8).astype(np.float32)


def encode(samples: np.ndarray, out: Path) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
           "-c:a", "libmp3lame", "-b:a", "56k", str(out)]
    subprocess.run(cmd, input=pcm, check=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    seed = 1000
    for mood, variants in MOODS.items():
        for i, (title, root, scale, bpm, prog, bright, pulse, arp) in enumerate(variants, 1):
            seed += 1
            track_id = f"{mood}-{i}"
            path = OUT / f"{track_id}.mp3"
            encode(render(title, root, scale, bpm, prog, bright, pulse, arp, seed), path)
            rows.append({
                "id": track_id, "title": title, "mood": mood, "file": path.name,
                "duration_s": round(int(LOOP_S // (240 / bpm)) * 240 / bpm, 2), "bpm": bpm,
                "licence": "CC0 1.0", "author": "Dastango project (synthesised by scripts/make_music.py)",
                "source": "scripts/make_music.py",
            })
            print("wrote", path.name)
    (OUT / "library.json").write_text(json.dumps({"tracks": rows}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
