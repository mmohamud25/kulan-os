"""Dictation: speech to text on this computer (whisper.cpp).

Kulan Shell records while Shift+Super+D is held on (pressed once to start,
again to stop), then runs `kulan-assistant dictate FILE` and types the
result where the cursor is. The recording is deleted straight after.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import engine

WHISPER = Path("/usr/lib/kulan-assistant/whisper/whisper-cli")
TIMEOUT_S = 300
# Whisper writes these for silence or noise instead of nothing.
NOISE = re.compile(r"^\s*[\[(]\s*(blank_audio|silence|music|noise|inaudible|no speech)\s*[\])]\s*$", re.I)


def language() -> str:
    """'auto', or a two-letter code chosen in Kulan Settings > Assistant."""
    chosen = engine._setting("dictation-language", "")  # noqa: SLF001
    return chosen if re.fullmatch(r"[a-z]{2,3}", chosen or "") else "auto"


def transcribe(audio: Path) -> str:
    if not WHISPER.exists():
        raise engine.EngineError("Kulan Assistant isn't fully installed. Turn it on in Kulan Settings › Add-ons.")
    model_id = engine.usable_model("dictation")
    if model_id is None:
        raise engine.EngineError("No dictation model yet. Download one in Kulan Settings › Assistant.")
    threads = max(1, min(8, (os.cpu_count() or 2) - 1))
    out = subprocess.run([str(WHISPER), "-m", str(engine.model_path(model_id)), "-f", str(audio), "-l", language(),
                          "-t", str(threads), "-nt", "-np"],
                         capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    if out.returncode != 0:
        raise engine.EngineError("Dictation couldn't read the recording.")
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip() and not NOISE.match(line)]
    return " ".join(lines).strip()
