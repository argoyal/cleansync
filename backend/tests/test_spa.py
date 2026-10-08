from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    return dist


@pytest.fixture
def spa_client(settings, tmp_path):
    settings.frontend_dir = _dist(tmp_path)
    with TestClient(create_app(settings, start_background=False)) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/drive", "/activity/3", "/settings?tab=accounts"])
def test_app_routes_get_index_html(spa_client, path):
    r = spa_client.get(path)
    assert r.status_code == 200
    assert "id=root" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_assets_are_served_with_a_long_cache(spa_client):
    r = spa_client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_top_level_files_are_served(spa_client):
    r = spa_client.get("/favicon.svg")
    assert r.status_code == 200 and "<svg" in r.text


@pytest.mark.parametrize("path", ["/api/nope", "/files/1/x.txt", "/assets/missing.js", "/api"])
def test_api_files_and_assets_never_fall_back_to_the_app(spa_client, path):
    r = spa_client.get(path)
    assert "id=root" not in r.text
    assert r.status_code in (401, 404)


def test_missing_build_gives_a_clear_404(settings, tmp_path):
    settings.frontend_dir = tmp_path / "not-built"
    with TestClient(create_app(settings, start_background=False)) as c:
        r = c.get("/")
    assert r.status_code == 404
    assert "not built" in r.json()["detail"]
