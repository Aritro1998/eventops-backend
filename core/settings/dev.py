import os

from .base import *


DEBUG = os.getenv("DEBUG", "True").strip().lower() == "true"

# Keep local development permissive by default.
ALLOWED_HOSTS = ["*"]
