"""Tests du parsing/écriture HTTP pur du port de pool (aucune I/O)."""

from broxy4g.services.pool_http import (
    ClientRequest,
    bad_gateway_response,
    bad_request_response,
    build_connect_request,
    build_forward_request,
    parse_request_head,
    parse_status_code,
    response,
)


def _head(request_line: str, *header_lines: str) -> bytes:
    return ("\r\n".join([request_line, *header_lines, ""]).encode("ascii"))


def test_parse_connect_request():
    req = parse_request_head(_head("CONNECT example.com:443 HTTP/1.1", "Host: example.com:443"))
    assert req is not None
    assert req.method == "CONNECT"
    assert req.target == "example.com:443"
    assert req.host == "example.com"
    assert req.port == 443


def test_parse_absolute_url_default_port_80():
    req = parse_request_head(_head("GET http://example.com/a/b?q=1 HTTP/1.1"))
    assert req is not None
    assert req.target == "/a/b?q=1"
    assert req.host == "example.com"
    assert req.port == 80


def test_parse_absolute_url_explicit_port():
    req = parse_request_head(_head("GET http://h:8080/x HTTP/1.1"))
    assert req is not None
    assert req.port == 8080
    assert req.target == "/x"


def test_parse_absolute_url_bare_host_becomes_root_path():
    req = parse_request_head(_head("GET http://h HTTP/1.1"))
    assert req is not None
    assert req.target == "/"


def test_parse_absolute_url_bare_host_with_query():
    req = parse_request_head(_head("GET http://h?q=1 HTTP/1.1"))
    assert req is not None
    assert req.target == "/?q=1"


def test_parse_https_absolute_url_rejected():
    assert parse_request_head(_head("GET https://h/ HTTP/1.1")) is None


def test_parse_relative_target_rejected():
    assert parse_request_head(_head("GET /relative HTTP/1.1")) is None


def test_parse_incomplete_request_line_rejected():
    assert parse_request_head(b"GET /") is None


def test_parse_header_without_colon_rejected():
    assert parse_request_head(_head("GET http://h/ HTTP/1.1", "Host x")) is None


def test_parse_proxy_authorization_extracted_case_insensitively_and_name_preserved():
    req = parse_request_head(
        _head("CONNECT h:443 HTTP/1.1", "Proxy-Authorization: Basic abc", "Host: h")
    )
    assert req is not None
    assert req.proxy_authorization == "Basic abc"
    assert ("Proxy-Authorization", "Basic abc") in req.headers  # nom conservé tel quel
    assert ("Host", "h") in req.headers


def test_parse_status_code_ok():
    assert parse_status_code(b"HTTP/1.1 200 Connection established\r\n\r\n") == 200
    assert parse_status_code(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n") == 407


def test_parse_status_code_garbage():
    assert parse_status_code(b"garbage") is None


def test_build_connect_request_contains_required_lines_and_no_close():
    raw = build_connect_request("example.com:443", "Basic abc")
    assert b"CONNECT example.com:443 HTTP/1.1" in raw
    assert b"Host: example.com:443" in raw
    assert b"Proxy-Authorization: Basic abc" in raw
    assert b"Connection: close" not in raw  # tunnel : pas de close imposé
    assert raw.endswith(b"\r\n\r\n")


def test_build_forward_request_strips_hop_headers_and_imposes_auth_close():
    request = ClientRequest(
        method="GET",
        target="/a/b?q=1",
        host="example.com",
        port=80,
        version="HTTP/1.1",
        headers=(
            ("Proxy-Authorization", "Basic client"),
            ("Connection", "keep-alive"),
            ("Proxy-Connection", "close"),
            ("Keep-Alive", "timeout=5"),
            ("Host", "example.com"),
            ("Transfer-Encoding", "chunked"),
            ("X-Custom", "1"),
        ),
        proxy_authorization="Basic client",
    )
    raw = build_forward_request(request, "Basic member")
    text = raw.decode("ascii")
    lines = text.split("\r\n")
    assert "GET /a/b?q=1 HTTP/1.1" in lines
    assert "Host: example.com" in lines
    assert text.count("Host: example.com") == 1  # jamais dupliqué
    # En-têtes de saut du client retirés...
    assert "Proxy-Authorization: Basic client" not in text
    assert "Connection: keep-alive" not in text
    assert "Proxy-Connection: close" not in text
    assert "Keep-Alive: timeout=5" not in text
    # ... mais Transfer-Encoding et les en-têtes applicatifs sont conservés.
    assert "Transfer-Encoding: chunked" in lines
    assert "X-Custom: 1" in lines
    # Identifiants du membre et close imposés.
    assert "Proxy-Authorization: Basic member" in lines
    assert text.count("Proxy-Authorization: Basic member") == 1
    assert "Connection: close" in lines
    assert raw.endswith(b"\r\n\r\n")


def test_response_format_exact_content_length_and_close():
    raw = response(200, "OK", body=b"abc")
    text = raw.decode("ascii")
    assert text.startswith("HTTP/1.1 200 OK\r\n")
    assert "Content-Length: 3\r\n" in text
    assert "Connection: close\r\n" in text
    assert text.endswith("\r\n\r\nabc")


def test_bad_request_and_bad_gateway_responses():
    bad = bad_request_response("mauvaise requête")
    assert bad.startswith(b"HTTP/1.1 400 ")
    assert b"Content-Length: 0" in bad
    assert b"Connection: close" in bad
    assert bad.endswith(b"\r\n\r\n")
    gw = bad_gateway_response("aucun membre")
    assert gw.startswith(b"HTTP/1.1 502 ")
    assert gw.endswith(b"\r\n\r\n")
