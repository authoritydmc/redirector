"""sso users and oidc login states

Revision ID: a91f3c7e2b5d
Revises: e05a511c074b
Create Date: 2026-10-11 17:35:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = 'a91f3c7e2b5d'
down_revision: str | Sequence[str] | None = 'e05a511c074b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sso_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('subject', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
              server_default='viewer'),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('last_login_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'subject', name='uq_sso_user')
    )
    op.create_index(op.f('ix_sso_users_provider'), 'sso_users', ['provider'], unique=False)
    op.create_index(op.f('ix_sso_users_subject'), 'sso_users', ['subject'], unique=False)
    op.create_table('oidc_states',
    sa.Column('state', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('code_verifier', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('nonce', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.Column('expires_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('state')
    )


def downgrade() -> None:
    op.drop_table('oidc_states')
    op.drop_index(op.f('ix_sso_users_subject'), table_name='sso_users')
    op.drop_index(op.f('ix_sso_users_provider'), table_name='sso_users')
    op.drop_table('sso_users')
