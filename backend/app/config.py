from __future__ import annotations

import os
import json
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
AVAILABLE_MODELS = [
    item.strip()
    for item in os.getenv("AVAILABLE_MODELS", MODEL_NAME).split(",")
    if item.strip()
]
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "45"))
MODEL_INPUT_PRICE_PER_MILLION = float(os.getenv("MODEL_INPUT_PRICE_PER_MILLION", "0"))
MODEL_OUTPUT_PRICE_PER_MILLION = float(os.getenv("MODEL_OUTPUT_PRICE_PER_MILLION", "0"))
try:
    _raw_model_prices = json.loads(os.getenv("MODEL_PRICES", "{}"))
except json.JSONDecodeError:
    _raw_model_prices = {}
if not isinstance(_raw_model_prices, dict):
    _raw_model_prices = {}
MODEL_PRICES = {
    str(model): {
        "input": max(0.0, float(prices.get("input", 0))),
        "output": max(0.0, float(prices.get("output", 0))),
    }
    for model, prices in _raw_model_prices.items()
    if isinstance(prices, dict)
}
IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "60"))
IDLE_TRIGGER_MIN_MESSAGES = int(os.getenv("IDLE_TRIGGER_MIN_MESSAGES", "3"))
FALLBACK_TRIGGER_MESSAGES = int(os.getenv("FALLBACK_TRIGGER_MESSAGES", "20"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
