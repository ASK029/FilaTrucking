"""Reading a shipment out of a WhatsApp message, and telling shipments from chit-chat (QA-051).

Pure functions, no Django imports, so every rule is unit-testable without HTTP.

A shipment needs a booking, a container and a seal. Everything else is optional: a note/description (the first line,
words only), a chassis, and the older optional customer / date / rate / driver / truck / location.

Accepted layouts (all of them may carry blank lines, bullets, WhatsApp *bold* markup, CRLF):

    Klaipeda            note / description (optional, words without numbers)
    95938662            booking
    HAMU3898114         container
    179458              seal
    NAPZ225644          chassis (optional)

    95938662 #booking   tagged lines, in any order (``#note``, ``#description``, ``#chassis`` too)
    BOOKING: 95938662   KEY: value lines (the original format)
    BK1 / CNT1 / SEAL1 / [CUSTOMER] / [DATE]   the original positional format

``parse_message(text).kind`` is one of:
    "shipment"    booking, container and seal were all found;
    "incomplete"  it clearly tries to be a shipment but something required is missing (the driver is told what);
    "ignore"      anything else: chat, questions, links, a lone number. No row is stored, nobody is answered.
"""
import re
from dataclasses import dataclass, field

REQUIRED = ("booking", "container", "seal")

# "KEY: value" lines: key -> field
KEY_FIELDS = {
    "date": "date", "booking": "booking", "container": "container", "seal": "seal", "customer": "customer",
    "rate": "rate", "driver": "driver", "truck": "truck", "location": "location", "chassis": "chassis",
    "note": "note", "notes": "note", "description": "note", "desc": "note",
}
# "value #tag" lines: tag -> field ("note/description" is how the format is documented, so it is accepted literally)
TAG_FIELDS = {
    "booking": "booking", "container": "container", "seal": "seal", "chassis": "chassis",
    "note": "note", "notes": "note", "description": "note", "desc": "note", "note/description": "note",
}

MAX_LINES = 10          # a real shipment message is 3 to ~8 lines; a pasted paragraph is not one
MAX_LINE_CHARS = 120
MAX_SENTENCE_WORDS = 6  # more words than this on one line is a sentence, not a value
NOTE_MAX_WORDS = 6
NOTE_MAX_CHARS = 40

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏⁠﻿"), None)
_BULLET = re.compile(r"^(?:[-*•–·]+\s+|\d{1,2}[.)]\s+)")
_EMPHASIS = re.compile(r"(?<!\w)([*_~`])(\S(?:.*?\S)?)\1(?!\w)")
_URL = re.compile(r"https?://|www\.", re.I)
_TAGS = "|".join(sorted((re.escape(t) for t in TAG_FIELDS), key=len, reverse=True))
_TAG_AFTER = re.compile(rf"^(?P<value>.*?\S)?\s*#(?P<tag>{_TAGS})\b\s*(?:\(.*\))?\s*$", re.I)
_TAG_BEFORE = re.compile(rf"^#(?P<tag>{_TAGS})\b\s*(?P<value>\S.*)$", re.I)
_CONTAINER = re.compile(r"[A-Za-z]{4}\d{7}")  # ISO 6346 shape: 4 letters + 7 digits (check digit not enforced)
_DATE = re.compile(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}")
_VALUE_LIKE = re.compile(r"[\w./-]{3,30}")


@dataclass
class Parsed:
    kind: str = "ignore"                       # "ignore" | "incomplete" | "shipment"
    fields: dict = field(default_factory=dict)  # booking, container, seal, note, chassis, customer, date, rate, ...
    missing: list = field(default_factory=list)  # required fields that were not found


def normalise_lines(text):
    """Non-empty lines without bullets, WhatsApp markup, zero-width characters and surrounding spaces. Lines with no
    letter or digit at all (".", an emoji) are dropped: they are never values."""
    lines = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.translate(_ZERO_WIDTH).replace(" ", " ").strip()
        line = _BULLET.sub("", line).strip()
        line = _EMPHASIS.sub(r"\2", line).strip()
        if any(ch.isalnum() for ch in line):
            lines.append(line)
    return lines


def _compact_container(line):
    """'ABCD 123456 7' / 'abcd-1234567' -> 'ABCD1234567'; any other line is returned unchanged."""
    if len(line) <= 16:
        squeezed = re.sub(r"[ -]", "", line)
        if _CONTAINER.fullmatch(squeezed) and re.fullmatch(r"[A-Za-z0-9 -]+", line):
            return squeezed.upper()
    return line


def _has_digit(text):
    return any(ch.isdigit() for ch in text)


def _is_value_like(token):
    """A single token that could be a booking, container or seal: 3-30 characters with a digit, not a date."""
    return bool(_VALUE_LIKE.fullmatch(token)) and _has_digit(token) and not _DATE.fullmatch(token)


def _is_alphanumeric_code(token):
    return any(ch.isalpha() for ch in token) and _has_digit(token)


def _is_sentence(line):
    return len(line.split()) > MAX_SENTENCE_WORDS or line.endswith(("?", "!"))


