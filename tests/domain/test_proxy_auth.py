"""Tests de l'authentification Basic du port de pool (fonctions pures)."""

import base64

from broxy4g.domain.proxy_auth import (
    REALM,
    UNAUTHORIZED_RESPONSE,
    basic_authorization_header,
    check_basic_authorization,
    parse_basic_authorization,
)


def _b64(payload: str) -> str:
    return base64.b64encode(payload.encode()).decode("ascii")


def test_parse_basic_authorization_round_trip():
    assert parse_basic_authorization(f"Basic {_b64('user:pass')}") == ("user", "pass")


def test_parse_basic_authorization_scheme_case_insensitive():
    assert parse_basic_authorization(f"bAsIc {_b64('user:pass')}") == ("user", "pass")


def test_parse_basic_authorization_password_with_colon_splits_on_first():
    """Le mot de passe peut contenir des ``:`` : découpe sur le premier seulement."""
    assert parse_basic_authorization(f"Basic {_b64('user:pa:ss')}") == ("user", "pa:ss")


def test_parse_basic_authorization_none_or_empty():
    assert parse_basic_authorization(None) is None
    assert parse_basic_authorization("") is None


def test_parse_basic_authorization_wrong_scheme():
    assert parse_basic_authorization("Bearer abc") is None


def test_parse_basic_authorization_invalid_base64():
    assert parse_basic_authorization("Basic !!!") is None


def test_parse_basic_authorization_valid_base64_without_colon():
    """Base64 valide sans ``:`` : en-tête inexploitable, donc ``None``."""
    assert parse_basic_authorization(f"Basic {_b64('nosign')}") is None


def test_check_basic_authorization_correct_couple():
    header = f"Basic {_b64('user:pass')}"
    assert check_basic_authorization(header, username="user", password="pass") is True


def test_check_basic_authorization_wrong_password():
    header = f"Basic {_b64('user:bad')}"
    assert check_basic_authorization(header, username="user", password="pass") is False


def test_check_basic_authorization_wrong_user():
    header = f"Basic {_b64('other:pass')}"
    assert check_basic_authorization(header, username="user", password="pass") is False


def test_check_basic_authorization_missing_header():
    assert check_basic_authorization(None, username="user", password="pass") is False


def test_check_basic_authorization_empty_expected_username():
    """Un pool activé ne doit jamais exister sans identifiants : refus systématique."""
    assert check_basic_authorization(f"Basic {_b64(':pass')}", username="", password="pass") is False


def test_check_basic_authorization_empty_expected_password():
    assert check_basic_authorization(f"Basic {_b64('user:')}", username="user", password="") is False


def test_basic_authorization_header_round_trip_with_parse():
    header = basic_authorization_header("alice", "s3cret:x")
    assert parse_basic_authorization(header) == ("alice", "s3cret:x")


def test_unauthorized_response_is_full_407():
    assert UNAUTHORIZED_RESPONSE.startswith(b"HTTP/1.1 407")
    assert f'Proxy-Authenticate: Basic realm="{REALM}"'.encode("ascii") in UNAUTHORIZED_RESPONSE
    assert b"Content-Length: 0" in UNAUTHORIZED_RESPONSE
    assert UNAUTHORIZED_RESPONSE.endswith(b"\r\n\r\n")
