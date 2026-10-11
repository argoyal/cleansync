"""Public privacy policy and terms pages, as Google's consent screen asks for (spec §10).

They are plain pages with no login, no script and their own strict policy. The contact
address comes from the CONTACT_EMAIL setting, so no personal address lives in the code.
"""

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

PAGE_CSP = "; ".join(
    [
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)

_STYLE = """
body{margin:0;background:#F5F6FA;color:#1D2230;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:720px;margin:0 auto;padding:40px 20px 64px}
.card{background:#fff;border:1px solid #E4E6EE;border-radius:12px;padding:28px 28px 20px}
header{display:flex;align-items:center;gap:12px;margin-bottom:24px}
header img{width:40px;height:40px}
h1{font-size:26px;margin:0}
h2{font-size:18px;margin:28px 0 8px}
a{color:#2E2363}
.muted{color:#5A6172;font-size:14px}
@media (prefers-color-scheme:dark){
body{background:#14161C;color:#E7E9EF}.card{background:#1B1E26;border-color:#2E3340}
a{color:#B49CFF}.muted{color:#A0A6B5}}
"""


def _contact(request: Request) -> str:
    email = request.app.state.settings.contact_email.strip()
    if not email:
        return "the person who runs this server"
    safe = escape(email, quote=True)
    return f'<a href="mailto:{safe}">{safe}</a>'


def _page(request: Request, title: str, body: str) -> HTMLResponse:
    name = escape(request.app.state.settings.app_name)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · {name}</title><style>{_STYLE}</style></head>
<body><main><header><img src="/favicon.svg" alt=""><h1>{escape(title)}</h1></header>
<div class="card">{body}</div>
<p class="muted"><a href="/privacy">Privacy policy</a> · <a href="/terms">Terms of service</a> · <a href="/">Open {name}</a></p>
</main></body></html>"""
    return HTMLResponse(
        html, headers={"content-security-policy": PAGE_CSP, "cache-control": "no-cache"}
    )


@router.get("/privacy", include_in_schema=False)
def privacy(request: Request) -> HTMLResponse:
    contact = _contact(request)
    body = f"""
<p class="muted">Last updated: 11 October 2026</p>
<p>CleanSync is software you run on your own server to keep a copy of your Google Drive
and Gmail there, and to free space in Google by moving old files and mail to Google's
trash when you ask. This page explains what it does with your Google data.</p>

<h2>Who runs it</h2>
<p>This copy of CleanSync is run privately by its owner for their own accounts. It is not
a public service. Questions: {contact}.</p>

<h2>What Google data it uses</h2>
<ul>
<li><strong>Google Drive</strong> (permission <code>drive</code>): to copy your files to this
server and keep both sides in step, and to move files you choose to offload into Drive's trash.</li>
<li><strong>Gmail</strong> (permission <code>gmail.modify</code>): to copy your mail to this
server as standard <code>.eml</code> files, keep that copy up to date, move mail you choose to
offload into Gmail's trash, and put it back when you restore it.</li>
<li>Your Google account's email address, to tell your accounts apart.</li>
</ul>
<p>CleanSync never deletes anything from your Google account for good. It only moves items
to Google's own trash, where Google keeps them for 30 days.</p>

<h2>Where the data is kept</h2>
<p>Copies of your files and mail are stored only on this server, on storage the owner
controls. Saved Google sign-ins are kept encrypted. Nothing is sent anywhere else.</p>

<h2>What it never does</h2>
<ul>
<li>It does not sell, rent or share your data with anyone.</li>
<li>It does not use your data for advertising.</li>
<li>It does not send your data to any third party, or use it to train AI or machine learning models.</li>
<li>No person reads your data, apart from the owner reading their own files and mail.</li>
</ul>

<h2>Google API Services User Data Policy</h2>
<p>CleanSync's use and transfer of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services
User Data Policy</a>, including the Limited Use requirements.</p>

<h2>Removing access and data</h2>
<p>You can remove CleanSync's access to your Google account at any time at
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>,
or by disconnecting the account in CleanSync's Settings. The copies on this server stay
until the owner deletes them; ask {contact} to have them removed.</p>

<h2>Changes</h2>
<p>If this policy changes, the new version will be posted on this page with a new date.</p>
"""
    return _page(request, "Privacy policy", body)


@router.get("/terms", include_in_schema=False)
def terms(request: Request) -> HTMLResponse:
    contact = _contact(request)
    body = f"""
<p class="muted">Last updated: 11 October 2026</p>
<p>CleanSync is self-hosted software for backing up and cleaning up your own Google Drive
and Gmail. This copy is run privately by its owner for their own use.</p>

<h2>Using it</h2>
<ul>
<li>Use it only with Google accounts you own or are allowed to manage.</li>
<li>You are responsible for the server it runs on, its storage and its backups.</li>
<li>You decide what is offloaded or restored. CleanSync only acts on your request and
only moves items to Google's trash, never deleting them for good.</li>
</ul>

<h2>No warranty</h2>
<p>CleanSync is provided as is, without any warranty. The owner is not liable for lost
data, missed backups or any other damage from using it. Keep your own backups.</p>

<h2>Google</h2>
<p>Your use of Google Drive and Gmail is also covered by Google's own terms. See the
<a href="/privacy">privacy policy</a> for how CleanSync handles your Google data.</p>

<h2>Contact</h2>
<p>Questions: {contact}.</p>
"""
    return _page(request, "Terms of service", body)
