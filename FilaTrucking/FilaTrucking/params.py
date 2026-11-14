"""Validated reading of query-string parameters (QA-036): garbage in the URL is a 400, never a 500."""
from django.core.exceptions import BadRequest
from django.utils.dateparse import parse_date


def int_param(request, name, default, minimum, maximum):
    raw = request.GET.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a whole number")
    if not minimum <= value <= maximum:
        raise BadRequest(f"'{name}' must be between {minimum} and {maximum}")
    return value


def date_param(request, name):
    """A YYYY-MM-DD date, or '' when the parameter is absent/empty."""
    raw = (request.GET.get(name) or "").strip()
    if not raw:
        return ""
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise BadRequest(f"'{name}' must be a date like 2026-01-31")
    return raw
