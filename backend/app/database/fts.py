"""The full-text search table (spec §4.1). SQLite's FTS5 makes it as a virtual table plus
helper tables (mail_fts_data, mail_fts_idx, …) that Alembic cannot describe, so Alembic's
comparisons leave every table whose name starts with mail_fts out."""

FTS_TABLE = "mail_fts"


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(FTS_TABLE))
