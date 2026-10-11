"""Tests for resolve_frontend_dist() and register_frontend() in main.py."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

# The repo root is one level above tests/
REPO_ROOT = Path(__file__).resolve().parent.parent


def test_env_var_set_returns_that_path(monkeypatch):
    monkeypatch.setenv("BROXY4G_FRONTEND_DIST", "/custom/frontend/dist")
    from broxy4g.main import resolve_frontend_dist
    assert resolve_frontend_dist() == Path("/custom/frontend/dist")


def test_env_var_empty_returns_fallback(monkeypatch):
    monkeypatch.setenv("BROXY4G_FRONTEND_DIST", "")
    from broxy4g.main import resolve_frontend_dist
    fallback = REPO_ROOT / "frontend" / "dist"
    assert resolve_frontend_dist() == fallback


def test_env_var_unset_returns_fallback(monkeypatch):
    monkeypatch.delenv("BROXY4G_FRONTEND_DIST", raising=False)
    from broxy4g.main import resolve_frontend_dist
    fallback = REPO_ROOT / "frontend" / "dist"
    assert resolve_frontend_dist() == fallback


def test_unmatched_api_path_returns_404_not_spa(tmp_path):
    """A non-matched /api/... path must 404, never serve index.html."""
    from broxy4g.main import register_frontend

    index = tmp_path / "index.html"
    index.write_text("SPA-INDEX-MARKER")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")

    app = FastAPI()
    register_frontend(app, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "SPA-INDEX-MARKER" not in response.text
    assert response.json() == {"detail": "Not Found"}


def test_root_returns_index_html(tmp_path):
    """GET / serves the SPA index.html."""
    from broxy4g.main import register_frontend

    index = tmp_path / "index.html"
    index.write_text("SPA-INDEX-MARKER")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")

    app = FastAPI()
    register_frontend(app, tmp_path)
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert response.text == "SPA-INDEX-MARKER"


def test_existing_static_file_is_served(tmp_path):
    """An existing static file under dist is served with its real content."""
    from broxy4g.main import register_frontend

    index = tmp_path / "index.html"
    index.write_text("SPA-INDEX-MARKER")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")

    app = FastAPI()
    register_frontend(app, tmp_path)
    client = TestClient(app)

    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('app')"
    assert "SPA-INDEX-MARKER" not in response.text


def test_missing_dist_is_noop(tmp_path):
    """When dist is absent, register_frontend adds no route and breaks nothing."""
    from broxy4g.main import register_frontend

    missing = tmp_path / "does-not-exist"
    app = FastAPI()
    register_frontend(app, missing)
    client = TestClient(app)

    # No catch-all registered: / returns 404 from FastAPI, not the SPA.
    response = client.get("/")
    assert response.status_code == 404
    assert "SPA-INDEX-MARKER" not in response.text
