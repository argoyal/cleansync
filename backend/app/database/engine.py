from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text


def make_engine(db_file: Path) -> Engine:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return engine


def integrity_ok(engine: Engine) -> bool:
    with engine.connect() as c:
        return c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
