# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Auto-redact: find sensitive text in OCR results.

Works on OCR word boxes. Each line is rebuilt as a string so patterns that span several
words (``4111 1111 1111 1111``, ``(555) 123-4567``) are found, then the matching words' boxes
are merged. Nothing found here is ever logged.
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections import Counter
from dataclasses import dataclass

from grabbyhands.i18n import ngettext
from grabbyhands.ocr import Word

KINDS = ("email", "phone", "card", "ssn", "ip", "secret")


def kind_label(kind: str, n: int) -> str:
    labels = {
        "email": ngettext("{n} email", "{n} emails", n),
        "phone": ngettext("{n} phone number", "{n} phone numbers", n),
        "card": ngettext("{n} card number", "{n} card numbers", n),
        "ssn": ngettext("{n} ID number", "{n} ID numbers", n),
        "ip": ngettext("{n} IP address", "{n} IP addresses", n),
        "secret": ngettext("{n} key or token", "{n} keys or tokens", n),
    }
    return labels[kind].format(n=n)


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# 13-19 digits, optionally grouped by single spaces or dashes
CARD = re.compile(r"(?<![\d])(?:\d[ -]?){12,18}\d(?![\d])")
SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{3,4}[ .-]?\d{3,4}(?![\w])"
)
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
SECRET_PREFIXES = re.compile(
    r"(?:ghp_|gho_|ghs_|ghu_|github_pat_|glpat-|sk-|sk_live_|pk_live_|rk_live_|xox[abpr]-|AKIA|"
    r"ASIA|AIza|ya29\.|eyJ)[A-Za-z0-9_\-./+=]{8,}"
)
LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")


@dataclass(frozen=True)
class Finding:
    kind: str
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in image pixels


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _entropy(text: str) -> float:
    counts = Counter(text)
    n = len(text)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_like_secret(token: str) -> bool:
    if SECRET_PREFIXES.fullmatch(token):
        return True
    if not LONG_TOKEN.fullmatch(token):
        return False
    classes = sum(
        (
            any(c.islower() for c in token),
            any(c.isupper() for c in token),
            any(c.isdigit() for c in token),
        )
    )
    return classes >= 3 and _entropy(token) >= 3.8


def _valid(kind: str, text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    if kind == "card":
        return 13 <= len(digits) <= 19 and luhn_ok(digits) and len(set(digits)) > 1
    if kind == "phone":
        return 9 <= len(digits) <= 15
    if kind == "ip":
        try:
            addr = ipaddress.IPv4Address(text)
        except ValueError:
            return False
        return not (addr.is_unspecified or str(addr).startswith("255."))
    return True


def _line_matches(line: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) spans, most specific first; spans never overlap."""
    found: list[tuple[str, int, int]] = []
    # Card-length digit runs that fail the checksum aren't cards, and pieces of them aren't
    # phone numbers either.
    blocked = [
        ("", m.start(), m.end())
        for m in CARD.finditer(line)
        if not _valid("card", m.group(0).strip(" -"))
    ]

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for _k, s, e in found + blocked)

    for kind, pattern in (
        ("email", EMAIL),
        ("secret", SECRET_PREFIXES),
        ("card", CARD),
        ("ssn", SSN),
        ("ip", IPV4),
        ("phone", PHONE),
    ):
        for m in pattern.finditer(line):
            text = m.group(0).strip(" -.")
            start = m.start() + m.group(0).index(text)
            end = start + len(text)
            if _valid(kind, text) and free(start, end):
                found.append((kind, start, end))
    for m in re.finditer(r"\S+", line):
        token = m.group(0).strip(".,;:()[]{}<>\"'")
        start = m.start() + m.group(0).find(token)
        end = start + len(token)
        if token and free(start, end) and _looks_like_secret(token):
            found.append(("secret", start, end))
    return found


def find_sensitive(words: list[Word], pad: int = 3) -> list[Finding]:
    """Boxes to redact, one per match (merged across the words it spans)."""
    lines: dict[tuple[int, int, int], list[Word]] = {}
    for w in words:
        lines.setdefault((w.block, w.par, w.line), []).append(w)

    findings: list[Finding] = []
    for line_words in lines.values():
        text = ""
        spans: list[tuple[int, int, Word]] = []
        for w in line_words:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(w.text), w))
            text += w.text
        for kind, start, end in _line_matches(text):
            hit = [w for s, e, w in spans if s < end and e > start]
            if not hit:
                continue
            x0 = min(w.left for w in hit) - pad
            y0 = min(w.top for w in hit) - pad
            x1 = max(w.left + w.width for w in hit) + pad
            y1 = max(w.top + w.height for w in hit) + pad
            findings.append(Finding(kind, (max(0, x0), max(0, y0), x1, y1)))
    return findings


def summary(findings: list[Finding]) -> str:
    """'2 emails and 1 phone number', for a toast."""
    counts = Counter(f.kind for f in findings)
    parts = [kind_label(k, counts[k]) for k in KINDS if counts.get(k)]
    if len(parts) <= 1:
        return "".join(parts)
    return _("{items} and {last}").format(items=", ".join(parts[:-1]), last=parts[-1])
