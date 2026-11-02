"""Add Google identities and support accounts without a local password."""

import secrets

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])


def downgrade():
    # Google-only accounts keep an unusable password if Google support is removed.
    unusable = PasswordHasher().hash(secrets.token_urlsafe(48))
    op.get_bind().execute(
        sa.text("UPDATE users SET password_hash = :value WHERE password_hash IS NULL"), {"value": unusable}
    )
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
