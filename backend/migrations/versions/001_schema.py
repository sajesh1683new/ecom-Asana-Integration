"""Initial bridge schema, including persisted mock integration and QA evidence."""

from alembic import op
from backend.app.db import Base
from backend.app import models  # noqa: F401

revision = "001"
down_revision = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
