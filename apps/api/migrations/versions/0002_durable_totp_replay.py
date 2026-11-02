"""Preserve accepted TOTP time-step fencing across Redis loss."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("last_totp_step", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("users", "last_totp_step")
