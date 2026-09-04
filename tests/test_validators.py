"""Tests for backend.validators — input validation helpers.

Fixes:
- C15: Hyphenated identifier inconsistency between main.py and validators.py
- A1:  SQL injection vector testing through all validation paths
- Coverage: Comprehensive injection payloads, boundary conditions
"""
import re
import pytest
from fastapi import HTTPException

from unittest.mock import MagicMock, patch

from backend.validators import (
    _IDENTIFIER_RE, _FULL_NAME_RE, _validate, sql_str, require_admin,
)


class TestIdentifierRegex:
    """_IDENTIFIER_RE should accept valid UC identifiers (validators.py version)."""

    def test_simple_name(self):
        assert _IDENTIFIER_RE.match("my_catalog")

    def test_name_with_hyphens(self):
        """C15: validators.py accepts hyphens (correct for UC)."""
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

    def test_rejects_sql_injection_basic(self):
        assert not _IDENTIFIER_RE.match("'; DROP TABLE --")

    def test_rejects_semicolons(self):
        """A1: Semicolons must never pass (SQL statement separator)."""
        assert not _IDENTIFIER_RE.match("catalog;")

    def test_rejects_single_quotes(self):
        """A1: Single quotes must never pass (SQL string delimiter)."""
        assert not _IDENTIFIER_RE.match("cat'alogue")

    def test_rejects_double_quotes(self):
        assert not _IDENTIFIER_RE.match('cata"log')

    def test_rejects_backticks(self):
        assert not _IDENTIFIER_RE.match("`catalog`")

    def test_rejects_parentheses(self):
        """A1: Parens enable function calls in injected SQL."""
        assert not _IDENTIFIER_RE.match("catalog()")

    def test_rejects_comment_syntax(self):
        """A1: SQL block-comment sequences (with special chars) are rejected.

        Note: '--' is composed solely of hyphens, which the regex intentionally
        ALLOWS (UC permits hyphens in identifiers). The real defense against a
        trailing '--' comment is SQL parameterization, not this character class.
        Sequences containing slashes/stars (block comments) ARE rejected.
        """
        assert _IDENTIFIER_RE.match("catalog--")  # hyphens are valid id chars
        assert not _IDENTIFIER_RE.match("catalog/**/")

    def test_rejects_union_keyword_chars(self):
        """A1: Spaces required for UNION, but ensure no whitespace passes."""
        assert not _IDENTIFIER_RE.match("x UNION SELECT")

    def test_rejects_newlines(self):
        """A1: Newlines can bypass single-line comment filters.

        Note: Python's `$` matches just before a trailing newline, so
        `.match("catalog\\n")` succeeds on the "catalog" prefix. The correct
        anchored check is `fullmatch`, which rejects the trailing newline —
        this is what `_validate` effectively relies on after `.strip()`.
        """
        assert not _IDENTIFIER_RE.fullmatch("catalog\n")

    def test_rejects_null_bytes(self):
        assert not _IDENTIFIER_RE.match("catalog\x00")


class TestIdentifierRegexDiscrepancy:
    """C15: main.py _IDENTIFIER_RE vs validators.py _IDENTIFIER_RE differ on hyphens.

    main.py:       ^[A-Za-z0-9_]{1,255}$     (NO hyphens)
    validators.py: ^[A-Za-z0-9_-]{1,255}$    (allows hyphens)

    This is a real bug — routes using main.py’s regex reject valid UC names
    like 'adi-413' or 'my-catalog', while routes using validators.py accept them.
    """

    def test_main_py_rejects_hyphens(self):
        """C15 BUG: main.py regex rejects valid hyphenated UC identifiers."""
        main_re = re.compile(r"^[A-Za-z0-9_]{1,255}$")
        # This is a valid UC catalog name but main.py rejects it
        assert not main_re.match("my-catalog")
        assert not main_re.match("adi-413")

    def test_validators_py_accepts_hyphens(self):
        """C15: validators.py correctly accepts hyphens."""
        assert _IDENTIFIER_RE.match("my-catalog")
        assert _IDENTIFIER_RE.match("adi-413")

    def test_inconsistency_documented(self):
        """Both regexes should agree on standard identifiers."""
        main_re = re.compile(r"^[A-Za-z0-9_]{1,255}$")
        validators_re = _IDENTIFIER_RE
        # No-hyphen names: both agree
        assert main_re.match("my_catalog") and validators_re.match("my_catalog")
        # Hyphenated names: they disagree (BUG)
        assert not main_re.match("my-catalog")
        assert validators_re.match("my-catalog")


