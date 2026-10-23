"""Safe, low-detail summaries for production diagnostics."""

from __future__ import annotations

from urllib.error import HTTPError


def safe_exception_summary(exc: BaseException) -> str:
    """Return a diagnostic label without exposing an exception message or payload."""
    if isinstance(exc, HTTPError):
        return f"{type(exc).__name__}(status={exc.code})"

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return f"{type(exc).__name__}(status={status})"
    return type(exc).__name__
