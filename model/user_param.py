# model/user_param.py
from . import db
from datetime import datetime, timezone

class UserParam(db.Model):
    __tablename__ = 'user_params'
    id = db.Column(db.Integer, primary_key=True)
    shortcut_pattern = db.Column(db.String, nullable=False)  # New: associate param with shortcut
    param_name = db.Column(db.String, nullable=False)
    description = db.Column(db.String, nullable=True)
    required = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('shortcut_pattern', 'param_name', name='uq_shortcut_param'),
    )

    def __repr__(self):
        return f'<UserParam {self.shortcut_pattern}:{self.param_name}>'
