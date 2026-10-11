"""Tests des identifiants proxy (domaines purs, rng injectable)."""

import random

from passlib.hash import md5_crypt

from broxy4g.domain.proxy_credentials import (
    PASSWORD_ALPHABET,
    USER_ALPHABET,
    ProxyCredentials,
    generate_credentials,
    generate_password,
    generate_username,
    hash_password,
)

AMBIGUOUS_CHARS = set("0oOlI1")


def test_username_default_length_and_alphabet():
    username = generate_username()
    assert len(username) == 12
    assert set(username) <= set(USER_ALPHABET)


def test_password_default_length_and_alphabet():
    password = generate_password()
    assert len(password) == 32
    assert set(password) <= set(PASSWORD_ALPHABET)


def test_alphabets_exclude_ambiguous_chars():
    assert not (set(USER_ALPHABET) & AMBIGUOUS_CHARS)
    assert not (set(PASSWORD_ALPHABET) & AMBIGUOUS_CHARS)


def test_password_alphabet_is_user_alphabet_plus_uppercase():
    assert PASSWORD_ALPHABET.startswith(USER_ALPHABET)
    assert set(PASSWORD_ALPHABET) > set(USER_ALPHABET)


def test_two_calls_differ():
    assert generate_username() != generate_username()
    assert generate_password() != generate_password()


def test_hash_password_md5_crypt_format_and_verify():
    hashed = hash_password("secret")
    assert hashed.startswith("$1$")
    assert md5_crypt.verify("secret", hashed)
    assert not md5_crypt.verify("other", hashed)


def test_hash_password_fixed_salt_is_deterministic():
    first = hash_password("secret", salt="abcd")
    second = hash_password("secret", salt="abcd")
    assert first == second
    assert first.startswith("$1$abcd$")
    assert md5_crypt.verify("secret", first)


def test_generate_credentials_with_injected_rng():
    rng = random.Random(42)
    creds = generate_credentials(rng=rng)

    assert isinstance(creds, ProxyCredentials)
    assert len(creds.username) == 12
    assert len(creds.password) == 32
    assert set(creds.username) <= set(USER_ALPHABET)
    assert set(creds.password) <= set(PASSWORD_ALPHABET)
    assert creds.password_hash.startswith("$1$")
    assert md5_crypt.verify(creds.password, creds.password_hash)


def test_generate_username_injected_rng_is_deterministic():
    a = generate_username(rng=random.Random(7))
    b = generate_username(rng=random.Random(7))
    assert a == b
    assert len(a) == 12
    assert set(a) <= set(USER_ALPHABET)
