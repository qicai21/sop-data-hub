"""Live (machine-local) tests — second gate after path selection.

Requires SOP_TEST_LIVE=1. Never enables WeChat send or portal submit.
Never opens production DBs for write (readonly URI only).
"""
from __future__ import annotations

import os

import pytest

if os.environ.get("SOP_TEST_LIVE") != "1":
    pytest.skip(
        "live tests require SOP_TEST_LIVE=1 (make test-live-readonly)",
        allow_module_level=True,
    )
