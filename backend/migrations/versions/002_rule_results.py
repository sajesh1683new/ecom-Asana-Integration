"""Persist explicit rule outcomes, including blocked expectations."""

from alembic import op
from backend.app.models import RuleResult

revision = "002"
down_revision = "001"


def upgrade():
    RuleResult.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    RuleResult.__table__.drop(op.get_bind(), checkfirst=True)
