"""Shared API-layer helpers: bounded pagination and date normalisation.

Pagination
----------
List endpoints previously used ``.to_list(1000)`` -- a hard 1000-row ceiling
that both *silently truncated* larger result sets and let a single request
materialise up to 1000 full documents. ``resolve_pagination`` replaces it with
explicit, bounded ``limit``/``offset`` semantics so callers control the page
and the server controls the ceiling.

Date inputs
-----------
``expiryDate``/``createdAt`` filters arrive as unstructured strings from the
query string. ``normalize_date_bound`` accepts either a bare date
(``2026-09-30``) or a full ISO timestamp (``2026-09-30T12:00:00Z``) and returns
a comparable value for the field being filtered, so a "created before X" filter
cannot accidentally exclude everything from the day of X.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Query, status

#: Hard server-side ceiling for any single list response.
MAX_PAGE_SIZE = 200
#: Default page size when the caller does not specify one.
DEFAULT_PAGE_SIZE = 50


def resolve_pagination(
    limit: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum number of records to return.",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Number of records to skip before returning results.",
    ),
) -> tuple[int, int]:
    """Validate and return ``(limit, offset)``, clamped to the server ceiling."""
    return limit, offset


def _parse_iso(value: str, *, end_of_day: bool) -> str:
    """Return ``value`` normalised for comparison against stored ISO strings.

    * A bare date is widened to the start (``00:00:00``) or end
      (``23:59:59.999999``) of that UTC day, so range filters are inclusive.
    * A full timestamp is returned unchanged (normalised to ISO-8601).
    """
    candidate = value.strip()
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date filter must not be empty",
        )

    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        try:
            day = datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date filter. Expected YYYY-MM-DD or an ISO-8601 timestamp.",
            )
        if end_of_day:
            return day.replace(
                hour=23, minute=59, second=59, microsecond=999999
            ).replace(tzinfo=timezone.utc).isoformat()
        return day.replace(tzinfo=timezone.utc).isoformat()

    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date filter. Expected YYYY-MM-DD or an ISO-8601 timestamp.",
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def normalize_date_bound(value: Optional[str], field: str, *, is_upper_bound: bool) -> Optional[str]:
    """Normalise a date-only or ISO timestamp filter for ``field``.

    ``expiryDate`` is stored as a date-only string (``YYYY-MM-DD``) whereas
    ``createdAt``/``updatedAt`` are stored as full ISO timestamps, so the two
    must be compared differently.
    """
    if not value:
        return None

    if field in ("expiryDate", "effectiveDate"):
        parsed = _parse_iso(value, end_of_day=False)
        return parsed[:10]

    return _parse_iso(value, end_of_day=is_upper_bound)
