"""Small subprocess helpers for runtime-managed child processes."""

from __future__ import annotations

from collections.abc import Callable
import signal
import sys


def parent_death_signal_preexec_fn() -> Callable[[], None] | None:
    """Return a Linux-only preexec hook that terminates children with the parent."""

    if not sys.platform.startswith("linux"):
        return None

    def _set_parent_death_signal() -> None:
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6")
            pr_set_pdeathsig = 1
            libc.prctl(pr_set_pdeathsig, signal.SIGTERM)
        except Exception:
            return

    return _set_parent_death_signal
