"""Markers for tests that rely on the operating system enforcing permission bits."""

from __future__ import annotations

import os

import pytest

ROOT_EUID = 0

requires_enforced_permissions = pytest.mark.skipif(
    os.geteuid() == ROOT_EUID, reason="root bypasses directory permission bits"
)
