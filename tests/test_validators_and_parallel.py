"""Tests for backend.validators and backend.parallel — small shared helpers."""
import pytest
from fastapi import HTTPException


class TestValidators:
    def test_identifier_regex_accepts_valid(self):
        from backend.validators import _IDENTIFIER_RE
        assert _IDENTIFIER_RE.match("my_catalog")
        assert _IDENTIFIER_RE.match("my-catalog")  # hyphens allowed in UC
        assert _IDENTIFIER_RE.match("adi-413")

    def test_identifier_regex_rejects_bad(self):
        from backend.validators import _IDENTIFIER_RE
        assert not _IDENTIFIER_RE.match("has space")
        assert not _IDENTIFIER_RE.match("drop;table")
        assert not _IDENTIFIER_RE.match("")

    def test_full_name_regex(self):
        from backend.validators import _FULL_NAME_RE
        assert _FULL_NAME_RE.match("cat.sch.tbl")
        assert not _FULL_NAME_RE.match("cat.sch")
        assert not _FULL_NAME_RE.match("just_a_name")

    def test_validate_ok(self):
        from backend.validators import _validate
        assert _validate("  main  ", "catalog") == "main"

    def test_validate_empty_raises_400(self):
        from backend.validators import _validate
        with pytest.raises(HTTPException) as e:
            _validate("", "catalog")
        assert e.value.status_code == 400

    def test_validate_injection_raises_400(self):
        from backend.validators import _validate
        with pytest.raises(HTTPException) as e:
            _validate("bad'; DROP--", "table")
        assert e.value.status_code == 400


class TestParallel:
    def test_map_parallel_maps(self):
        from backend.parallel import map_parallel
        out = map_parallel(lambda x: x * 2, [1, 2, 3])
        assert sorted(out) == [2, 4, 6]

    def test_map_parallel_empty(self):
        from backend.parallel import map_parallel
        assert map_parallel(lambda x: x, []) == []

    def test_map_parallel_drops_failures(self):
        from backend.parallel import map_parallel
        def f(x):
            if x == 2:
                raise ValueError("boom")
            return x
        out = map_parallel(f, [1, 2, 3])
        assert 2 not in out and set(out) == {1, 3}

    def test_run_parallel_multiple(self):
        from backend.parallel import run_parallel
        out = run_parallel(lambda: "a", lambda: "b")
        assert out == ["a", "b"]

    def test_run_parallel_single(self):
        from backend.parallel import run_parallel
        assert run_parallel(lambda: "x") == ["x"]

    def test_run_parallel_empty(self):
        from backend.parallel import run_parallel
        assert run_parallel() == []
