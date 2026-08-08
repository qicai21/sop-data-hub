"""Phase 4: declared install dependencies are importable in portable envs."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_core_third_party_imports():
    import cryptography  # noqa: F401
    import openpyxl  # noqa: F401
    import yaml  # noqa: F401
    from PIL import Image  # noqa: F401
    import requests  # noqa: F401
    import docx  # noqa: F401

    assert yaml.safe_load("a: 1")["a"] == 1
