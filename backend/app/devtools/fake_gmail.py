"""A fake Gmail for tests, development and the browser test (spec §11).

It answers the Gmail REST calls CleanSync makes, from mail kept in memory (and saved to a
JSON file after every change when it has one), and can be told to fail: a status for the
next calls to an endpoint, a revoked login, history that is too old. No real account, no
quota. Plug it into the real GmailAdapter with transport().
"""

import argparse
import base64
import email.utils
import json
import os
import re
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx

SYSTEM_LABELS = [
    "INBOX",
    "SENT",
    "STARRED",
    "IMPORTANT",
    "UNREAD",
    "TRASH",
    "SPAM",
    "DRAFT",
    "CHAT",
    "CATEGORY_PERSONAL",
    "CATEGORY_UPDATES",
]
LEFT_OUT = {"SPAM", "TRASH", "DRAFT", "CHAT"}
_BASE = "/gmail/v1/users/me"
_UPLOAD = "/upload/gmail/v1/users/me"
MULTIPART_LIMIT = 5 * 1024 * 1024  # Gmail's limit for a simple or multipart upload


@dataclass
class FakeMessage:
    id: str
    thread_id: str
    raw: bytes
    labels: list[str]
    internal_date_ms: int
    history_id: int


@dataclass
class _Failure:
    method: str
    path: re.Pattern
    status: int
    times: int
    reason: str | None


