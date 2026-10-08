from alembic import context
from backend.app.db import Base, engine
from backend.app import models  # noqa: F401
with engine.connect() as connection:
    context.configure(connection=connection,target_metadata=Base.metadata,render_as_batch=True)
    with context.begin_transaction(): context.run_migrations()