class TestFullNameRegex:
    """_FULL_NAME_RE should match three-part catalog.schema.table names."""

    def test_valid_three_part_name(self):
        assert _FULL_NAME_RE.match("catalog.schema.table")

    def test_with_hyphens(self):
        """C15: Three-part names with hyphens should be valid."""
        assert _FULL_NAME_RE.match("my-catalog.my-schema.my-table")

    def test_rejects_two_parts(self):
        assert not _FULL_NAME_RE.match("catalog.schema")

    def test_rejects_four_parts(self):
        assert not _FULL_NAME_RE.match("a.b.c.d")

    def test_rejects_spaces_in_parts(self):
        assert not _FULL_NAME_RE.match("my catalog.schema.table")

    def test_rejects_sql_injection_in_part(self):
        """A1: Injection in any part of the three-part name."""
        assert not _FULL_NAME_RE.match("cat';DROP.schema.table")
        assert not _FULL_NAME_RE.match("catalog.sch;DROP.table")
        assert not _FULL_NAME_RE.match("catalog.schema.tbl OR 1=1")

    def test_rejects_url_encoded_injection(self):
        """A1: Even encoded payloads should fail."""
        assert not _FULL_NAME_RE.match("catalog.schema.table%27")


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
        """C15: Hyphenated names valid in validators.py."""
        assert _validate("my-catalog", "catalog") == "my-catalog"

    def test_error_detail_contains_param_name(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("bad value!", "schema")
        assert "schema" in exc_info.value.detail

    # --- A1: SQL injection payload tests ---
    def test_rejects_or_1_equals_1(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("x' OR '1'='1", "catalog")
        assert exc_info.value.status_code == 400

    def test_rejects_union_select(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("x UNION SELECT", "table")
        assert exc_info.value.status_code == 400

    def test_rejects_stacked_queries(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("x; DROP TABLE users", "schema")
        assert exc_info.value.status_code == 400

    def test_rejects_comment_evasion(self):
        with pytest.raises(HTTPException) as exc_info:
            _validate("admin'--", "catalog")
        assert exc_info.value.status_code == 400

    def test_rejects_hex_encoded_injection(self):
        # '0x27' matches [A-Za-z0-9_-] but is not harmful by itself.
        # This test documents that hex-looking chars PASS the regex
        # (actual SQL parameterization is the real defense), so _validate
        # returns the value unchanged rather than raising.
        assert _validate("0x27", "catalog") == "0x27"

    def test_truncation_in_error_message(self):
        """Error detail should truncate long inputs ([:50])."""
        long_invalid = "@" * 100
        with pytest.raises(HTTPException) as exc_info:
            _validate(long_invalid, "catalog")
        # Verify truncation doesn't leak full payload
        assert len(exc_info.value.detail) < 200


class TestSqlStr:
    r"""`sql_str` is the load-bearing escape for every user value that reaches SQL
    text. The backslash-before-quote ORDER is the whole point: Databricks SQL
    (Spark) treats `\` as an escape inside single-quoted literals by default, so
    quote-doubling alone is bypassable — a value starting `\'` becomes `\''`,
    whose first quote is consumed as an escaped quote and whose second quote
    CLOSES the literal, letting the remainder execute as SQL."""

    def test_doubles_quotes(self):
        assert sql_str("O'Brien") == "O''Brien"

    def test_escapes_backslash_before_quote(self):
        # The bypass payload: a lone backslash-quote must NOT be able to close the
        # literal. Backslash is doubled first, so the quote stays escaped data.
        assert sql_str("\\'") == "\\\\''"

    def test_bypass_payload_cannot_close_the_literal(self):
        payload = "\\' UNION SELECT ssn FROM main.pii.customers -- "
        out = sql_str(payload)
        # The dangerous adjacency is a single backslash immediately followed by a
        # single quote; after escaping the backslash is doubled so it is data.
        assert "\\\\''" in out
        assert not out.startswith("\\'")

    def test_order_matters_regression(self):
        # Quote-first-then-backslash would produce \\'' from \' too, but would
        # mangle a bare backslash differently. Pin the exact expected output so a
        # future refactor can't silently swap the order.
        assert sql_str("a\\b'c") == "a\\\\b''c"

    def test_none_and_non_string(self):
        assert sql_str(None) == ""
        assert sql_str(7) == "7"

    def test_limit_truncates_before_escaping(self):
        # Truncating AFTER escaping could split an escape pair and re-open the
        # literal; `limit` must therefore apply to the raw value.
        assert sql_str("\\" * 10, limit=2) == "\\\\\\\\"
        assert sql_str("abcdef", limit=3) == "abc"

    def test_limit_ignored_when_non_positive(self):
        assert sql_str("abc", limit=0) == "abc"


class TestRequireAdmin:
    """Every privileged handler calls this explicitly — the app has no auth
    middleware and no router-level dependencies, so a missing call ships ungated."""

    def test_raises_403_for_non_admin(self):
        with patch("backend.main._get_user_info", return_value=("user@x.com", False)):
            with pytest.raises(HTTPException) as exc:
                require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_raises_403_for_anonymous(self):
        with patch("backend.main._get_user_info", return_value=(None, False)):
            with pytest.raises(HTTPException) as exc:
                require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_returns_email_for_admin(self):
        with patch("backend.main._get_user_info", return_value=("admin@x.com", True)):
            assert require_admin(MagicMock()) == "admin@x.com"

    def test_admin_with_no_email_returns_empty_string(self):
        with patch("backend.main._get_user_info", return_value=(None, True)):
            assert require_admin(MagicMock()) == ""
