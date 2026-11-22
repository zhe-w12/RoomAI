from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT_DIR / value


DATABASE_PATH = _path_from_env("DATABASE_PATH", "backend/data/roomai.sqlite3")
UPLOAD_DIR = _path_from_env("UPLOAD_DIR", "backend/uploads")
MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "https://aiping.cn/api/v1").rstrip("/")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "GLM-5.3-Flash")
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "45"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
