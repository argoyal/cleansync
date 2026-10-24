from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.database.engine import make_engine

_MIGRATIONS = Path(__file__).parent / "migrations"


def run_migrations(db_file: Path, revision: str = "head", *, wal: bool = False) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    engine = make_engine(db_file, wal=wal)
    try:
        with engine.connect() as connection:
            # Some migrations rebuild tables (SQLite can't alter them in place). With foreign
            # keys on, dropping the old table would cascade-delete the rows that point at it.
            # The pragma only takes effect outside a transaction, so set it before Alembic's.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            with connection.begin():
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, revision)
            bad = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
            if bad:
                raise RuntimeError(f"database migration broke {len(bad)} foreign key links")
    finally:
        engine.dispose()
