from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.sqlite import enable_sqlite_foreign_keys

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
enable_sqlite_foreign_keys(engine)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
