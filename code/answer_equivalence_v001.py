#!/usr/bin/env python3
"""Backend answer-equivalence normalizers for free-text benchmark responses.

Free-text items (instrument v0.4, researcher direction of 2026-08-11: no forced
multiple choice; equivalency checked on the back end) are scored by canonical
equality, not string equality. Each free-text item declares a `normalizer`
class in `adjudication.answer_key`; this module maps any response string to a
canonical form for that class. Two responses agree when their canonical forms
match; a response matches gold when its canonical form equals the canonical
form of the gold answer or of any listed alias.

Design rules:
- Deterministic, pure-Python, no locale dependence; the module is part of the
  frozen instrument and is hashed into the run configuration.
- The reserved escape token UNCERTAIN (any case, with or without punctuation)
  canonicalizes to "UNCERTAIN" under every normalizer.
- Unparseable values fall back to aggressive string canonicalization rather
  than raising: scoring then reduces to near-exact match, never a crash.

Run `python answer_equivalence_v001.py --self-test` for the built-in checks.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation

NORMALIZERS = ("money", "number", "percent", "date", "duration", "string", "citation")

_UNCERTAIN = re.compile(r"^\W*uncertain\W*$", re.I)

_WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9,
}

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_UNIT_ALIASES = {
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year",
    "hour": "hour", "hours": "hour", "minute": "minute", "minutes": "minute",
    "business": "business", "calendar": "",
}


def _strip(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value))
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("“", '"').replace("”", '"')
    value = value.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


def _words_to_int(text: str) -> int | None:
    words = [w for w in re.split(r"[\s-]+", text.lower().replace(" and ", " ")) if w]
    if not words:
        return None
    total, chunk = 0, 0
    seen = False
    for w in words:
        if w not in _WORD_NUMS:
            return None
        seen = True
        n = _WORD_NUMS[w]
        if n == 100:
            chunk = max(chunk, 1) * 100
        elif n >= 1000:
            total += max(chunk, 1) * n
            chunk = 0
        else:
            chunk += n
    return total + chunk if seen else None


def _parse_decimal(text: str) -> Decimal | None:
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        word_val = _words_to_int(text)
        return Decimal(word_val) if word_val is not None else None


def _fmt_decimal(value: Decimal) -> str:
    value = value.normalize()
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _scale_suffix(text: str) -> tuple[str, Decimal]:
    scales = [
        (r"\b(million|mm|m)\b", Decimal(10**6)),
        (r"\b(billion|bn|b)\b", Decimal(10**9)),
        (r"\b(thousand|k)\b", Decimal(10**3)),
        (r"\b(hundred)\b", Decimal(100)),
    ]
    for pattern, mult in scales:
        m = re.search(pattern, text, re.I)
        if m:
            return re.sub(pattern, " ", text, flags=re.I), mult
    return text, Decimal(1)


def norm_string(value: str) -> str:
    value = _strip(value)
    if _UNCERTAIN.match(value):
        return "UNCERTAIN"
    value = value.strip(" \t\"'.,;:()[]")
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def norm_money(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text = raw.lower()
    text = re.sub(r"\b(usd|u\.s\.d\.|us dollars|dollars|dollar|bucks)\b", " ", text)
    text = text.replace("$", " ")
    text, mult = _scale_suffix(text)
    text = re.sub(r"[^0-9a-z.,\s-]", " ", text)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    number: Decimal | None = None
    if m:
        number = _parse_decimal(m.group(0))
    else:
        number = _parse_decimal(text)
    if number is None:
        return "money:" + norm_string(raw)
    return "money:" + _fmt_decimal(number * mult)


def norm_number(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text, mult = _scale_suffix(raw.lower())
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    number = _parse_decimal(m.group(0)) if m else _parse_decimal(text)
    if number is None:
        return "number:" + norm_string(raw)
    return "number:" + _fmt_decimal(number * mult)


def norm_percent(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text = raw.lower().replace("percentage points", " ").replace("percent", " ")
    text = text.replace("per cent", " ").replace("%", " ")
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    number = _parse_decimal(m.group(0)) if m else _parse_decimal(text)
    if number is None:
        return "percent:" + norm_string(raw)
    return "percent:" + _fmt_decimal(number)


def norm_date(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text = raw.lower().strip(" .,;")
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if m and m.group(1) in _MONTHS:
        return f"date:{int(m.group(3)):04d}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})\b", text)
    if m and m.group(2) in _MONTHS:
        return f"date:{int(m.group(3)):04d}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        return f"date:{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if m:
        year = int(m.group(3))
        year += 2000 if year < 100 else 0
        return f"date:{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{4})\b", text)
    if m and m.group(1) in _MONTHS:
        return f"date:{int(m.group(2)):04d}-{_MONTHS[m.group(1)]:02d}"
    m = re.search(r"\b(\d{4})\b", text)
    if m:
        return f"date:{int(m.group(1)):04d}"
    return "date:" + norm_string(raw)


def norm_duration(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text = raw.lower()
    text = re.sub(r"\(([^)]*)\)", " ", text)  # drop parenthetical numerals: "five (5) days"
    number: Decimal | None = None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if m:
        number = _parse_decimal(m.group(0))
    else:
        for token in re.findall(r"[a-z][a-z-]*", text):
            candidate = _words_to_int(token)
            if candidate is not None:
                number = Decimal(candidate)
                break
    unit = ""
    business = " business " in f" {text} "
    for token in re.findall(r"[a-z]+", text):
        mapped = _UNIT_ALIASES.get(token)
        if mapped and mapped != "business":
            unit = mapped
            break
    if number is None or not unit:
        return "duration:" + norm_string(raw)
    prefix = "business-" if business and unit == "day" else ""
    return f"duration:{_fmt_decimal(number)}-{prefix}{unit}"


def norm_citation(value: str) -> str:
    raw = _strip(value)
    if _UNCERTAIN.match(raw):
        return "UNCERTAIN"
    text = raw.lower()
    text = text.replace("section", "§").replace("sec.", "§").replace("sect.", "§")
    text = re.sub(r"\s*§\s*", "§", text)
    text = re.sub(r"[\s.,;]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return "cite:" + text


_DISPATCH = {
    "money": norm_money,
    "number": norm_number,
    "percent": norm_percent,
    "date": norm_date,
    "duration": norm_duration,
    "string": norm_string,
    "citation": norm_citation,
}


def canonicalize(value: str, normalizer: str) -> str:
    """Canonical form of `value` under the named normalizer class."""
    if normalizer not in _DISPATCH:
        raise ValueError(f"unknown normalizer: {normalizer!r}")
    return _DISPATCH[normalizer](value)


def equivalent(a: str, b: str, normalizer: str) -> bool:
    return canonicalize(a, normalizer) == canonicalize(b, normalizer)


def matches_gold(value: str, gold: str | None, aliases: list[str], normalizer: str) -> bool:
    targets = ([gold] if gold is not None else []) + list(aliases or [])
    canon = canonicalize(value, normalizer)
    return any(canonicalize(t, normalizer) == canon for t in targets)


SELF_TESTS: list[tuple[str, str, str, bool]] = [
    ("$1,875,000", "1.875 million dollars", "money", True),
    ("$1.875 million", "USD 1875000.00", "money", True),
    ("$1,875,000", "$1,875,500", "money", False),
    ("$45 per day", "$45", "money", True),  # money ignores trailing unit words
    ("6,412", "6412", "number", True),
    ("twenty-four", "24", "number", True),
    ("two thousand", "2,000", "number", True),
    ("25%", "25 percent", "percent", True),
    ("25%", "2.5%", "percent", False),
    ("June 12, 2026", "12 June 2026", "date", True),
    ("June 12, 2026", "6/12/2026", "date", True),
    ("June 12, 2026", "June 13, 2026", "date", False),
    ("thirty (30) days", "30 days", "duration", True),
    ("thirty days", "30 days", "duration", True),
    ("10 business days", "ten business days", "duration", True),
    ("10 business days", "10 days", "duration", False),
    ("two years", "24 months", "duration", False),  # unit-faithful by design
    ("Marcus Fenwick", "marcus fenwick", "string", True),
    ("Letters Testamentary", "letters testamentary.", "string", True),
    ("Rule 60(b)(1)", "rule 60(b)(1)", "citation", True),
    ("Section 998", "§ 998", "citation", True),
    ("UNCERTAIN", "uncertain.", "money", True),
]


def self_test() -> int:
    failures = 0
    for a, b, norm, expected in SELF_TESTS:
        got = equivalent(a, b, norm)
        if got != expected:
            failures += 1
            print(f"FAIL {norm}: {a!r} vs {b!r} -> {got}, expected {expected} "
                  f"({canonicalize(a, norm)} | {canonicalize(b, norm)})", file=sys.stderr)
    print(f"self-test: {len(SELF_TESTS) - failures}/{len(SELF_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    if len(sys.argv) == 4:
        print(canonicalize(sys.argv[1], sys.argv[3]), canonicalize(sys.argv[2], sys.argv[3]))
        raise SystemExit(0 if equivalent(sys.argv[1], sys.argv[2], sys.argv[3]) else 1)
    print(__doc__)
