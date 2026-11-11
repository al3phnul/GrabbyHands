# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Text recognition with the Tesseract command-line tool.

Screen text is small (about 96 dpi), so we upscale before recognition; that
improves accuracy a lot. Recognized text is never logged.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
from dataclasses import dataclass

from PIL import Image, ImageOps

TIMEOUT = 90


@dataclass(frozen=True)
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    block: int
    par: int
    line: int


def available() -> bool:
    return shutil.which("tesseract") is not None


def languages() -> list[str]:
    if not available():
        return []
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return sorted(
        line.strip() for line in out.splitlines()[1:] if line.strip() and line.strip() != "osd"
    )


def friendly_error(error: str) -> str:
    """A readable message for a failed read (``error`` is Tesseract's own last line)."""
    lowered = error.lower()
    if "language" in lowered or "traineddata" in lowered:
        return _("Couldn't read the text. Check the language in Settings → Text.")
    return _("Couldn't read the text: {error}").format(error=error)


def _prepare(image: Image.Image) -> tuple[Image.Image, float]:
    gray = ImageOps.grayscale(image.convert("RGB"))
    longest = max(gray.size)
    factor = 3.0 if longest < 600 else 2.0 if longest < 2600 else 1.0
    if factor > 1:
        gray = gray.resize(
            (round(gray.width * factor), round(gray.height * factor)), Image.Resampling.LANCZOS
        )
    # Tesseract prefers dark text on a light background.
    if gray.resize((1, 1)).getpixel((0, 0)) < 110:
        gray = ImageOps.invert(gray)
    return gray, factor


def _run(image: Image.Image, language: str, *extra: str) -> str:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    cmd = ["tesseract", "stdin", "stdout", "-l", language or "eng", "--psm", "3", *extra]
    try:
        proc = subprocess.run(
            cmd, input=buf.getvalue(), capture_output=True, timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as err:
        # (the exception's own text is the whole command line: no use to anyone)
        raise RuntimeError(f"timed out after {TIMEOUT} seconds") from err
    if proc.returncode != 0:
        raise RuntimeError(_failure_reason(proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace")


def _failure_reason(stderr: str) -> str:
    """The line of Tesseract's error output that says what went wrong.

    Its last line is usually just "Could not initialize tesseract."; when a language is
    missing, the line that says so comes earlier.
    """
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if "language" in lowered or "traineddata" in lowered:
            return line
    return lines[-1] if lines else "tesseract failed"


def read_text(image: Image.Image, language: str = "eng") -> str:
    prepared, _factor = _prepare(image)
    text = _run(prepared, language)
    lines = [line.rstrip() for line in text.replace("\f", "").splitlines()]
    # collapse runs of blank lines
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def read_words(image: Image.Image, language: str = "eng", min_conf: float = 30) -> list[Word]:
    """Words with bounding boxes in the original image's pixel coordinates."""
    prepared, factor = _prepare(image)
    tsv = _run(prepared, language, "tsv")
    words: list[Word] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", -1))
        except (TypeError, ValueError):
            continue
        if not text or conf < min_conf:
            continue
        try:
            word = Word(
                text=text,
                left=round(int(row["left"]) / factor),
                top=round(int(row["top"]) / factor),
                width=round(int(row["width"]) / factor),
                height=round(int(row["height"]) / factor),
                conf=conf,
                block=int(row["block_num"]),
                par=int(row["par_num"]),
                line=int(row["line_num"]),
            )
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row: skip it, keep the rest
        words.append(word)
    return words


def join_words(words: list[Word]) -> str:
    """Words (in reading order) back to text: spaces within a line, newlines between lines,
    a blank line between paragraphs."""
    out: list[str] = []
    prev: Word | None = None
    for word in words:
        if prev is not None:
            if (word.block, word.par) != (prev.block, prev.par):
                out.append("\n\n")
            elif word.line != prev.line:
                out.append("\n")
            else:
                out.append(" ")
        out.append(word.text)
        prev = word
    return "".join(out)
