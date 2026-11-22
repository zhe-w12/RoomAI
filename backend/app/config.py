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
LANGGRAPH_CHECKPOINT_PATH = _path_from_env("LANGGRAPH_CHECKPOINT_PATH", "backend/data/roomai-checkpoints.sqlite3")
UPLOAD_DIR = _path_from_env("UPLOAD_DIR", "backend/uploads")
DOCUMENT_DIR = _path_from_env("DOCUMENT_DIR", "backend/documents")
MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "https://aiping.cn/api/v1").rstrip("/")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "GLM-5.3-Flash")
AVAILABLE_MODELS = [
    item.strip()
    for item in os.getenv("AVAILABLE_MODELS", MODEL_NAME).split(",")
    if item.strip()
]
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "45"))
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_TIMEOUT_SECONDS = float(os.getenv("TAVILY_TIMEOUT_SECONDS", "15"))
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
DEFAULT_EVALUATION_DIMENSIONS = [
    {"key": "feasibility", "label": "技术可行性", "guidance": "现有技术与团队能力能否实现"},
    {"key": "people_cost", "label": "人力成本", "guidance": "需要多少人力与资源投入"},
    {"key": "timeline", "label": "时间周期", "guidance": "多久能交付首个可用版本"},
    {"key": "risk", "label": "风险", "guidance": "关键不确定性与潜在风险"},
    {"key": "innovation", "label": "创新性", "guidance": "相对已有方案的新颖之处"},
    {"key": "team_fit", "label": "团队匹配度", "guidance": "与团队技能和资源的匹配程度"},
]
try:
    _configured_dimensions = json.loads(os.getenv("EVALUATION_DIMENSIONS", "[]"))
except json.JSONDecodeError:
    _configured_dimensions = []
if isinstance(_configured_dimensions, list):
    _dimension_keys: set[str] = set()
    EVALUATION_DIMENSIONS = []
    for _item in _configured_dimensions[:10]:
        if not isinstance(_item, dict):
            continue
        _key = str(_item.get("key", "")).strip()
        _label = str(_item.get("label", "")).strip()
        if not _key or not _key.replace("_", "").isalnum() or not _label or _key in _dimension_keys:
            continue
        _dimension_keys.add(_key)
        EVALUATION_DIMENSIONS.append({
            "key": _key,
            "label": _label[:40],
            "guidance": str(_item.get("guidance", ""))[:180],
        })
else:
    EVALUATION_DIMENSIONS = []
if not EVALUATION_DIMENSIONS:
    EVALUATION_DIMENSIONS = DEFAULT_EVALUATION_DIMENSIONS
IDLE_TRIGGER_SECONDS = int(os.getenv("IDLE_TRIGGER_SECONDS", "60"))
IDLE_TRIGGER_MIN_MESSAGES = int(os.getenv("IDLE_TRIGGER_MIN_MESSAGES", "3"))
FALLBACK_TRIGGER_MESSAGES = int(os.getenv("FALLBACK_TRIGGER_MESSAGES", "20"))
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
ROOM_RETENTION_DAYS = max(1, int(os.getenv("ROOM_RETENTION_DAYS", "30")))
ROOM_CLEANUP_WARNING_DAYS = max(0, min(
    ROOM_RETENTION_DAYS - 1,
    int(os.getenv("ROOM_CLEANUP_WARNING_DAYS", "3")),
))
ROOM_CLEANUP_INTERVAL_SECONDS = max(60, int(os.getenv("ROOM_CLEANUP_INTERVAL_SECONDS", "3600")))
