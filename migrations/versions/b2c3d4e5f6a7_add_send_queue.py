"""add leads.send_queued_at for the paced outreach queue

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    # create_all() in app_factory may already have added this on a fresh boot.
    if _has_column("leads", "send_queued_at"):
        return
    op.add_column("leads", sa.Column("send_queued_at", sa.DateTime(), nullable=True))
    op.create_index("ix_leads_send_queued_at", "leads", ["send_queued_at"])


def downgrade():
    if not _has_column("leads", "send_queued_at"):
        return
    op.drop_index("ix_leads_send_queued_at", table_name="leads")
    op.drop_column("leads", "send_queued_at")
