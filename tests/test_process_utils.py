"""Tests for small subprocess lifecycle helpers."""

from __future__ import annotations

import sys

import shared.process_utils as process_utils


def test_parent_death_signal_preexec_fn_is_platform_aware(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert process_utils.parent_death_signal_preexec_fn() is None

    monkeypatch.setattr(sys, "platform", "linux")
    hook = process_utils.parent_death_signal_preexec_fn()
    assert callable(hook)
