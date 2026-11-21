"""SQLite-specific SQLAlchemy connection initialization."""

from sqlalchemy import event


def enable_sqlite_foreign_keys(engine) -> None:
    """Enable FK enforcement on every SQLite DBAPI connection.

    AsyncEngine exposes its event target as ``sync_engine``. Accepting either
    form keeps the hook usable by application and Alembic engine creation.
    """
    sync_engine = getattr(engine, "sync_engine", engine)
    if sync_engine.dialect.name != "sqlite":
        return

    @event.listens_for(sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
