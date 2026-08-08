"""Tests for scripts/ses_groups.py, using a small synthetic .ses snippet
shaped like the real HyperMesh-exported format (including the line-
continuation convention that has an easy-to-get-wrong quote-stripping
gotcha -- see ses_groups.py's module docstring)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ses_groups import parse_ses_groups  # noqa: E402

# Mimics the real file's structure: a single-line group (LUMPED_MASS), a
# group split across a continuation (RIBS, using both individual IDs and an
# a:b range), and a second, separate group (SKIN) to confirm groups don't
# bleed into each other.
SAMPLE_SES = (
    '$# Started creation of session file sample.ses\n'
    '$# Exported by: Altair HyperMesh 12.0.0.85\n'
    'ga_group_create( "LUMPED_MASS" ) \n'
    'ga_group_entity_add( "LUMPED_MASS", " Element 100 101" )\n'
    'ga_group_create( "RIBS" ) \n'
    'ga_group_entity_add( "RIBS", " Element 1 2 3" // @\n'
    '" 10:12 20" )\n'
    'ga_group_create( "SKIN" ) \n'
    'ga_group_entity_add( "SKIN", " Element 500:502" )\n'
)


@pytest.fixture
def ses_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.ses"
    path.write_text(SAMPLE_SES)
    return path


def test_parse_ses_groups_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        parse_ses_groups(tmp_path / "does_not_exist.ses")


def test_parse_ses_groups_single_line(ses_path: Path):
    groups = parse_ses_groups(ses_path)
    assert groups["LUMPED_MASS"] == {100, 101}


def test_parse_ses_groups_continuation_and_ranges(ses_path: Path):
    """The continuation join must not leave stray characters (e.g. an
    empty "" pair) that would corrupt the element-ID list -- this is
    exactly the bug hit and fixed during development (see module
    docstring)."""
    groups = parse_ses_groups(ses_path)
    assert groups["RIBS"] == {1, 2, 3, 10, 11, 12, 20}


def test_parse_ses_groups_does_not_mix_groups(ses_path: Path):
    groups = parse_ses_groups(ses_path)
    assert groups["SKIN"] == {500, 501, 502}
    assert groups.keys() == {"LUMPED_MASS", "RIBS", "SKIN"}
