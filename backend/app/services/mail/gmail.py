"""Gmail through its REST API (spec §3.2), every call through the paced, counted client."""

import base64
import json
import re
import secrets
from datetime import UTC, datetime

import httpx

from app.services.drive_api.tokens import DriveApiError, DriveAuthError
from app.services.google.client import GoogleClient, error_reasons
from app.services.mail.adapter import (
    GONE,
    IN_INBOX,
    IN_TRASH,
    TOO_OLD,
    MailApiError,
    MailAuthError,
    MailboxInfo,
    MailChanges,
    MailMeta,
    MailNotFound,
    MailSetupProblem,
    MailUnreachable,
    ProviderLabel,
    ProviderState,
    TooOld,
)

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
# Gmail takes at most 5 MB in one multipart upload: bigger mail goes up resumable.
RESUMABLE_OVER = 4 * 1024 * 1024
# Where a resumable upload's session may live: the bearer token is never sent elsewhere.
UPLOAD_HOSTS = frozenset({"gmail.googleapis.com", "www.googleapis.com"})
IMPORT_PARAMS = {"internalDateSource": "dateHeader", "neverMarkSpam": "true"}
# Spec §4.3. Chats are left out too (spec §2).
LIST_QUERY = "-in:spam -in:trash -in:drafts -in:chats"
PAGE_SIZE = 500
HISTORY_TYPES = ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved")
SCOPE_REASONS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
AUTH_MSG = "Google says the login expired or was revoked. Reconnect the account."
SCOPE_MSG = "Google no longer lets CleanSync read this account's mail. Set up mail again."
API_OFF_MSG = (
    "The Gmail API is turned off for this Google Cloud project. Turn it on in Google Cloud "
    "(APIs & Services → Library → Gmail API) and try again."
)
API_OFF_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})


def api_turned_off(resp: httpx.Response) -> bool:
    """Google's answer when the Gmail API is not turned on for the Cloud project: the
    reason sits in `errors` (accessNotConfigured) or in `details` (SERVICE_DISABLED)."""
    reasons = set(error_reasons(resp))
    try:
        details = resp.json()["error"].get("details") or []
    except (ValueError, KeyError, TypeError, AttributeError):
        details = []
    reasons |= {str(d.get("reason")) for d in details if isinstance(d, dict)}
    return bool(reasons & API_OFF_REASONS)


# A Message-ID safe to put in a Gmail search. Refused (such mail is imported without a
# lookup): spaces, quotes, brackets, and { } , | and a leading - , which Gmail's search may
# read as its own syntax; and invisible characters (zero-width and direction marks, the soft
# hyphen), which hide what is searched.
_BAD = '\\s<>"(){},|\u00ad\u200b-\u200f\u2060\ufeff'
_MSGID = re.compile(rf"[^{_BAD}-][^{_BAD}]{{0,899}}")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def search_message_id(value: str | None) -> str | None:
    """The Message-ID header without its <>, or None when it can't be searched for safely."""
    mid = (value or "").strip().removeprefix("<").removesuffix(">")
    return mid if _MSGID.fullmatch(mid) else None


def same_message_id(a: str | None, b: str | None) -> bool:
    """Two Message-ID headers name the same mail. Surrounding whitespace and <> are left out;
    the part before the last @ must match exactly (RFC 5322 leaves its case to the sender),
    the domain after it ignoring only A-Z's case (casefold() or lower() would make other
    letters equal too, say ß and ss)."""

    def norm(v: str | None) -> tuple[str, str] | None:
        v = (v or "").strip().removeprefix("<").removesuffix(">").strip()
        if not v:
            return None
        local, at, domain = v.rpartition("@")
        return (local, domain.translate(_ASCII_LOWER)) if at else (v, "")

    na, nb = norm(a), norm(b)
    return na is not None and na == nb


# The one place the shape of a Gmail message id is written (the store uses valid_mail_id).
_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")


