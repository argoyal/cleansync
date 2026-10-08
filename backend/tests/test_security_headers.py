from app.web.security import APP_CSP, IMMUTABLE, headers_for


def test_every_response_gets_the_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


def test_error_responses_get_them_too(client):
    r = client.get("/api/sync-pairs")
    assert r.status_code == 401
    assert r.headers["content-security-policy"] == APP_CSP


def test_api_docs_page_may_load_swagger_from_its_cdn(auth_client):
    r = auth_client.get("/api/docs")
    assert r.status_code == 200
    assert "https://cdn.jsdelivr.net" in r.headers["content-security-policy"]


def test_assets_get_long_cache_only_when_found():
    assert headers_for("/assets/app-1.js", 200)["cache-control"] == IMMUTABLE
    assert "cache-control" not in headers_for("/assets/missing.js", 404)
    assert "cache-control" not in headers_for("/drive", 200)
