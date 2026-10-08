"""Error-code catalog drift gate (EPIC-03 task 3).

Every machine-readable `code` used by the backend must be documented in
docs/error-codes.md: new codes fail here until documented. Scans string
literals (`code="domain:reason"`) plus the outcome-derived `resolve:*`
family (built as f-strings in the resolve router).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlmodel")

ROOT = Path(__file__).resolve().parent.parent

LITERAL_RE = re.compile(r'code="([a-z-]+:[a-z-]+)"')
RESOLVE_OUTCOMES = ("need_params", "not_found", "gone", "forbidden", "unsafe")


def _codes_in_source() -> set[str]:
    codes: set[str] = set()
    for path in (ROOT / "backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        codes.update(LITERAL_RE.findall(text))
    resolve_router = (ROOT / "backend" / "routers" / "resolve.py").read_text(encoding="utf-8")
    if 'code=f"resolve:{' in resolve_router or 'code=f"resolve:' in resolve_router:
        codes.update(f"resolve:{outcome}" for outcome in RESOLVE_OUTCOMES)
    return codes


def test_error_codes_cataloged() -> None:
    catalog = (ROOT / "docs" / "error-codes.md").read_text(encoding="utf-8")
    missing = sorted(code for code in _codes_in_source() if f"`{code}`" not in catalog)
    assert not missing, f"undocumented error codes (add to docs/error-codes.md): {missing}"


def test_catalog_has_no_orphans() -> None:
    """The other direction: no documented codes that code no longer emits."""
    catalog = (ROOT / "docs" / "error-codes.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z-]+:[a-z-]+)`", catalog))
    orphans = sorted(documented - _codes_in_source())
    assert not orphans, f"catalog lists codes the backend never emits: {orphans}"
