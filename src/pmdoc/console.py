"""
console.py — a print() that a caller can redirect, per thread.

The pipeline modules report progress with emit(). Under the CLI it IS print();
when several repo traces run in parallel threads, capture() prefixes each
thread's lines with its repo name so the shared terminal stays readable.

`emit` is a drop-in replacement for print(). With no sink installed it IS
print(). Inside `capture(sink)` every emit in the current thread (contextvars
are per-thread, and per-task under asyncio) goes to `sink` instead, one call per
emitted message with the trailing newline included.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

Sink = Callable[[str], None]

_sink: ContextVar[Sink | None] = ContextVar("console_sink", default=None)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def emit(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = False) -> None:
    sink = _sink.get()
    if sink is None:
        print(*args, sep=sep, end=end, file=file, flush=flush)
        return
    sink(sep.join(str(a) for a in args) + end)


@contextmanager
def capture(sink: Sink) -> Iterator[None]:
    """Route every emit() in this thread/context to `sink` until exit."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)
