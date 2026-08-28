"""
Add shortcut_pattern to user_params for per-shortcut param descriptions (SQLite compatible)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250621b_user_param_shortcut_pattern'
down_revision = '20250621_add_user_param_table'
branch_labels = None
depends_on = None

def upgrade():
    # 1. Create new table with correct schema
    op.create_table(
        'user_params_new',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('shortcut_pattern', sa.String, nullable=False, server_default=''),
        sa.Column('param_name', sa.String, nullable=False),
        sa.Column('description', sa.String, nullable=True),
        sa.Column('required', sa.Boolean, nullable=False, default=True),
        sa.UniqueConstraint('shortcut_pattern', 'param_name', name='uq_shortcut_param')
    )
    # 2. Copy data from old table, setting shortcut_pattern to ''
    op.execute('INSERT INTO user_params_new (id, shortcut_pattern, param_name, description, required) SELECT id, "", param_name, description, required FROM user_params')
    # 3. Drop old table
    op.drop_table('user_params')
    # 4. Rename new table
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
