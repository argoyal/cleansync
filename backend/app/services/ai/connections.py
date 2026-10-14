"""Model connections and which model each task uses (AI rules spec §4). An API key and the
values of extra headers are files under app/data/secrets/ai/ (mode 600), never columns, and
are handed to the log redactor as soon as they are known."""

import json
import os
import secrets as pysecrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import as_utc, utcnow
from app.models import AiConnection, AiTask
from app.models.enums import AiTaskName
from app.services.ai.limits import DOWN, Pause, check_reset


class _Keep:
    pass


KEEP = _Keep()  # "leave the saved value as it is"
Keep = _Keep


class ConnectionError_(ValueError):
    """A plain reason, shown as is."""


class ConnectionNotFound(ConnectionError_):
    """The connection id is not (or no longer) saved."""


@dataclass(frozen=True)
class AiTarget:
    connection_id: int
    base_url: str
    api_key: str | None
    headers: dict[str, str]
    model: str
    temperature: float
    timeout_s: float
    skip_pause: bool = False  # Test: call even while paused


def clean_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConnectionError_(
            "The address must start with http:// or https://, for example http://ollama:11434/v1."
        )
    if "@" in parts.netloc:
        raise ConnectionError_(
            "Don't put a user name or password in the address. Use the API key field."
        )
    if url.lower().endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    return url


