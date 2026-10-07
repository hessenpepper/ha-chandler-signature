# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Load the integration's pure-Python modules without importing Home Assistant."""

import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "chandler_signature"

pkg = types.ModuleType("chandler_signature")
pkg.__path__ = [str(PKG_DIR)]
sys.modules.setdefault("chandler_signature", pkg)

