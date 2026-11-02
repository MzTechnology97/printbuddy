"""Columns added to ``farm_`` tables after their first release.

``create_all`` creates missing tables but never alters existing ones, and upstream's
``run_migrations`` only knows upstream tables. This hook runs right after every
``create_all`` (app start-up) and adds the missing farm columns, without foreign keys
(SQLite cannot add them later; new databases get them from the models).
"""

from sqlalchemy import event, inspect, text

from backend.app.core.database import Base

_COLUMNS: dict[str, dict[str, str]] = {}


def add_columns(table: str, columns: dict[str, str]) -> None:
    """Register ``{column: SQL type}`` to add to ``table`` when an older database lacks them."""
    _COLUMNS.setdefault(table, {}).update(columns)


@event.listens_for(Base.metadata, "after_create")
def _add_missing_columns(target, connection, **kw) -> None:
    insp = inspect(connection)
    for table, columns in _COLUMNS.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, sql_type in columns.items():
            if name not in have:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
