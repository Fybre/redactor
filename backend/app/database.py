from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from app.models.job import Job  # noqa: ensure model is registered
    from app.models.region import RedactionRegion  # noqa: ensure model is registered
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Additive migrations — safe to run every startup on SQLite
        for col, typedef in [
            ("webhook_headers",            "JSON"),
            ("webhook_secret",             "VARCHAR"),
            ("webhook_include_file",       "BOOLEAN DEFAULT 0"),
            ("webhook_template",           "VARCHAR"),
            ("webhook_extra",              "JSON"),
            ("validation_url",             "VARCHAR"),
            ("completion_callback_url",    "VARCHAR"),
            ("completion_callback_headers","JSON"),
            ("completion_callback_body",   "VARCHAR"),
        ]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE jobs ADD COLUMN {col} {typedef}"
                )
            except Exception:
                pass  # column already exists


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
