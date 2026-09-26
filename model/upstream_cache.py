from flask_sqlalchemy import SQLAlchemy

from . import db

class UpstreamCache(db.Model):
    __tablename__ = 'upstream_cache'

    pattern = db.Column(db.String, primary_key=True)
    upstream_name = db.Column(db.String, primary_key=True)
    resolved_url = db.Column(db.String)
    checked_at = db.Column(db.String)

    def __repr__(self):
        return f"<UpstreamCache(pattern='{self.pattern}', upstream_name='{self.upstream_name}', resolved_url='{self.resolved_url}', checked_at='{self.checked_at}')>"
