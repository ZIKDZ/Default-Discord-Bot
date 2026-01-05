import os
from dotenv import load_dotenv

load_dotenv()  # load .env automatically

class Config:
    """Centralized configuration loader."""

    def get(self, key, default=None):
        """Read a value from environment variables."""
        return os.getenv(key.upper(), default)
