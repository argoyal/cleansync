import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def project_version() -> str:
    return tomllib.loads(PYPROJECT.read_text())["project"]["version"]


def test_app_version_is_the_project_version():
    # The image doesn't install the project (tool.uv package = false), so there is no package
    # metadata to read: the version is written in app/__init__.py and must follow pyproject.
    assert __version__ == project_version()


def test_health_reports_ok_and_version(settings):
    client = TestClient(create_app(settings))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "version": project_version(),
        "app_name": "CleanSync",
        "syncing": "on",
    }
