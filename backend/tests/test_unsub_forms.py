from app.services.unsubscribe.forms import has_form, page_text, simple_form

BASE = "https://x.com/u?t=1"


def test_simple_form_with_hidden_and_checkbox():
    html = """<form action="/confirm" method="post">
      <input type="hidden" name="t" value="abc">
      <input type="email" name="email" value="me@example.com">
      <input type="checkbox" name="list_all" value="all">
      <input type="checkbox" name="keep_promos" value="promos">
      <button type="submit" name="go" value="1">Unsubscribe</button></form>"""
    f = simple_form(html, BASE)
    assert f.method == "POST" and f.action == "https://x.com/confirm"
    assert ("t", "abc") in f.fields and ("email", "me@example.com") in f.fields
    assert ("list_all", "all") in f.fields and ("keep_promos", "promos") not in f.fields
    assert ("go", "1") in f.fields


def test_not_simple():
    assert simple_form("<p>no form</p>", BASE) is None
    assert simple_form("<form></form><form></form>", BASE) is None
    assert simple_form('<form><input type="password" name="p"></form>', BASE) is None
    assert simple_form('<form><div class="g-recaptcha"></div></form>', BASE) is None
    assert simple_form('<form action="javascript:x()"></form>', BASE) is None


def test_get_form_and_page_text():
    f = simple_form(
        '<form method="get" action="https://y.com/a"><input name="q" value="1"></form>', BASE
    )
    assert f.method == "GET" and f.action == "https://y.com/a"
    assert "You are unsubscribed" in page_text(
        "<html><body><h1>You are unsubscribed</h1></body></html>"
    )
    assert len(page_text("<p>" + "a " * 5000 + "</p>")) <= 4000


def test_defaults_checked_select_textarea_and_has_form():
    html = """<form><input type="checkbox" name="c" value="v" checked>
      <select name="s"><option value="a">A</option><option value="b" selected>B</option></select>
      <textarea name="t">hi</textarea></form>"""
    f = simple_form(html, BASE)
    assert f.action == BASE and f.method == "POST"
    assert ("c", "v") in f.fields and ("s", "b") in f.fields and ("t", "hi") in f.fields
    assert has_form(html) and not has_form("<p>x</p>")
