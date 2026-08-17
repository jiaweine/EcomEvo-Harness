"""Compatibility alias for the modular API implementation.

Keeping ``ecomevo.api.app`` as the canonical import path preserves existing deployments,
tests and monkeypatch hooks while allowing the production implementation to stay split
into maintainable modules.
"""
from __future__ import annotations

import sys
from . import application as _application

sys.modules[__name__] = _application
