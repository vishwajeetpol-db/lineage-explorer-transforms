"""Tests for backend.validators — input validation helpers."""
import pytest
from fastapi import HTTPException

from backend.validators import _IDENTIFIER_RE, _FULL_NAME_RE, _validate


class TestIdentifierRegex:
    """_IDENTIFIER_RE should accept valid UC identifiers."""

    def test_simple_name(self):
        assert _IDENTIFIER_RE.match("my_catalog")

    def test_name_with_hyphens(self):
        assert _IDENTIFIER_RE.match("my-catalog-v2")

    def test_name_with_digits(self):
        assert _IDENTIFIER_RE.match("catalog123")

    def test_single_char(self):
        assert _IDENTIFIER_RE.match("a")

    def test_max_length_255(self):
        assert _IDENTIFIER_RE.match("a" * 255)

    def test_rejects_256_chars(self):
        assert not _IDENTIFIER_RE.match("a" * 256)

    def test_rejects_empty(self):
        assert not _IDENTIFIER_RE.match("")

    def test_rejects_spaces(self):
        assert not _IDENTIFIER_RE.match("my catalog")

    def test_rejects_dots(self):
        assert not _IDENTIFIER_RE.match("catalog.schema")

    def test_rejects_special_chars(self):
        assert not _IDENTIFIER_RE.match("catalog@name")

    def test_rejects_sql_injection(self):
        assert not _IDENTIFIER_RE.match("'; DROP TABLE --")


class TestFullNameRegex:
    """_FULL_NAME_RE should match three-part catalog.schema.table names."""

    def test_valid_three_part_name(self):
        assert _FULL_NAME_RE.match("catalog.schema.table")

    def test_with_hyphens(self):
        assert _FULL_NAME_RE.match("my-catalog.my-schema.my-table")

    def test_rejects_two_parts(self):
        assert not _FULL_NAME_RE.match("catalog.schema")

    def test_rejects_four_parts(self):
        assert not _FULL_NAME_RE.match("a.b.c.d")

    def test_rejects_spaces_in_parts(self):
        assert not _FULL_NAME_RE.match("my catalog.schema.table")


class TestValidateFunction:
    """_validate should strip and validate, raising HTTP 400 on failure."""

    def test_valid_input_passes(self):
        assert _validate("my_catalog", "catalog") == "my_catalog"

    def test_strips_whitespace(self):
        assert _validate("  my_table  ", "table") == "my_table"

    def test_empty_string_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("", "catalog")
        assert exc_info.value.status_code == 400

    def test_none_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate(None, "catalog")
        assert exc_info.value.status_code == 400

    def test_invalid_chars_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("catalog;DROP", "catalog")
        assert exc_info.value.status_code == 400

    def test_hyphenated_name_passes(self):
        assert _validate("my-catalog", "catalog") == "my-catalog"

    def test_error_detail_contains_param_name(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("bad value!", "schema")
        assert "schema" in exc_info.value.detail
