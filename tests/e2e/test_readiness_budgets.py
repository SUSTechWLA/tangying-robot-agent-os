"""Readiness budgets must stay large enough for the slowest supported runner.

CI boots MuJoCo with software rendering (MUJOCO_GL=osmesa) on two cores, which
takes several times longer than a developer machine. A readiness wait that is too
short fails healthy stacks under load, which reads as a product failure and hides
real ones. These tests pin the budgets and their override so the lesson is not
lost the next time someone tightens a timeout.
"""

from __future__ import annotations

import importlib
import os

import pytest

from tests.e2e import helpers


def test_readiness_budgets_are_generous_enough_for_a_slow_runner():
    assert helpers.STARTUP_TIMEOUT_S >= 60.0, (
        "a 20 s startup budget failed healthy rgbd stacks on a GitHub runner; "
        "readiness waits bound broken stacks, not slow ones"
    )
    assert helpers.LIFECYCLE_TIMEOUT_S >= 60.0, (
        "starting or stopping the stack on a slow runner needs more than the "
        "35 s that used to be hard-coded here"
    )


def test_readiness_budgets_can_be_tuned_without_editing_code(monkeypatch):
    monkeypatch.setenv("TANGYING_E2E_STARTUP_TIMEOUT_S", "150")
    monkeypatch.setenv("TANGYING_E2E_LIFECYCLE_TIMEOUT_S", "240")
    reloaded = importlib.reload(helpers)
    try:
        assert reloaded.STARTUP_TIMEOUT_S == 150.0
        assert reloaded.LIFECYCLE_TIMEOUT_S == 240.0
    finally:
        os.environ.pop("TANGYING_E2E_STARTUP_TIMEOUT_S", None)
        os.environ.pop("TANGYING_E2E_LIFECYCLE_TIMEOUT_S", None)
        importlib.reload(helpers)


@pytest.mark.parametrize("value", ["", "not-a-number", "0", "-5"])
def test_invalid_budget_falls_back_to_the_safe_default(monkeypatch, value):
    monkeypatch.setenv("TANGYING_E2E_STARTUP_TIMEOUT_S", value)
    reloaded = importlib.reload(helpers)
    try:
        assert reloaded.STARTUP_TIMEOUT_S >= 60.0
    finally:
        os.environ.pop("TANGYING_E2E_STARTUP_TIMEOUT_S", None)
        importlib.reload(helpers)
