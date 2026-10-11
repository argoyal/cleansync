from fastapi.testclient import TestClient

from app.main import create_app


def page(settings, path):
    return TestClient(create_app(settings, start_background=False)).get(path)


def test_privacy_and_terms_are_public(settings):
    for path in ("/privacy", "/terms"):
        resp = page(settings, path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html")
        assert "CleanSync" in resp.text


def test_privacy_names_the_google_data_and_the_limited_use_rules(settings):
    text = page(settings, "/privacy").text
    assert "Google Drive" in text and "Gmail" in text
    assert "gmail.modify" in text
    assert "Limited Use" in text
    assert "Google API Services User Data Policy" in text
    assert "myaccount.google.com/permissions" in text


def test_the_contact_email_comes_from_the_setting(settings):
    settings.contact_email = "owner@example.com"
    for path in ("/privacy", "/terms"):
        assert "mailto:owner@example.com" in page(settings, path).text


def test_without_a_contact_email_the_pages_still_work(settings):
    settings.contact_email = ""
    text = page(settings, "/privacy").text
    assert "mailto:" not in text
    assert "the person who runs this server" in text


def test_the_contact_email_is_escaped(settings):
    settings.contact_email = '"><script>x</script>@example.com'
    text = page(settings, "/privacy").text
    assert "<script>x</script>" not in text


def test_the_pages_have_their_own_strict_policy(settings):
    policy = page(settings, "/privacy").headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "script-src" not in policy
    assert "frame-ancestors 'none'" in policy
