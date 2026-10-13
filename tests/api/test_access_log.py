import logging

import uvicorn.config

from broxy4g.api.access_log import RedactingAccessFormatter, build_access_log_config

_ACCESS_FMT = uvicorn.config.LOGGING_CONFIG["formatters"]["access"]["fmt"]


def _access_record(full_path: str):
    """Reconstruit un LogRecord tel que l'émet le handler access d'uvicorn
    (``msg='%s - "%s %s HTTP/%s" %d'``, args positionnels)."""
    return logging.LogRecord(
        name="access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", "GET", full_path, "1.1", 200),
        exc_info=None,
    )


class TestRedactingAccessFormatter:
    def test_redacts_password_in_query_string(self):
        formatter = RedactingAccessFormatter(fmt=_ACCESS_FMT, use_colors=False)
        line = formatter.format(_access_record("/api/v1/dongles?user=admin&password=secret-xyz"))

        assert "secret-xyz" not in line
        assert "admin" not in line  # `user` est aussi une clé sensible
        assert "password=***" in line
        # Le reste de la ligne (méthode, chemin, version HTTP, statut) est intact.
        assert "GET /api/v1/dongles?user=***&password=*** HTTP/1.1" in line
        assert "127.0.0.1:54321" in line
        assert line.endswith("200 OK")

    def test_redacts_user_and_token_keys(self):
        formatter = RedactingAccessFormatter(fmt=_ACCESS_FMT, use_colors=False)
        line = formatter.format(_access_record("/x?key=abc123&token=t0k&username=bob"))

        assert "abc123" not in line
        assert "t0k" not in line
        assert "bob" not in line
        assert "key=***" in line
        assert "token=***" in line
        assert "username=***" in line

    def test_case_insensitive_keys(self):
        formatter = RedactingAccessFormatter(fmt=_ACCESS_FMT, use_colors=False)
        line = formatter.format(_access_record("/x?PASSWORD=secret-xyz"))

        assert "secret-xyz" not in line
        assert "PASSWORD=***" in line

    def test_idempotent_on_already_redacted_value(self):
        formatter = RedactingAccessFormatter(fmt=_ACCESS_FMT, use_colors=False)
        line = formatter.format(_access_record("/x?password=***"))

        assert "password=***" in line

    def test_keeps_non_sensitive_values(self):
        formatter = RedactingAccessFormatter(fmt=_ACCESS_FMT, use_colors=False)
        line = formatter.format(_access_record("/api/v1/dongles?sort=asc&limit=5"))

        assert "sort=asc" in line
        assert "limit=5" in line


class TestBuildAccessLogConfig:
    def test_replaces_access_formatter(self):
        config = build_access_log_config()

        assert config["formatters"]["access"]["()"] is RedactingAccessFormatter
        # ``fmt`` est conservé (format de ligne identique à uvicorn).
        assert config["formatters"]["access"]["fmt"] == _ACCESS_FMT

    def test_does_not_mutate_uvicorn_default_config(self):
        build_access_log_config()

        assert uvicorn.config.LOGGING_CONFIG["formatters"]["access"]["()"] == "uvicorn.logging.AccessFormatter"
