import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("BARBER_SECRET_KEY", "dev-barber-manager-secret")
    DATABASE = str(BASE_DIR / "dados" / "barber_manager.sqlite3")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
