from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401

target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    from pathlib import Path
    if settings().database_url.startswith("sqlite:///"):
        Path(settings().database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