def is_note(line):
    """A description line: words only (no digits), short, not a question or an exclamation."""
    return (
        any(ch.isalpha() for ch in line) and not _has_digit(line) and "#" not in line
        and len(line.split()) <= NOTE_MAX_WORDS and len(line) <= NOTE_MAX_CHARS and not _is_sentence(line)
    )


def _is_chassis_like(token):
    # no upper bound here: an over-long chassis must reach the length check and be refused, not vanish silently
    return bool(re.fullmatch(r"[A-Za-z0-9-]{6,}", token)) and _is_alphanumeric_code(token)


def _is_customer_like(text):
    return (
        bool(re.fullmatch(r"[\w .&'/-]{1,60}", text)) and not _has_digit(text)
        and len(text.split()) <= 4 and not _is_sentence(text)
    )


def _find_container(values):
    """Index of the container among untagged lines: the first ISO-shaped token, or the second when two in a row look
    ISO-shaped (a booking number can have that shape, the container comes after it)."""
    hits = [i for i, v in enumerate(values) if _CONTAINER.fullmatch(v)]
    if not hits:
        return None
    if len(hits) > 1 and hits[0] == 0 and hits[1] == 1:
        return hits[1]
    return hits[0]


def parse_message(text):
    parsed = Parsed()
    lines = normalise_lines(text)
    if not lines or len(lines) > MAX_LINES or any(len(ln) > MAX_LINE_CHARS or _URL.search(ln) for ln in lines):
        return parsed

    explicit, untagged, keys_seen = {}, [], 0
    for line in lines:
        tagged = _TAG_AFTER.match(line) or _TAG_BEFORE.match(line)
        if tagged:
            keys_seen += 1
            if tagged.group("value"):
                explicit.setdefault(TAG_FIELDS[tagged.group("tag").lower()], tagged.group("value").strip())
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            name = KEY_FIELDS.get(key.strip().lower())
            if name:
                keys_seen += 1
                if value.strip():
                    explicit.setdefault(name, value.strip())
            continue  # a ':' line with an unknown key ("Note to self: ...") is never a value
        untagged.append(_compact_container(line))

    fields = dict(explicit)
    need = [f for f in REQUIRED if f not in fields]
    values = list(untagged)
    used = []     # untagged lines taken as booking/container/seal
    extras = []   # what follows the seal: chassis / customer / date

    if need and values:
        anchor = _find_container(values) if "container" in need else None
        if anchor is not None:
            start = anchor - 1 if "booking" in need else anchor
            if start >= 0:
                if "booking" in need:
                    fields["booking"] = values[anchor - 1]
                    used.append(values[anchor - 1])
                fields["container"] = values[anchor]
                used.append(values[anchor])
                after = values[anchor + 1:]
                if "seal" in need and after:
                    fields["seal"] = after.pop(0)
                    used.append(fields["seal"])
                extras = after
                leading = values[:max(start, 0)]
                if "note" not in fields and leading and is_note(leading[0]):
                    fields["note"] = leading[0]
            else:  # the container is the first line: no booking before it
                fields["container"] = values[anchor]
                used.append(values[anchor])
                after = values[anchor + 1:]
                if "seal" in need and after:
                    fields["seal"] = after.pop(0)
                    used.append(fields["seal"])
                extras = after
        else:
            if "note" not in fields and is_note(values[0]) and len(values) - 1 >= len(need):
                fields["note"] = values.pop(0)
            for name in need:
                if values:
                    fields[name] = values.pop(0)
                    used.append(fields[name])
            extras = values
    else:
        if values and "note" not in fields and is_note(values[0]):
            fields["note"] = values.pop(0)
        extras = values

    # Required values taken from bare lines must look like values, or the message is chat that happens to have
    # several short lines ("ok" / "thanks" / "boss"): at least two of them need a digit and a plausible length.
    # A container is always letters + digits, so three bare numbers ("3125550101", "1500", "20") are not a shipment.
    if used:
        if (
            sum(_is_value_like(u) for u in used) < min(2, len(used))
            or not any(_is_alphanumeric_code(u) for u in used)
            or any(len(u.split()) > 1 for u in used)
        ):
            for name in need:
                fields.pop(name, None)
            if "note" not in explicit:
                fields.pop("note", None)
            extras = []

    for token in extras:
        token = token.strip()
        if _DATE.fullmatch(token):
            fields.setdefault("date", token)
        elif _is_chassis_like(token):
            fields.setdefault("chassis", token)
        elif _is_customer_like(token):
            fields.setdefault("customer", token)

    for name in ("container", "chassis"):
        if name in fields and (name == "chassis" or _CONTAINER.fullmatch(fields[name])):
            fields[name] = fields[name].upper()

    parsed.fields = {k: v for k, v in fields.items() if v}
    parsed.missing = [f for f in REQUIRED if f not in parsed.fields]
    if not parsed.missing:
        parsed.kind = "shipment"
        return parsed

    value_like = [t for t in untagged if _is_value_like(t)]
    looks_like_an_attempt = keys_seen >= 2 or (
        len(value_like) >= 2 and (len(value_like) >= 3 or any(_is_alphanumeric_code(t) for t in value_like))
    )
    if looks_like_an_attempt and not any(_is_sentence(t) for t in untagged):
        parsed.kind = "incomplete"
    return parsed
