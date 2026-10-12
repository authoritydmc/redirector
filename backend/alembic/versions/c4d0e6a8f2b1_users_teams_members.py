"""local users, teams, memberships, shortcut team links

Revision ID: c4d0e6a8f2b1
Revises: a91f3c7e2b5d
Create Date: 2026-10-12 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = 'c4d0e6a8f2b1'
down_revision: str | Sequence[str] | None = 'a91f3c7e2b5d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_by_email', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_teams_name'), 'teams', ['name'], unique=True)
    op.create_table('team_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
              server_default='member'),
    sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'email', name='uq_team_member')
    )
    op.create_index(op.f('ix_team_members_email'), 'team_members', ['email'], unique=False)
    op.create_index(op.f('ix_team_members_team_id'), 'team_members', ['team_id'], unique=False)
    # Batch mode: SQLite cannot ALTER constraints (copy-and-move instead);
    # Postgres renders the same statements as plain ALTERs.
    with op.batch_alter_table('shortcuts') as batch_op:
        batch_op.add_column(sa.Column('team_id', sa.Integer(), nullable=True))
        batch_op.create_index(op.f('ix_shortcuts_team_id'), ['team_id'], unique=False)
        batch_op.create_foreign_key('fk_shortcuts_team_id', 'teams',
                                    ['team_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('shortcuts') as batch_op:
        batch_op.drop_constraint('fk_shortcuts_team_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_shortcuts_team_id'))
        batch_op.drop_column('team_id')
    op.drop_index(op.f('ix_team_members_team_id'), table_name='team_members')
    op.drop_index(op.f('ix_team_members_email'), table_name='team_members')
    op.drop_table('team_members')
    op.drop_index(op.f('ix_teams_name'), table_name='teams')
    op.drop_table('teams')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
