"""add webhook_events table

Records every inbound hit on /api/webhooks/ghl so a dropped message is
visible in the dashboard instead of only in a log file on the server.

Revision ID: a1b2c3d4e5f6
Revises: 510632ec96ab
Create Date: 2026-07-27

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '510632ec96ab'
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    # create_all() in app_factory may already have created this table on a
    # fresh boot, so creating it again would abort the upgrade.
    if _has_table("webhook_events"):
        return

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=50), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("contact_id", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_events_created_at", "webhook_events", ["created_at"])


def downgrade():
    if not _has_table("webhook_events"):
        return
    op.drop_index("ix_webhook_events_created_at", table_name="webhook_events")
    op.drop_table("webhook_events")
