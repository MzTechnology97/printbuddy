"""safe_join_under / assert_under (ported from Bambuddy 0.2.4.5)."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.utils.safe_path import PathTraversalError, assert_under, safe_join_under


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    lib.mkdir()
    return lib


def test_clean_joins_resolve_under_parent(library: Path):
    assert safe_join_under(library, "model.3mf") == (library / "model.3mf").resolve()
    assert safe_join_under(library, "a", "b", "c.3mf", http=False) == (library / "a" / "b" / "c.3mf").resolve()


@pytest.mark.parametrize(
    "parts",
    [
        (),
        ("",),
        (42,),
        ("evil\x00.3mf",),
        ("/etc/passwd",),
        (r"\\server\share\x",),
        ("..", "etc", "passwd"),
        ("foo", "..", "..", "etc"),
        ("foo/../../etc",),
    ],
)
def test_escapes_are_refused(library: Path, parts):
    with pytest.raises(HTTPException) as exc:
        safe_join_under(library, *parts)
    assert exc.value.status_code == 400
    with pytest.raises(PathTraversalError):
        safe_join_under(library, *parts, http=False)


def test_assert_under(library: Path, tmp_path: Path):
    assert assert_under(library, library / "x" / "y.txt") == (library / "x" / "y.txt").resolve()
    with pytest.raises(PathTraversalError):
        assert_under(library, tmp_path / "elsewhere.txt", http=False)
