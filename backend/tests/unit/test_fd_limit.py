"""Startup RLIMIT_NOFILE raise (port of upstream #2883)."""

import builtins
import sys
from types import SimpleNamespace

import pytest

from backend.app.core.fd_limit import raise_open_file_limit

INF = -1


def _fake_resource(soft, hard, refuse_above=None):
    calls = []

    def setrlimit(_which, limits):
        if refuse_above is not None and limits[0] > refuse_above:
            raise ValueError("not allowed")
        calls.append(limits)

    module = SimpleNamespace(
        RLIMIT_NOFILE=7,
        RLIM_INFINITY=INF,
        getrlimit=lambda _which: (soft, hard),
        setrlimit=setrlimit,
    )
    return module, calls


@pytest.mark.parametrize(
    ("soft", "hard", "refuse_above", "expected", "expected_calls"),
    [
        (1024, 1048576, None, 1048576, [(1048576, 1048576)]),  # Docker default: raise to hard
        (1024, INF, None, 65536, [(65536, INF)]),  # unlimited hard: finite target
        (1024, INF, 10240, 10240, [(10240, INF)]),  # macOS: falls back to 10240
        (4096, 4096, None, 4096, []),  # already at hard: nothing to do
        (INF, INF, None, INF, []),  # unlimited soft is never lowered
        (1024, 2048, 1024, 1024, []),  # refused: logged, startup carries on
    ],
)
def test_raises_soft_limit_without_ever_lowering_it(monkeypatch, soft, hard, refuse_above, expected, expected_calls):
    module, calls = _fake_resource(soft, hard, refuse_above)
    monkeypatch.setitem(sys.modules, "resource", module)
    assert raise_open_file_limit() == expected
    assert calls == expected_calls


def test_noop_where_resource_is_missing(monkeypatch):
    """Windows has no ``resource`` module."""
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "resource":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "resource", raising=False)
    monkeypatch.setattr(builtins, "__import__", _import)
    assert raise_open_file_limit() is None
