from flask_sqlalchemy import SQLAlchemy

# Create SQLAlchemy instance
db = SQLAlchemy()
# Import models to register them with SQLAlchemy
from .upstream_check_log import UpstreamCheckLog
from .redirect import Redirect
from .upstream_cache import UpstreamCache
from .user_param import UserParam