@dataclass
class FakeGmail:
    email: str = "alice@example.com"
    path: Path | None = None
    messages: dict[str, FakeMessage] = field(default_factory=dict)
    user_labels: dict[str, str] = field(default_factory=dict)  # id -> name
    history: list[dict] = field(default_factory=list)
    history_id: int = 1000
    oldest_history: int = 1000
    revoked: bool = False
    # False: the mailbox has no Gmail settings permission, so every filter call answers 403.
    settings_scope: bool = True
    filters: dict[str, dict] = field(default_factory=dict)  # id -> {id, criteria, action}
    calls: list[tuple[str, str, dict]] = field(default_factory=list)
    _failures: list[_Failure] = field(default_factory=list)
    _uploads: dict[str, list[str]] = field(default_factory=dict)  # resumable: id -> labels
    # Ids every rfc822msgid search also answers: real Gmail's search is not an exact match.
    loose_matches: list[str] = field(default_factory=list)
    _next: int = 0x18A0000000000000
    _saved: tuple[int, int] | None = None  # the file as this object last wrote or read it

    # ---- building a mailbox ------------------------------------------------------------
    def _new_id(self) -> str:
        self._next += 1
        return f"{self._next:x}"

    def _record(self, kind: str, msg: FakeMessage) -> None:
        self.history_id += 1
        msg.history_id = self.history_id
        entry = {"id": str(self.history_id)}
        item = {"message": {"id": msg.id, "threadId": msg.thread_id, "labelIds": list(msg.labels)}}
        entry[kind] = [item]
        self.history.append(entry)

    def add_label(self, name: str) -> str:
        lid = f"Label_{len(self.user_labels) + 1}"
        self.user_labels[lid] = name
        self.save()
        return lid

    def add(
        self,
        raw: bytes,
        *,
        labels=("INBOX",),
        when: datetime | None = None,
        thread_id: str | None = None,
    ) -> str:
        mid = self._new_id()
        when = when or datetime.now(UTC)
        labels = list(labels)
        sender = email.utils.parseaddr(str(email.message_from_bytes(raw).get("From") or ""))[1]
        for f in self.filters.values():  # a filter works on new mail only
            wanted = str(f["criteria"].get("from") or "").lower()
            if wanted and wanted == sender.lower():
                drop = set(f["action"].get("removeLabelIds") or [])
                labels = [x for x in labels if x not in drop]
        msg = FakeMessage(mid, thread_id or mid, raw, labels, int(when.timestamp() * 1000), 0)
        self.messages[mid] = msg
        self._record("messagesAdded", msg)
        self.save()
        return mid

    def set_labels(self, mid: str, labels) -> None:
        msg = self.messages[mid]
        before, after = set(msg.labels), set(labels)
        msg.labels = list(labels)
        if after - before:
            self._record("labelsAdded", msg)
        if before - after:
            self._record("labelsRemoved", msg)
        self.save()

    def remove(self, mid: str) -> None:
        msg = self.messages.pop(mid)
        self._record("messagesDeleted", msg)
        self.save()

    def forget_history(self) -> None:
        """Gmail keeps history for a limited time: from now on, every start id older than
        the current one answers 404 (the current one, from the profile, still works)."""
        self.oldest_history = self.history_id

    def fail(
        self,
        method: str,
        path_regex: str,
        status: int,
        *,
        times: int = 1,
        reason: str | None = None,
    ) -> None:
        self._failures.append(_Failure(method, re.compile(path_regex), status, times, reason))

    def revoke(self) -> None:
        self.revoked = True

    # ---- saving --------------------------------------------------------------------------
    def disk_stamp(self) -> tuple[int, int] | None:
        """When and how big the saved file is now, or None without one."""
        try:
            st = self.path.stat() if self.path is not None else None
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size) if st is not None else None

    def changed_on_disk(self) -> bool:
        """Something else (the delete command) changed the file since this object saved it."""
        return self.path is not None and self.disk_stamp() != self._saved

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "email": self.email,
            "history_id": self.history_id,
            "oldest_history": self.oldest_history,
            "next": self._next,
            "user_labels": self.user_labels,
            "history": self.history,
            "messages": [
                {**asdict(m), "raw": base64.b64encode(m.raw).decode()}
                for m in self.messages.values()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)
        self._saved = self.disk_stamp()

    @classmethod
    def load(cls, path: Path, email: str = "alice@example.com") -> "FakeGmail":
        fake = cls(email=email, path=path)
        if not path.exists():
            return fake
        data = json.loads(path.read_text(encoding="utf-8"))
        fake.email = data["email"]
        fake.history_id = data["history_id"]
        fake.oldest_history = data["oldest_history"]
        fake._next = data["next"]
        fake.user_labels = data["user_labels"]
        fake.history = data["history"]
        for m in data["messages"]:
            m["raw"] = base64.b64decode(m["raw"])
            fake.messages[m["id"]] = FakeMessage(**m)
        fake._saved = fake.disk_stamp()
        return fake

    # ---- answering -----------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _error(status: int, reason: str | None = None) -> httpx.Response:
        errors = [{"reason": reason}] if reason else []
        return httpx.Response(
            status, json={"error": {"code": status, "message": "fake", "errors": errors}}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = {k: v for k, v in request.url.params.multi_items()}
        self.calls.append((request.method, path, params))
        for f in self._failures:
            if f.times > 0 and f.method == request.method and f.path.search(path):
                f.times -= 1
                return self._error(f.status, f.reason)
        if self.revoked or not request.headers.get("Authorization", "").startswith("Bearer "):
            return self._error(401)
        if path.startswith(_UPLOAD) and path.endswith("/messages/import"):
            if params.get("uploadType") == "resumable":
                return self._resumable(request, params)
            return self._import(request)
        if not path.startswith(_BASE):
            return self._error(404)
        rest = path[len(_BASE) :]
        if rest == "/profile":
            return httpx.Response(
                200,
                json={
                    "emailAddress": self.email,
                    "messagesTotal": len(self.messages),
                    "historyId": str(self.history_id),
                },
            )
        if rest == "/settings/filters" or rest.startswith("/settings/filters/"):
            return self._filters(request, rest)
        if rest == "/labels" and request.method == "POST":
            name = str(json.loads(request.content or b"{}").get("name") or "").strip()
            taken = {v.lower() for v in self.user_labels.values()} | {
                x.lower() for x in SYSTEM_LABELS
            }
            if not name or name.lower() in taken:
                return self._error(409, "duplicate")
            lid = self.add_label(name)
            return httpx.Response(200, json={"id": lid, "name": name, "type": "user"})
        if rest == "/labels":
            labels = [{"id": x, "name": x, "type": "system"} for x in SYSTEM_LABELS]
            labels += [{"id": k, "name": v, "type": "user"} for k, v in self.user_labels.items()]
            return httpx.Response(200, json={"labels": labels})
        if rest == "/messages" and request.method == "GET":
            return self._list(request)
        if rest == "/history":
            return self._history(request)
        if rest == "/messages/batchDelete" and request.method == "POST":
            ids = [str(x) for x in json.loads(request.content or b"{}").get("ids") or []]
            if not ids or len(ids) > 1000:
                return self._error(400, "invalidArgument")
            for mid in ids:
                if mid in self.messages:  # Gmail ignores ids it doesn't have
                    self.remove(mid)
            return httpx.Response(204)
        if rest == "/messages/batchModify" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            ids = [str(x) for x in body.get("ids") or []]
            if not ids or len(ids) > 1000 or any(i not in self.messages for i in ids):
                return self._error(400, "invalidArgument")
            add = [str(x) for x in body.get("addLabelIds") or []]
            drop = {str(x) for x in body.get("removeLabelIds") or []}
            if "TRASH" in add:
                drop.add("INBOX")  # as the fake's messages.trash does
            for mid in ids:
                kept = [x for x in self.messages[mid].labels if x not in drop]
                self.set_labels(mid, kept + [x for x in add if x not in kept])
            return httpx.Response(204)
        m = re.fullmatch(r"/messages/([^/]+)(/trash|/untrash|/modify)?", rest)
        if m is None:
            return self._error(404)
        msg = self.messages.get(m.group(1))
        if msg is None:
            return self._error(404)
        if request.method == "GET":
            return self._get(msg, params.get("format", "full"))
        if request.method == "DELETE" and m.group(2) is None:
            self.remove(msg.id)
            return httpx.Response(204)
        if m.group(2) == "/trash":
            self.set_labels(
                msg.id,
                [x for x in msg.labels if x != "INBOX"] + ["TRASH"]
                if "TRASH" not in msg.labels
                else msg.labels,
            )
            return httpx.Response(200, json={"id": msg.id, "labelIds": msg.labels})
        if m.group(2) == "/untrash":
            back = [x for x in msg.labels if x != "TRASH"]
            self.set_labels(msg.id, back if "INBOX" in back or "SENT" in back else ["INBOX", *back])
            return httpx.Response(200, json={"id": msg.id, "labelIds": msg.labels})
        if m.group(2) == "/modify" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            add = [str(x) for x in body.get("addLabelIds") or []]
            remove = {str(x) for x in body.get("removeLabelIds") or []}
            self.set_labels(
                msg.id,
                [x for x in msg.labels if x not in remove]
                + [x for x in add if x not in msg.labels],
            )
            return httpx.Response(200, json={"id": msg.id, "labelIds": msg.labels})
        return self._error(404)

    def _filters(self, request: httpx.Request, rest: str) -> httpx.Response:
        if not self.settings_scope:
            return self._error(403, "insufficientPermissions")
        if rest == "/settings/filters" and request.method == "GET":
            return httpx.Response(200, json={"filter": list(self.filters.values())})
        if rest == "/settings/filters" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            criteria, action = body.get("criteria") or {}, body.get("action") or {}
            if any(
                f["criteria"] == criteria and f["action"] == action for f in self.filters.values()
            ):
                return httpx.Response(
                    400, json={"error": {"code": 400, "message": "Filter already exists"}}
                )
            fid = f"ANe1Bm{len(self.filters) + 1}"
            while fid in self.filters:
                fid += "x"
            self.filters[fid] = {"id": fid, "criteria": criteria, "action": action}
            return httpx.Response(200, json=self.filters[fid])
        fid = rest.removeprefix("/settings/filters/")
        if request.method == "DELETE":
            if self.filters.pop(fid, None) is None:
                return self._error(404)
            return httpx.Response(204)
        return self._error(404)

    def _ordered(self, include_left_out: bool) -> list[FakeMessage]:
        msgs = [
            m for m in self.messages.values() if include_left_out or not (LEFT_OUT & set(m.labels))
        ]
        return sorted(msgs, key=lambda m: (m.internal_date_ms, m.id), reverse=True)

    def _list(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        q = p.get("q", "")
        skip_left_out = "-in:spam" in q and "-in:trash" in q
        msgs = self._ordered(include_left_out=not skip_left_out)
        if q.startswith("rfc822msgid:"):
            # Gmail leaves Spam and Trash out unless includeSpamTrash=true.
            wanted = q.removeprefix("rfc822msgid:").strip("<>")
            spam_trash = p.get("includeSpamTrash") == "true"
            msgs = [
                m
                for m in msgs
                if (_message_id(m.raw) == wanted or m.id in self.loose_matches)
                and (spam_trash or not {"SPAM", "TRASH"} & set(m.labels))
            ]
        size = int(p.get("maxResults", "100"))
        start = int(p.get("pageToken") or 0)
        page = msgs[start : start + size]
        body: dict = {
            "messages": [{"id": m.id, "threadId": m.thread_id} for m in page],
            "resultSizeEstimate": len(msgs),
        }
        if start + size < len(msgs):
            body["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=body)

    def _get(self, msg: FakeMessage, fmt: str) -> httpx.Response:
        body = {
            "id": msg.id,
            "threadId": msg.thread_id,
            "labelIds": list(msg.labels),
            "historyId": str(msg.history_id),
            "internalDate": str(msg.internal_date_ms),
            "sizeEstimate": len(msg.raw),
        }
        if fmt == "metadata":
            mid = _message_id(msg.raw)
            body["payload"] = {
                "headers": [{"name": "Message-ID", "value": f"<{mid}>"}] if mid else []
            }
        if fmt == "raw":
            body["raw"] = base64.urlsafe_b64encode(msg.raw).decode().rstrip("=")
            body["snippet"] = msg.raw.split(b"\r\n\r\n", 1)[-1][:80].decode("utf-8", "replace")
        return httpx.Response(200, json=body)

    def _history(self, request: httpx.Request) -> httpx.Response:
        p = request.url.params
        start = int(p.get("startHistoryId", "0"))
        if start < self.oldest_history:
            return self._error(404)
        newer = [h for h in self.history if int(h["id"]) > start]
        offset = int(p.get("pageToken") or 0)
        page = newer[offset : offset + 100]
        body: dict = {"history": page, "historyId": str(self.history_id)}
        if offset + 100 < len(newer):
            body["nextPageToken"] = str(offset + 100)
        return httpx.Response(200, json=body)

    def _import(self, request: httpx.Request) -> httpx.Response:
        if len(request.content) > MULTIPART_LIMIT:
            return self._error(413, "uploadTooLarge")
        ctype = request.headers.get("Content-Type", "")
        boundary = ctype.split("boundary=", 1)[-1].encode()
        parts = request.content.split(b"--" + boundary)
        meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
        raw = parts[2].split(b"\r\n\r\n", 1)[1]
        raw = raw[:-2] if raw.endswith(b"\r\n") else raw
        return self._imported(raw, meta.get("labelIds") or [])

    def _resumable(self, request: httpx.Request, params: dict) -> httpx.Response:
        """Gmail's resumable upload: a POST with the labels opens a session (its address in
        Location), then a PUT to that address carries the mail."""
        if request.method == "POST":
            upload_id = secrets.token_hex(8)
            meta = json.loads(request.content or b"{}")
            self._uploads[upload_id] = list(meta.get("labelIds") or [])
            location = str(request.url.copy_merge_params({"upload_id": upload_id}))
            return httpx.Response(200, headers={"Location": location})
        labels = self._uploads.pop(params.get("upload_id", ""), None)
        if request.method != "PUT" or labels is None:
            return self._error(404)
        return self._imported(request.content, labels)

    def _imported(self, raw: bytes, labels: list[str]) -> httpx.Response:
        when = datetime.now(UTC)
        date_line = re.search(rb"^Date: (.+?)\r?$", raw, re.M)
        if date_line:
            try:
                when = email.utils.parsedate_to_datetime(date_line.group(1).decode())
            except (TypeError, ValueError):
                pass
        mid = self.add(raw, labels=labels, when=when)
        return httpx.Response(
            200, json={"id": mid, "threadId": mid, "labelIds": self.messages[mid].labels}
        )


def _message_id(raw: bytes) -> str | None:
    found = re.search(rb"^Message-ID:\s*<?([^>\r\n]+)>?", raw, re.M | re.I)
    return found.group(1).decode("utf-8", "replace").strip() if found else None


# ---- sample mail --------------------------------------------------------------------------
# A tiny valid PDF and a 1x1 PNG, so attachments preview in the browser.
MINI_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]"
    b"/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def make_mail(
    subject: str,
    *,
    sender: str = "friend@example.com",
    to: str = "alice@example.com",
    text: str = "Hello there.",
    html: str | None = None,
    when: datetime | None = None,
    attachments: tuple[tuple[str, str, bytes], ...] = (),
    inline_png_cid: str | None = None,
    reply_to: str | None = None,
    cc: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """One mail as it would arrive (CRLF line ends), for tests and the sample mailbox."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    for name, value in (headers or {}).items():
        msg[name] = value
    msg["Subject"] = subject
    msg["Date"] = email.utils.format_datetime(when or datetime.now(UTC))
    msg["Message-ID"] = email.utils.make_msgid(domain="example.com")
    if reply_to:
        msg["In-Reply-To"] = reply_to
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
        if inline_png_cid:
            msg.get_payload()[1].add_related(
                TINY_PNG, "image", "png", cid=f"<{inline_png_cid}>", disposition="inline"
            )
    for name, ctype, data in attachments:
        main, sub = ctype.split("/", 1)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))


SAMPLE_KEPT = 26
SAMPLE_TOTAL = 28  # with one Spam and one draft, which are never kept
SAMPLE_OLDER_THAN_A_YEAR = 13


def sample_mailbox(fake: FakeGmail, now: datetime) -> None:
    """The mailbox of the pretend account (development and the browser test)."""
    work, receipts, travel = (
        fake.add_label("Work"),
        fake.add_label("Receipts"),
        fake.add_label("Travel"),
    )
    me = fake.email
    days = lambda n: now - timedelta(days=n)  # noqa: E731
    pdf = lambda name: ((name, "application/pdf", MINI_PDF),)  # noqa: E731
    add = fake.add
    add(
        make_mail("Welcome to your new mailbox", sender="team@example.com", when=days(2)),
        labels=("INBOX", "UNREAD"),
        when=days(2),
    )
    add(
        make_mail(
            "Your order has shipped",
            sender="shop@example.com",
            when=days(5),
            html='<p>On its way.</p><img src="http://images.example.com/track.png" alt="tracking">',
        ),
        labels=("INBOX",),
        when=days(5),
    )
    # Mail the Marketing starter rule fits (an unsubscribe link, an offer in the subject).
    for subject, age in (("Big sale: 30% off shoes", 15), ("Spring offers just for you", 25)):
        add(
            make_mail(
                subject,
                sender="deals@shop.example.com",
                when=days(age),
                headers={"List-Unsubscribe": "<mailto:unsubscribe@shop.example.com>"},
            ),
            labels=("INBOX",),
            when=days(age),
        )
    add(
        make_mail(
            "Trip photos",
            when=days(10),
            html='<p>Look!</p><img src="cid:photo1" alt="photo">',
            inline_png_cid="photo1",
        ),
        labels=("INBOX", "STARRED", "IMPORTANT"),
        when=days(10),
    )
    add(
        make_mail(
            "Invoice October",
            sender="billing@example.com",
            when=days(20),
            attachments=pdf("invoice.pdf"),
        ),
        labels=("INBOX", receipts),
        when=days(20),
    )
    plan = add(
        make_mail("Project plan", sender="boss@example.com", when=days(35)),
        labels=("INBOX", work, "IMPORTANT"),
        when=days(35),
    )
    add(
        make_mail("Re: Project plan", sender=me, to="boss@example.com", when=days(34)),
        labels=("SENT", work),
        when=days(34),
        thread_id=plan,
    )
    add(
        make_mail(
            "Weekly digest #52",
            sender="news@example.com",
            when=days(60),
            html="<p>News</p><script>alert('x')</script><p onclick=\"steal()\">More</p>",
        ),
        labels=("INBOX",),
        when=days(60),
    )
    add(
        make_mail(
            "Boarding pass BLR to DEL",
            sender="airline@example.com",
            when=days(90),
            attachments=pdf("boarding-pass.pdf"),
        ),
        labels=("INBOX", travel),
        when=days(90),
    )
    add(
        make_mail(
            "Statement for June",
            sender="bank@example.com",
            when=days(120),
            attachments=pdf("statement-june.pdf"),
        ),
        labels=("INBOX", receipts),
        when=days(120),
    )
    add(
        make_mail(
            "Photos from the weekend",
            when=days(200),
            attachments=(("a.png", "image/png", TINY_PNG), ("b.png", "image/png", TINY_PNG)),
        ),
        labels=("INBOX",),
        when=days(200),
    )
    add(
        make_mail("Old newsletter #1", sender="news@example.com", when=days(300)),
        labels=("INBOX",),
        when=days(300),
    )
    add(make_mail("Holiday plans", when=days(380)), labels=("INBOX", "STARRED"), when=days(380))
    add(
        make_mail(
            "Statement for 2024",
            sender="bank@example.com",
            when=days(400),
            attachments=pdf("statement-2024.pdf"),
        ),
        labels=(receipts,),
        when=days(400),
    )
    add(
        make_mail(
            "Big video",
            when=days(450),
            attachments=(("holiday.mp4", "video/mp4", b"\0" * 6_000_000),),
        ),
        labels=("INBOX",),
        when=days(450),
    )
    add(
        make_mail("Conference tickets", sender="events@example.com", when=days(500)),
        labels=(travel,),
        when=days(500),
    )
    add(
        make_mail("Old receipt", sender="shop@example.com", when=days(600)),
        labels=(receipts,),
        when=days(600),
    )
    add(
        make_mail("Family recipe", sender="mom@example.com", when=days(700)),
        labels=("INBOX",),
        when=days(700),
    )
    add(
        make_mail("Weekly digest #1", sender="news@example.com", when=days(750)),
        labels=("INBOX",),
        when=days(750),
    )
    contract = add(
        make_mail(
            "Contract draft",
            sender="boss@example.com",
            when=days(800),
            attachments=(
                (
                    "contract.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    b"PK\x03\x04fake",
                ),
            ),
        ),
        labels=(work,),
        when=days(800),
    )
    add(
        make_mail("Re: Contract draft", sender=me, to="boss@example.com", when=days(799)),
        labels=("SENT", work),
        when=days(799),
        thread_id=contract,
    )
    add(
        make_mail("Car service", sender="garage@example.com", when=days(900)),
        labels=("INBOX",),
        when=days(900),
    )
    add(
        make_mail(
            "Tax papers",
            sender="accountant@example.com",
            when=days(950),
            attachments=pdf("tax.pdf"),
        ),
        labels=("INBOX",),
        when=days(950),
    )
    add(make_mail("First mail", when=days(1000)), labels=("INBOX",), when=days(1000))
    add(
        make_mail("Hello", sender=me, to="friend@example.com", when=days(1050)),
        labels=("SENT",),
        when=days(1050),
    )
    add(
        make_mail("You won a prize", sender="prize@example.com", when=days(3)),
        labels=("SPAM",),
        when=days(3),
    )
    add(make_mail("Unfinished", sender=me, when=days(1)), labels=("DRAFT",), when=days(1))


def _subject(raw: bytes) -> str:
    return str(email.message_from_bytes(raw).get("Subject") or "").strip()


def main(argv: list[str] | None = None) -> int:
    """Development and the browser test only: change the pretend mailbox from outside, as the
    owner would in Gmail. `delete --file F --subject S` deletes every mail with subject S.
    Like the pretend accounts, it refuses to run without DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1."""
    parser = argparse.ArgumentParser(prog="python -m app.devtools.fake_gmail")
    commands = parser.add_subparsers(dest="command", required=True)
    delete = commands.add_parser("delete", help="delete mail by subject, as if deleted in Gmail")
    delete.add_argument("--file", required=True, type=Path)
    delete.add_argument("--subject", required=True)
    args = parser.parse_args(argv)
    allowed = os.environ.get("DRIVESYNC_ALLOW_LOCAL_ACCOUNTS", "").strip().lower()
    if allowed not in ("1", "true", "yes", "on"):
        print(
            "Refusing: the pretend Gmail is for development and tests only. "
            "Set DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 to use it.",
            file=sys.stderr,
        )
        return 2
    fake = FakeGmail.load(args.file)
    gone = [m.id for m in fake.messages.values() if _subject(m.raw) == args.subject]
    for mid in gone:
        fake.remove(mid)
    print(f"deleted {len(gone)}")
    return 0 if gone else 1


if __name__ == "__main__":
    raise SystemExit(main())
