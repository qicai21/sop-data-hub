"""Shared direct HTTP transport for the Jilin recipient portal.

The portal is a fixed business endpoint.  It must not inherit desktop proxy
environment variables: a stopped local proxy previously made valid dispatch
events appear as failed uploads.
"""
from __future__ import annotations

from typing import Any

import requests


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Issue one portal request directly, ignoring ambient HTTP proxy settings."""
    with requests.Session() as session:
        session.trust_env = False
        return session.request(method, url, **kwargs)