def _server(url: str) -> tuple:
    """Scheme, host and port: a change here means another server."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{pysecrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _clear(conn: AiConnection) -> bool:
    had = conn.pause_kind is not None or (conn.down_streak or 0) > 0
    conn.paused_until = conn.pause_kind = conn.pause_reason = None
    conn.pause_detail = conn.paused_at = None
    conn.down_streak = 0
    return had


class AiSettings:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        secrets_dir: Path,
        on_secret: Callable[[str], None] = lambda _s: None,
    ) -> None:
        self.sf = session_factory
        self.dir = secrets_dir / "ai"
        self.on_secret = on_secret

    # ---- files -------------------------------------------------------------------------
    def _key_file(self, conn_id: int) -> Path:
        return self.dir / f"{conn_id}.key"

    def _headers_file(self, conn_id: int) -> Path:
        return self.dir / f"{conn_id}.headers.json"

    def _save_key(self, conn_id: int, key: str | None) -> bool:
        key = (key or "").strip()  # a pasted key often ends with a newline
        if key:
            _write_private(self._key_file(conn_id), key)
            self.on_secret(key)
            return True
        self._key_file(conn_id).unlink(missing_ok=True)
        return False

    def _save_headers(self, conn_id: int, headers: dict[str, str]) -> list[str]:
        clean = {str(k).strip(): str(v).strip() for k, v in headers.items() if str(k).strip()}
        if clean:
            _write_private(self._headers_file(conn_id), json.dumps(clean))
            for v in clean.values():
                self.on_secret(v)
        else:
            self._headers_file(conn_id).unlink(missing_ok=True)
        return sorted(clean)

    def _read_key(self, conn_id: int) -> str | None:
        try:
            return self._key_file(conn_id).read_text() or None
        except FileNotFoundError:
            return None

    def _read_headers(self, conn_id: int) -> dict[str, str]:
        try:
            data = json.loads(self._headers_file(conn_id).read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    # ---- connections -------------------------------------------------------------------
    def create(
        self,
        name: str,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        resets_at: str = "00:00",
        resets_tz: str = "UTC",
    ) -> AiConnection:
        url = clean_base_url(base_url)
        try:
            check_reset(resets_at, resets_tz)
        except ValueError as e:
            raise ConnectionError_(str(e)) from None
        with self.sf() as db:
            conn = AiConnection(
                name=(name or "").strip() or urlsplit(url).netloc,
                base_url=url,
                resets_at=resets_at,
                resets_tz=resets_tz,
            )
            db.add(conn)
            db.flush()
            conn.has_key = self._save_key(conn.id, api_key)
            conn.header_names = self._save_headers(conn.id, headers or {})
            db.commit()
            return conn

    def _get(self, db: Session, conn_id: int) -> AiConnection:
        conn = db.get(AiConnection, conn_id)
        if conn is None:
            raise ConnectionNotFound("That connection no longer exists.")
        return conn

    def update(
        self,
        conn_id: int,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: "str | None | Keep" = KEEP,
        headers: "dict[str, str] | Keep" = KEEP,
    ) -> AiConnection:
        server_changed = False
        with self.sf() as db:
            conn = self._get(db, conn_id)
            if name is not None and name.strip():
                conn.name = name.strip()
            if base_url is not None:
                url = clean_base_url(base_url)
                if url != conn.base_url:
                    if _server(url) != _server(conn.base_url):
                        # Another server must not be sent the old one's key or headers.
                        api_key = None if isinstance(api_key, _Keep) else api_key
                        headers = {} if isinstance(headers, _Keep) else headers
                    conn.base_url = url
                    server_changed = True
                    conn.modes = {}  # another server: learn its answer modes again
            if not isinstance(api_key, _Keep):
                conn.has_key = self._save_key(conn.id, api_key)
                server_changed = True
            if not isinstance(headers, _Keep):
                conn.header_names = self._save_headers(conn.id, headers)
                server_changed = True
            if server_changed:
                _clear(conn)  # new address, key or headers: the old pause no longer applies
            db.commit()
            return conn

    def delete(self, conn_id: int) -> None:
        with self.sf() as db:
            conn = self._get(db, conn_id)
            db.delete(conn)  # ai_tasks.connection_id becomes NULL
            db.commit()
        self._key_file(conn_id).unlink(missing_ok=True)
        self._headers_file(conn_id).unlink(missing_ok=True)

    def connections(self) -> list[AiConnection]:
        with self.sf() as db:
            return list(db.scalars(select(AiConnection).order_by(AiConnection.id)))

    # ---- tasks -------------------------------------------------------------------------
    def set_task(
        self,
        task: AiTaskName,
        connection_id: int | None,
        model: str,
        temperature: float = 0.0,
        timeout_s: int = 60,
    ) -> AiTask:
        with self.sf() as db:
            if connection_id is not None:
                self._get(db, connection_id)
            row = db.get(AiTask, str(task)) or AiTask(task=str(task))
            row.connection_id = connection_id
            if connection_id is not None:
                _clear(db.get(AiConnection, connection_id))
            row.model = (model or "").strip()
            row.temperature = min(max(float(temperature), 0.0), 2.0)
            row.timeout_s = min(max(int(timeout_s), 5), 600)
            db.add(row)
            db.commit()
            return row

    def tasks(self) -> dict[str, AiTask]:
        with self.sf() as db:
            return {t.task: t for t in db.scalars(select(AiTask))}

    def target_for(
        self, connection_id: int, model: str, temperature: float = 0.0, timeout_s: float = 60
    ) -> AiTarget:
        with self.sf() as db:
            conn = self._get(db, connection_id)
            url = conn.base_url
        return AiTarget(
            connection_id=connection_id,
            base_url=url,
            api_key=self._read_key(connection_id),
            headers=self._read_headers(connection_id),
            model=model,
            temperature=temperature,
            timeout_s=float(timeout_s),
        )

    def target(self, task: AiTaskName) -> AiTarget | None:
        with self.sf() as db:
            row = db.get(AiTask, str(task))
            if (row is None or row.connection_id is None or not row.model) and (
                task in (AiTaskName.WRITING_RULES, AiTaskName.UNSUBSCRIBE)
            ):
                row = db.get(AiTask, str(AiTaskName.SORTING))
            if row is None or row.connection_id is None or not row.model:
                return None
            conn_id, model, temp, timeout = (
                row.connection_id,
                row.model,
                row.temperature,
                row.timeout_s,
            )
        return self.target_for(conn_id, model, temp, timeout)

    # ---- answer modes (spec §4: remembered per connection and model) --------------------
    def remembered_mode(self, connection_id: int, model: str) -> str | None:
        with self.sf() as db:
            conn = db.get(AiConnection, connection_id)
            return (conn.modes or {}).get(model) if conn else None

    def remember_mode(self, connection_id: int, model: str, mode: str) -> None:
        with self.sf() as db:
            conn = db.get(AiConnection, connection_id)
            if conn is None:
                return
            conn.modes = {**(conn.modes or {}), model: mode}  # a new dict: JSON change seen
            db.commit()

    # ---- pauses (model limits spec §6-7): saved here, never only in memory --------------
    def pause_of(self, connection_id: int, now: datetime | None = None) -> Pause | None:
        now = now or utcnow()
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None or c.pause_kind is None:
                return None
            until = as_utc(c.paused_until) if c.paused_until else None
            if until is not None and until <= now:
                return None
            return Pause(
                c.pause_kind,
                until,
                c.pause_reason or "",
                c.pause_detail,
                as_utc(c.paused_at) if c.paused_at else now,
            )

    def limits_of(self, connection_id: int) -> tuple[str, str, int]:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None:
                return "00:00", "UTC", 0
            return c.resets_at or "00:00", c.resets_tz or "UTC", c.down_streak or 0

    @staticmethod
    def _running_pause(c: AiConnection, new_until: datetime | None, now: datetime) -> Pause | None:
        """The saved pause when it is still running and ends later than the new one (no end,
        needs_you, counts as the latest). None when the new pause should replace it."""
        if c.pause_kind is None:
            return None
        old = as_utc(c.paused_until) if c.paused_until else None
        if old is not None and old <= now:
            return None
        if old is None or (new_until is not None and new_until <= old):
            return Pause(
                c.pause_kind,
                old,
                c.pause_reason or "",
                c.pause_detail,
                as_utc(c.paused_at) if c.paused_at else now,
            )
        return None

    def set_pause(
        self,
        connection_id: int,
        kind: str,
        until: datetime | None,
        reason: str,
        detail: str | None,
        now: datetime,
    ) -> Pause:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is not None:
                if kind == DOWN:
                    c.down_streak = (c.down_streak or 0) + 1
                kept = self._running_pause(c, until, now)
                if kept is not None:  # the later end wins: a shorter pause never cuts it short
                    db.commit()
                    return kept
                c.pause_kind, c.paused_until, c.pause_reason = kind, until, reason
                c.pause_detail, c.paused_at = detail, now
                db.commit()
        return Pause(kind, until, reason, detail, now)

    def clear_pause(self, connection_id: int) -> bool:
        with self.sf() as db:
            c = db.get(AiConnection, connection_id)
            if c is None or not _clear(c):
                return False
            db.commit()
            return True

    def secrets_of(self, connection_id: int) -> list[str]:
        key = self._read_key(connection_id)
        return [*([key] if key else []), *self._read_headers(connection_id).values()]

    def set_resets(self, connection_id: int, resets_at: str, resets_tz: str) -> AiConnection:
        try:
            check_reset(resets_at, resets_tz)
        except ValueError as e:
            raise ConnectionError_(str(e)) from None
        with self.sf() as db:
            conn = self._get(db, connection_id)
            conn.resets_at, conn.resets_tz = resets_at, resets_tz
            db.commit()
            return conn

    def pause_for_task(self, task: AiTaskName) -> tuple[int, str, Pause] | None:
        t = self.target(task)
        if t is None:
            return None
        p = self.pause_of(t.connection_id)
        if p is None:
            return None
        with self.sf() as db:
            c = db.get(AiConnection, t.connection_id)
            return (t.connection_id, c.name if c else "", p)

    def load_secrets(self) -> None:
        for conn in self.connections():
            key = self._read_key(conn.id)
            if key:
                self.on_secret(key)
            for v in self._read_headers(conn.id).values():
                self.on_secret(v)