def valid_mail_id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _mail_id(value: str) -> str:
    if valid_mail_id(value) is None:
        raise ValueError("not a usable mail id")
    return value


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise MailApiError("Gmail gave an unexpected answer") from None
    if not isinstance(data, dict):
        raise MailApiError("Gmail gave an unexpected answer")
    return data


class StaticTokens:
    """A token source that always hands out the same token (the fake Gmail takes any)."""

    def __init__(self, token: str) -> None:
        self.token = token

    async def access_token(self, remote_name: str) -> str:
        return self.token

    def forget(self, remote_name: str) -> None:
        return None


class GmailAdapter:
    def __init__(
        self, google: GoogleClient, tokens, remote_name: str, *, page_size: int = PAGE_SIZE
    ) -> None:
        self.google = google
        self.tokens = tokens
        self.remote = remote_name
        self.page_size = page_size

    async def _call(
        self,
        op: str,
        method: str,
        path: str,
        *,
        upload: bool = False,
        url: str | None = None,
        headers: dict | None = None,
        **kwargs,
    ) -> httpx.Response:
        url = url or (GMAIL_UPLOAD if upload else GMAIL) + path
        resp: httpx.Response | None = None
        for attempt in (1, 2):
            try:
                token = await self.tokens.access_token(self.remote)
            except DriveAuthError:
                raise MailAuthError(AUTH_MSG, login=True) from None
            except DriveApiError as e:
                raise MailUnreachable(str(e)) from None
            resp = await self.google.request(
                self.remote,
                op,
                method,
                url,
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
                **kwargs,
            )
            if resp is None:
                raise MailUnreachable("could not reach Gmail")
            if resp.status_code == 401 and attempt == 1:
                self.tokens.forget(self.remote)  # expired early: one try with a new token
                continue
            break
        assert resp is not None
        if resp.status_code == 401:
            raise MailAuthError(AUTH_MSG, login=True)
        if resp.status_code == 403 and error_reasons(resp) & SCOPE_REASONS:
            raise MailAuthError(SCOPE_MSG, login=False)
        if resp.status_code == 403 and api_turned_off(resp):
            raise MailSetupProblem(API_OFF_MSG, 403)
        if resp.status_code == 404:
            raise MailNotFound("Gmail has no such mail", 404)
        if resp.status_code >= 500:
            raise MailUnreachable(
                f"Gmail's servers had a problem (HTTP {resp.status_code})", resp.status_code
            )
        if resp.status_code >= 400:
            raise MailApiError(
                f"Gmail refused the request (HTTP {resp.status_code})", resp.status_code
            )
        return resp

    async def profile(self) -> MailboxInfo:
        data = _json(await self._call("gmail.profile", "GET", "/profile"))
        try:
            return MailboxInfo(
                email=str(data["emailAddress"]),
                total=int(data.get("messagesTotal", 0)),
                history_id=str(data["historyId"]),
            )
        except (KeyError, TypeError, ValueError):
            raise MailApiError("Gmail gave an unexpected answer") from None

    async def list_page(self, cursor: str | None) -> tuple[list[str], str | None]:
        params = {"q": LIST_QUERY, "maxResults": str(self.page_size), "includeSpamTrash": "false"}
        if cursor:
            params["pageToken"] = cursor
        data = _json(await self._call("gmail.messages.list", "GET", "/messages", params=params))
        ids = [
            mid
            for m in data.get("messages") or []
            if isinstance(m, dict) and (mid := valid_mail_id(m.get("id")))
        ]
        nxt = data.get("nextPageToken")
        return ids, nxt if isinstance(nxt, str) and nxt else None

    @staticmethod
    def _meta(data: dict) -> MailMeta:
        try:
            return MailMeta(
                id=_mail_id(str(data["id"])),
                thread_id=str(data.get("threadId") or data["id"]),
                labels=tuple(str(x) for x in data.get("labelIds") or ()),
                internal_date=datetime.fromtimestamp(int(data["internalDate"]) / 1000, UTC),
                size=int(data.get("sizeEstimate", 0)),
                history_id=str(data["historyId"]) if data.get("historyId") else None,
                snippet=str(data.get("snippet") or ""),
            )
        except (KeyError, TypeError, ValueError):
            raise MailApiError("Gmail gave an unexpected answer") from None

    async def fetch_raw(self, id: str) -> tuple[bytes, MailMeta]:
        path = f"/messages/{_mail_id(id)}"
        data = _json(await self._call("gmail.messages.get", "GET", path, params={"format": "raw"}))
        raw_text = data.get("raw")
        if not isinstance(raw_text, str):
            raise MailApiError("Gmail gave no mail content")
        try:
            raw = base64.urlsafe_b64decode(raw_text + "=" * (-len(raw_text) % 4))
        except ValueError:
            raise MailApiError("Gmail gave mail content that can't be read") from None
        return raw, self._meta(data)

    async def changes_since(self, history_id: str) -> MailChanges | TooOld:
        changes = MailChanges(history_id=history_id)
        token: str | None = None
        while True:
            params: list[tuple[str, str]] = [("startHistoryId", history_id)]
            params += [("historyTypes", t) for t in HISTORY_TYPES]
            if token:
                params.append(("pageToken", token))
            try:
                resp = await self._call("gmail.history.list", "GET", "/history", params=params)
            except MailNotFound:
                return TOO_OLD
            data = _json(resp)
            for h in data.get("history") or []:
                if not isinstance(h, dict):
                    continue
                for item in h.get("messagesAdded") or []:
                    m = item.get("message") or {}
                    if mid := valid_mail_id(m.get("id")):
                        changes.added[mid] = tuple(m.get("labelIds") or ())
                        changes.deleted.discard(mid)
                for item in h.get("messagesDeleted") or []:
                    m = item.get("message") or {}
                    if mid := valid_mail_id(m.get("id")):
                        changes.deleted.add(mid)
                        changes.added.pop(mid, None)
                        changes.labels.pop(mid, None)
                for key in ("labelsAdded", "labelsRemoved"):
                    for item in h.get(key) or []:
                        m = item.get("message") or {}
                        if mid := valid_mail_id(m.get("id")):
                            now = tuple(m.get("labelIds") or ())
                            if mid in changes.added:
                                changes.added[mid] = now
                            elif mid not in changes.deleted:
                                changes.labels[mid] = now
            if data.get("historyId"):
                changes.history_id = str(data["historyId"])
            token = data.get("nextPageToken") or None
            if not token:
                return changes

    async def labels(self) -> list[ProviderLabel]:
        data = _json(await self._call("gmail.labels.list", "GET", "/labels"))
        out = []
        for lab in data.get("labels") or []:
            if isinstance(lab, dict) and lab.get("id") and lab.get("name"):
                kind = "user" if lab.get("type") == "user" else "system"
                out.append(ProviderLabel(str(lab["id"]), str(lab["name"]), kind))
        return out

    async def trash(self, id: str) -> None:
        await self._call("gmail.messages.trash", "POST", f"/messages/{_mail_id(id)}/trash")

    async def untrash(self, id: str) -> None:
        await self._call("gmail.messages.untrash", "POST", f"/messages/{_mail_id(id)}/untrash")

    async def state(self, id: str) -> ProviderState:
        try:
            resp = await self._call(
                "gmail.messages.get",
                "GET",
                f"/messages/{_mail_id(id)}",
                params={"format": "minimal"},
            )
        except MailNotFound:
            return ProviderState(GONE, (), None)
        data = _json(resp)
        labels = tuple(str(x) for x in data.get("labelIds") or ())
        size = data.get("sizeEstimate")
        where = IN_TRASH if "TRASH" in labels else IN_INBOX
        return ProviderState(where, labels, int(size) if isinstance(size, int) else None)

    async def find_by_message_id(self, message_id: str) -> list[str]:
        """Gmail's ids of every mail with this Message-ID header, Spam and Trash included."""
        mid = search_message_id(message_id)
        if mid is None:
            raise ValueError("not a Message-ID that can be searched for")
        params = {"q": f"rfc822msgid:{mid}", "includeSpamTrash": "true", "maxResults": "10"}
        data = _json(await self._call("gmail.messages.list", "GET", "/messages", params=params))
        found = [
            gid
            for m in data.get("messages") or []
            if isinstance(m, dict) and (gid := valid_mail_id(m.get("id")))
        ]
        # Gmail's search is not an exact match: only a mail whose own Message-ID header is
        # this one counts.
        return [gid for gid in found if same_message_id(await self._message_id(gid), mid)]

    async def _message_id(self, id: str) -> str | None:
        """One mail's Message-ID header (None when it has none or is gone)."""
        try:
            resp = await self._call(
                "gmail.messages.get",
                "GET",
                f"/messages/{_mail_id(id)}",
                params={"format": "metadata", "metadataHeaders": "Message-ID"},
            )
        except MailNotFound:
            return None
        headers = (_json(resp).get("payload") or {}).get("headers") or []
        for h in headers:
            if isinstance(h, dict) and str(h.get("name", "")).lower() == "message-id":
                return str(h.get("value") or "")
        return None

    async def import_raw(self, raw: bytes, labels: list[str], internal_date: datetime) -> str:
        """Put a kept mail back into Gmail (Plan 4's restore). Gmail takes the date from the
        mail's Date header (internalDateSource=dateHeader), so internal_date is only for
        providers that need it. Mail over 4 MB goes up as a resumable upload."""
        if len(raw) > RESUMABLE_OVER:
            resp = await self._import_resumable(raw, labels)
        else:
            resp = await self._import_multipart(raw, labels)
        new_id = valid_mail_id(_json(resp).get("id"))
        if new_id is None:
            raise MailApiError("Gmail gave an unexpected answer to the import")
        return new_id

    async def _import_multipart(self, raw: bytes, labels: list[str]) -> httpx.Response:
        boundary = "cleansync-" + secrets.token_hex(12)
        meta = json.dumps({"labelIds": list(labels)}).encode()
        body = b"".join(
            [
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                meta,
                f"\r\n--{boundary}\r\nContent-Type: message/rfc822\r\n\r\n".encode(),
                raw,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        return await self._call(
            "gmail.messages.import",
            "POST",
            "/messages/import",
            upload=True,
            params={"uploadType": "multipart", **IMPORT_PARAMS},
            content=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )

    async def _import_resumable(self, raw: bytes, labels: list[str]) -> httpx.Response:
        """A POST with the labels opens an upload session; a PUT to its address carries the
        mail. Counted as one import. The whole mail goes in that one PUT (a mail is at most
        tens of MB): there is no chunking or resuming of a cut-off upload, so Gmail's 308
        ("send the rest") would surface as a plain refusal of this mail."""
        start = await self._call(
            "gmail.messages.import",
            "POST",
            "/messages/import",
            upload=True,
            params={"uploadType": "resumable", **IMPORT_PARAMS},
            content=json.dumps({"labelIds": list(labels)}).encode(),
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "message/rfc822",
                "X-Upload-Content-Length": str(len(raw)),
            },
        )
        session = start.headers.get("location", "")
        try:
            where = httpx.URL(session)
        except (httpx.InvalidURL, TypeError):
            where = None
        if where is None or where.scheme != "https" or where.host not in UPLOAD_HOSTS:
            raise MailApiError("Gmail gave an unexpected answer to the import")
        return await self._call(
            "gmail.messages.import.upload",
            "PUT",
            "",
            url=session,
            content=raw,
            headers={"Content-Type": "message/rfc822"},
        )
