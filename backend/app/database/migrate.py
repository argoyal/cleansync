from pathlib import Path

from alembic import command
from alembic.config import Config

from app.database.engine import make_engine

_MIGRATIONS = Path(__file__).parent / "migrations"


def run_migrations(db_file: Path) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    engine = make_engine(db_file)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
    engine.dispose()
