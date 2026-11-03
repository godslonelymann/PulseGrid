"""Enable account email alerts for existing email-confirmed users without a preference."""

import uuid

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet
from pulsegrid.config import settings

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    users = connection.execute(
        sa.text("""
            SELECT u.id, u.email FROM users u
            WHERE u.email_verified_at IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM notification_channels c WHERE c.owner_id = u.id AND c.kind = 'email'
            )
        """)
    ).all()
    if users:
        cipher = Fernet(settings().encryption_key.encode())
        connection.execute(
            sa.text("""
                INSERT INTO notification_channels (id, owner_id, name, kind, config, enabled)
                VALUES (:id, :owner, 'Email alerts', 'email', :config, true)
            """),
            [
                {"id": uuid.uuid4(), "owner": user.id, "config": cipher.encrypt(user.email.encode()).decode()}
                for user in users
            ],
        )


def downgrade():
    # Older releases can use these channels too. Preserve preferences and delivery history.
    pass
