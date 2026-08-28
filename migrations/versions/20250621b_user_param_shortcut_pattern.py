"""
Add shortcut_pattern to user_params for per-shortcut param descriptions (SQLite compatible)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250621b_user_param_shortcut_pattern'
down_revision = 'f200f245867a'
branch_labels = None
depends_on = None

def upgrade():
    # Handle both cases: fresh DB (no user_params) and existing DB with old schema
    # Check if user_params exists
    from alembic import context
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if 'user_params' not in tables:
        # Fresh DB: create with new schema directly
        op.create_table(
            'user_params',
            sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
            sa.Column('shortcut_pattern', sa.String, nullable=False, server_default=''),
            sa.Column('param_name', sa.String, nullable=False),
            sa.Column('description', sa.String, nullable=True),
            sa.Column('required', sa.Boolean, nullable=False, default=True),
            sa.Column('created_at', sa.DateTime, nullable=True),
            sa.Column('updated_at', sa.DateTime, nullable=True),
            sa.UniqueConstraint('shortcut_pattern', 'param_name', name='uq_shortcut_param')
        )
        return
    # Check if old schema (without shortcut_pattern) or new
    cols = [c['name'] for c in inspector.get_columns('user_params')]
    if 'shortcut_pattern' in cols:
        # Already new schema, nothing to do
        return
    # Old schema exists: migrate to new
    op.create_table(
        'user_params_new',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('shortcut_pattern', sa.String, nullable=False, server_default=''),
        sa.Column('param_name', sa.String, nullable=False),
        sa.Column('description', sa.String, nullable=True),
        sa.Column('required', sa.Boolean, nullable=False, default=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('shortcut_pattern', 'param_name', name='uq_shortcut_param')
    )
    op.execute('INSERT INTO user_params_new (id, shortcut_pattern, param_name, description, required) SELECT id, "", param_name, description, required FROM user_params')
    op.drop_table('user_params')
    op.rename_table('user_params_new', 'user_params')

def downgrade():
    # 1. Create old table schema
    op.create_table(
        'user_params_old',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('param_name', sa.String, nullable=False, unique=True),
        sa.Column('description', sa.String, nullable=True),
        sa.Column('required', sa.Boolean, nullable=False, default=True)
    )
    # 2. Copy data back (ignore shortcut_pattern)
    op.execute('INSERT INTO user_params_old (id, param_name, description, required) SELECT id, param_name, description, required FROM user_params')
    # 3. Drop new table
    op.drop_table('user_params')
    # 4. Rename old table
    op.rename_table('user_params_old', 'user_params')
