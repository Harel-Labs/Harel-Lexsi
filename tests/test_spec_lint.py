"""S2: split by heading, lint, and refuse a planted bad spec."""
from pathlib import Path

import pytest

from harel_lexsi.spec import lint_spec, split_spec
from harel_lexsi.spec.__main__ import main

SPECS = Path(__file__).parent / "fixtures" / "specs"
REAL_SPEC = Path(__file__).parents[1] / "spec" / "spec.md"


def lint(name):
    return lint_spec((SPECS / name).read_text())


def test_good_spec_passes_and_loads_its_contracts():
    r = lint("good_spec.md")
    assert r.ok, r.errors
    assert [c.id for c in r.contracts] == ["X-01"]


def test_split_is_by_heading_only():
    parts = split_spec((SPECS / "good_spec.md").read_text())
    assert parts.build.startswith("## Build") and "X-01" not in parts.build
    assert "X-01" in parts.holdout and "wallets" in parts.resources and not parts.missing


@pytest.mark.parametrize("name, fragment", [
    ("bad_unknown_resource.md", "not in Resources"),
    ("bad_contested_untouched.md", "contested resource 'wallets' has no contract"),
    ("bad_quotes_holdout_id.md", "quotes holdout contract id X-01"),
    ("bad_unknown_field.md", "unknown field"),
    ("bad_missing_holdout.md", "missing section: ## Holdout"),
])
def test_planted_bad_spec_is_rejected(name, fragment):
    r = lint(name)
    assert not r.ok and any(fragment in e for e in r.errors), r.errors


def test_cli_writes_split_files_only_when_clean(tmp_path):
    out = tmp_path / "out"
    assert main([str(SPECS / "good_spec.md"), "--out", str(out)]) == 0
    assert (out / "build.md").read_text().startswith("## Build")
    assert "X-01" in (out / "holdout.yaml").read_text() and "X-01" not in (out / "build.md").read_text()
    bad = tmp_path / "bad"
    assert main([str(SPECS / "bad_unknown_resource.md"), "--out", str(bad)]) == 1
    assert not bad.exists()


def test_real_spec_fails_until_the_team_writes_the_holdout():
    """S1 is hand-written. Until it is, the linter must say so rather than pass."""
    r = lint_spec(REAL_SPEC.read_text())
    assert not r.ok or r.contracts
    if not r.contracts:
        assert any("contested resource" in e for e in r.errors)
