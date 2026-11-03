"""Track addresses confirmed by PulseGrid email links."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("users", "email_verified_at")
