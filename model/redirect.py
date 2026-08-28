# model.py

from . import db
from datetime import datetime, timezone # Import datetime and timezone for default timestamps

class Redirect(db.Model):
    __tablename__ = 'redirects'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type = db.Column(db.String, nullable=False)
    pattern = db.Column(db.String, nullable=False, unique=True, index=True) # Added unique and index for patterns
    target = db.Column(db.String, nullable=False)
    access_count = db.Column(db.Integer, default=0, nullable=False) # Added default and nullable=False

    # --- Audit Columns ---
    created_at = db.Column(db.String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds'))
    updated_at = db.Column(db.String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds'), onupdate=lambda: datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds'))
    created_ip = db.Column(db.String, nullable=True) # IP address of creator
    updated_ip = db.Column(db.String, nullable=True) # IP address of last updater

    # --- Enterprise: discovery, ACL, lifecycle (issues #71, #74, #75) ---
    tags = db.Column(db.String, nullable=True)  # comma-separated tags, e.g. "eng,onboarding"
    visibility = db.Column(db.String, nullable=False, default='public')  # public|unlisted|private|team
    expires_at = db.Column(db.String, nullable=True)  # ISO8601, null = never expires
    owner_email = db.Column(db.String, nullable=True)  # for team ownership (#70)

    def __repr__(self):
        return (f"<Redirect(id={self.id}, type='{self.type}', pattern='{self.pattern}', "
                f"target='{self.target}', access_count={self.access_count}, "
                f"created_at='{self.created_at}', updated_at='{self.updated_at}')>")