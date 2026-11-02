"""Allow automatic grouping and alerts for current and future groups."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("buckets", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(
        "one_default_bucket_per_owner", "buckets", ["owner_id"], unique=True, postgresql_where=sa.text("is_default")
    )
    op.add_column(
        "notification_channels", sa.Column("all_groups", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    op.drop_column("notification_channels", "all_groups")
    op.drop_index("one_default_bucket_per_owner", table_name="buckets")
    op.drop_column("buckets", "is_default")
