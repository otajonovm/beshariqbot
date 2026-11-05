"""Matnni normalizatsiya qilish va yo'lovchi so'rovini baholash.

Klassifikatsiya to'liq sinxron: tarmoq va baza yo'q. Regexlar import paytida
bir marta yig'iladi, /addword dan keyin qayta kompilyatsiya qilinadi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bot.relay.keywords import (
    DRIVER_PHRASES,
    DRIVER_REGEX,
    MAX_CHARS,
    MIN_SCORE,
    PASSENGER_PHRASES,
    PASSENGER_REGEX,
)

_APOSTROPHE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u02bb": "'",
        "\u02bc": "'",
        "`": "'",
        "\u00b4": "'",
        "\u2032": "'",
        "\u02b9": "'",
        "\uff07": "'",
    }
)

# Kirill (o'zbek + rus) → lotin. ў/ғ apostrof bilan chiqadi, keyin olib tashlanadi.
_CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "x",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "",
    "ы": "i",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ў": "o'",
    "ғ": "g'",
    "қ": "q",
    "ҳ": "h",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_URL = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<!\d)\+?\s*998[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)"
    r"|(?<!\d)\d{2}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}(?!\d)"
    r"|(?<!\d)(?:9\d|88|33|77|50|55|20)\d{7}(?!\d)"
)
_PASSENGER_REGEX = [(re.compile(pattern), weight) for pattern, weight in PASSENGER_REGEX]
_DRIVER_REGEX = [(re.compile(pattern), weight) for pattern, weight in DRIVER_REGEX]

_extra_passenger: list[tuple[str, int]] = []
_extra_driver: list[tuple[str, int]] = []
_disabled: set[str] = set()
_passenger: tuple[re.Pattern[str] | None, dict[str, int]] = (None, {})
_driver: tuple[re.Pattern[str] | None, dict[str, int]] = (None, {})


@dataclass(frozen=True, slots=True)
class Decision:
    accept: bool
    passenger: int
    driver: int
    normalized: str
    phones: tuple[str, ...]
    reason: str


def normalize(text: str) -> str:
    """Kichik harf, kirill→lotin, apostrof turlari bir xil, ortiqcha belgilar tushadi."""
    lowered = text.lower().translate(_APOSTROPHE)
    chars: list[str] = []
    for ch in lowered:
        mapped = _CYR.get(ch)
        chars.append(ch if mapped is None else mapped)
    folded = "".join(chars).replace("'", "")
    return _NON_ALNUM.sub(" ", folded).strip()


def extract_phones(text: str) -> list[str]:
    found: list[str] = []
    for match in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if digits.startswith("998") and len(digits) == 12:
            canon = "+" + digits
        elif len(digits) == 9:
            canon = "+998" + digits
        else:
            continue
        if canon not in found:
            found.append(canon)
        if len(found) == 3:
            break
    return found


def _compile(pairs: list[tuple[str, int]]) -> tuple[re.Pattern[str] | None, dict[str, int]]:
    weights: dict[str, int] = {}
    for phrase, weight in pairs:
        norm = normalize(phrase)
        if not norm or norm in _disabled:
            continue
        weights[norm] = max(weight, weights.get(norm, 0))
    if not weights:
        return None, {}
    ordered = sorted(weights, key=lambda item: (-len(item), item))
    parts = [r"\b" + re.escape(item).replace(r"\ ", r"\s+") + r"\b" for item in ordered]
    return re.compile("|".join(parts)), weights


def rebuild() -> None:
    """Builtin + admin qatlami. Chaqiruv sinxron, pattern almashtirish atomar."""
    global _passenger, _driver
    _passenger = _compile(PASSENGER_PHRASES + _extra_passenger)
    _driver = _compile(DRIVER_PHRASES + _extra_driver)


def load_overrides(rows: list[tuple[str, str, int]]) -> None:
    """Bazadagi qo'shimcha so'zlar. kind: passenger | driver | off."""
    global _extra_passenger, _extra_driver, _disabled
    _extra_passenger = []
    _extra_driver = []
    _disabled = set()
    for kind, phrase, weight in rows:
        norm = normalize(phrase)
        if not norm:
            continue
        if kind == "off":
            _disabled.add(norm)
        elif kind == "driver":
            _extra_driver.append((norm, weight))
        elif kind == "passenger":
            _extra_passenger.append((norm, weight))
    rebuild()


def upsert_extra(kind: str, phrase: str, weight: int) -> str:
    global _extra_passenger, _extra_driver
    norm = normalize(phrase)
    _disabled.discard(norm)
    _extra_passenger = [(item, score) for item, score in _extra_passenger if item != norm]
    _extra_driver = [(item, score) for item, score in _extra_driver if item != norm]
    if kind == "driver":
        _extra_driver.append((norm, weight))
    else:
        _extra_passenger.append((norm, weight))
    rebuild()
    return norm


def mark_disabled(phrase: str) -> bool:
    global _extra_passenger, _extra_driver
    norm = normalize(phrase)
    was_active = norm in _passenger[1] or norm in _driver[1]
    _disabled.add(norm)
    _extra_passenger = [(item, score) for item, score in _extra_passenger if item != norm]
    _extra_driver = [(item, score) for item, score in _extra_driver if item != norm]
    rebuild()
    return was_active


def _sum_literals(pattern: re.Pattern[str] | None, weights: dict[str, int], text: str) -> int:
    if pattern is None or not text:
        return 0
    total = 0
    for match in pattern.finditer(text):
        total += weights.get(match.group(0), 0)
    return total


def _sum_regex(rules: list[tuple[re.Pattern[str], int]], text: str) -> int:
    total = 0
    for pattern, weight in rules:
        if pattern.search(text):
            total += weight
    return total


def classify(
    text: str,
    *,
    max_chars: int = MAX_CHARS,
    min_score: int = MIN_SCORE,
) -> Decision:
    raw = text or ""
    phones = tuple(extract_phones(raw))
    stripped = raw.strip()
    if not stripped:
        return Decision(False, 0, 0, "", phones, "empty")
    if stripped.startswith("/"):
        return Decision(False, 0, 0, "", phones, "command")
    if len(raw) > max_chars:
        return Decision(False, 0, 0, "", phones, "too-long")
    if _URL.search(raw):
        return Decision(False, 0, 0, "", phones, "url")
    norm = normalize(raw)
    if not norm:
        return Decision(False, 0, 0, "", phones, "empty")
    passenger_re, passenger_w = _passenger
    driver_re, driver_w = _driver
    passenger = _sum_literals(passenger_re, passenger_w, norm) + _sum_regex(_PASSENGER_REGEX, norm)
    driver = _sum_literals(driver_re, driver_w, norm) + _sum_regex(_DRIVER_REGEX, norm)
    accept = passenger >= min_score and passenger > driver
    return Decision(accept, passenger, driver, norm, phones, "ok" if accept else "score")


rebuild()
