"""Console debug logger (visible when launched from .bat / cmd)."""

from __future__ import annotations

import sys
import traceback
from datetime import datetime


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def console_log(message: str, *, level: str = "INFO") -> None:
    """Print a line to stdout for the CMD window."""
    line = f"[{_ts()}] [{level}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def console_error(message: str, exc: BaseException | None = None) -> None:
    console_log(message, level="ERROR")
    if exc is not None:
        console_log(f"{type(exc).__name__}: {exc}", level="ERROR")
        try:
            tb = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            for row in tb.rstrip().splitlines():
                console_log(row, level="TRACE")
        except Exception:
            pass
