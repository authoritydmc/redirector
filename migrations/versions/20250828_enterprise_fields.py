"""enterprise fields: tags, visibility, expires_at, owner_email

Revision ID: 20250828_enterprise
Revises: 20250621b_user_param_shortcut_pattern
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa

revision = '20250828_enterprise'
down_revision = '20250621b_user_param_shortcut_pattern'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('redirects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('visibility', sa.String(), nullable=False, server_default='public'))
        batch_op.add_column(sa.Column('expires_at', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('owner_email', sa.String(), nullable=True))

def downgrade():
    with op.batch_alter_table('redirects', schema=None) as batch_op:
        batch_op.drop_column('owner_email')
        batch_op.drop_column('expires_at')
        batch_op.drop_column('visibility')
        batch_op.drop_column('tags')
