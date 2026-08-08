"""Sanity: live package only collects when SOP_TEST_LIVE=1."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def test_live_env_enabled():
    assert os.environ.get("SOP_TEST_LIVE") == "1"
