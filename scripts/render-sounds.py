#!/usr/bin/env python3
"""Render the Kulan OS sound theme (soft, short chimes) as Ogg Vorbis files.

Every sound is synthesised here from sine partials, so the theme is fully
Kulan's own and can be re-rendered or retuned. Needs numpy and ffmpeg (with
libvorbis). Output: packages/kulan-desktop-settings/root/usr/share/sounds/kulan/

Usage: scripts/render-sounds.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / "packages/kulan-desktop-settings/root/usr/share/sounds/kulan"
RATE = 48000
PEAK = 0.45  # about -7 dBFS: present, never loud

# Note frequencies (equal temperament, A4 = 440 Hz).
NOTES = {name: 440.0 * 2 ** (semis / 12) for name, semis in {
    "D5": 5, "E5": 7, "F#5": 9, "A5": 12, "B5": 14, "C#6": 16, "D6": 17, "E6": 19, "F#6": 21, "A6": 24,
    "A4": 0, "F4": -4, "C#5": 4,
}.items()}


def chime(freq: float, length: float, decay: float = 4.0, brightness: float = 1.0) -> np.ndarray:
    """A soft glass-like chime: a few sine partials, higher ones fading faster."""
    t = np.arange(int(RATE * length)) / RATE
    tone = np.zeros_like(t)
    for ratio, amp, fade in ((1.0, 1.0, 1.0), (2.0, 0.28 * brightness, 1.8), (3.0, 0.10 * brightness, 2.6),
                             (4.2, 0.04 * brightness, 3.5)):
        tone += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-decay * fade * t)
    attack = np.clip(t / 0.004, 0, 1)  # 4 ms, no click
    return tone * attack


def sequence(parts: list[tuple[float, np.ndarray]], length: float) -> np.ndarray:
    """Mix (start time, sound) parts into one buffer of the given length."""
    out = np.zeros(int(RATE * length))
    for start, sound in parts:
        i = int(RATE * start)
        n = min(len(sound), len(out) - i)
        out[i:i + n] += sound[:n]
    return out


def noise_click(length: float, low: float, high: float, decay: float) -> np.ndarray:
    """A short filtered-noise click (shutter-like), deterministic."""
    rng = np.random.default_rng(7)
    n = int(RATE * length)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spectrum[(freqs < low) | (freqs > high)] = 0
    t = np.arange(n) / RATE
    return np.fft.irfft(spectrum, n) * np.exp(-decay * t) * np.clip(t / 0.002, 0, 1)


def sounds() -> dict[str, np.ndarray]:
    n = NOTES
    return {
        # Notifications: a gentle rising two-note chime.
        "message-new-instant": sequence([(0, chime(n["E6"], 0.6)), (0.09, chime(n["A6"], 0.6, 4.5))], 0.75),
        "message": sequence([(0, chime(n["E6"], 0.6)), (0.09, chime(n["A6"], 0.6, 4.5))], 0.75),
        # Alert ("bell"): one soft, rounded note.
        "bell": chime(n["A5"], 0.5, 5.0, 0.8),
        "dialog-information": chime(n["A5"], 0.5, 5.0, 0.8),
        # Something finished: a small rising arpeggio.
        "complete": sequence([(0, chime(n["D6"], 0.5)), (0.08, chime(n["F#6"], 0.5)),
                              (0.16, chime(n["A6"], 0.7, 3.5))], 0.9),
        # Warnings: two lower notes, falling; errors a little lower still.
        "dialog-warning": sequence([(0, chime(n["C#6"], 0.5, 5.0)), (0.12, chime(n["A5"], 0.6, 4.5))], 0.75),
        "dialog-error": sequence([(0, chime(n["A5"], 0.5, 5.0)), (0.12, chime(n["F4"] * 2, 0.6, 4.5))], 0.75),
        # Devices and power: rising when something arrives, falling when it leaves.
        "device-added": sequence([(0, chime(n["A5"], 0.4, 6.0)), (0.07, chime(n["E6"], 0.5, 5.0))], 0.6),
        "device-removed": sequence([(0, chime(n["E6"], 0.4, 6.0)), (0.07, chime(n["A5"], 0.5, 5.0))], 0.6),
        "power-plug": sequence([(0, chime(n["D6"], 0.35, 7.0)), (0.06, chime(n["A6"], 0.45, 6.0))], 0.55),
        "power-unplug": sequence([(0, chime(n["A6"], 0.35, 7.0)), (0.06, chime(n["D6"], 0.45, 6.0))], 0.55),
        "network-connectivity-established": sequence([(0, chime(n["B5"], 0.4, 6.0)),
                                                      (0.07, chime(n["F#6"], 0.5, 5.0))], 0.6),
        "network-connectivity-lost": sequence([(0, chime(n["F#6"], 0.4, 6.0)),
                                               (0.07, chime(n["B5"], 0.5, 5.0))], 0.6),
        # Volume keys: a tiny tick.
        "audio-volume-change": chime(n["E6"], 0.12, 22.0, 0.5),
        # Screenshots: a soft shutter.
        "screen-capture": noise_click(0.09, 1500, 7000, 55.0) * 0.6 + sequence([(0.03, chime(n["A6"], 0.08, 40.0,
                                                                                            0.3) * 0.25)], 0.09),
        "camera-shutter": noise_click(0.09, 1500, 7000, 55.0) * 0.6,
        # Emptying the bin: a short soft rustle.
        "trash-empty": noise_click(0.35, 400, 4000, 9.0) * 0.5,
    }


def write_wav(path: Path, mono: np.ndarray) -> None:
    peak = float(np.max(np.abs(mono))) or 1.0
    mono = mono / peak * PEAK
    fade = min(len(mono), int(RATE * 0.02))  # never end on a click
    mono[-fade:] *= np.linspace(1, 0, fade)
    # A whisper of width: the right channel a hair later.
    delay = int(RATE * 0.0004)
    right = np.concatenate([np.zeros(delay), mono[:-delay]])
    stereo = np.stack([mono, right], axis=1)
    data = (stereo * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("error: ffmpeg not found", file=sys.stderr)
        return 1
    out = THEME / "stereo"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, sound in sounds().items():
            wav = Path(tmp) / f"{name}.wav"
            write_wav(wav, sound)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libvorbis", "-q:a", "5",
                            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                            str(out / f"{name}.oga")], check=True)
            print(f"  wrote {(out / f'{name}.oga').relative_to(ROOT)}")
    (THEME / "index.theme").write_text(
        "[Sound Theme]\n"
        "Name=Kulan\n"
        "Comment=Soft, short chimes made for Kulan OS\n"
        "Inherits=freedesktop\n"
        "Directories=stereo\n"
        "\n"
        "[stereo]\n"
        "OutputProfile=stereo\n"
    )
    print(f"  wrote {(THEME / 'index.theme').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
