from __future__ import annotations

import hashlib
import asyncio
import hmac
import logging
import re
import secrets
import shutil
import uuid
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    AVAILABLE_MODELS,
    DOCUMENT_DIR,
    FALLBACK_TRIGGER_MESSAGES,
    FRONTEND_ORIGIN,
    EVALUATION_DIMENSIONS,
    IDLE_TRIGGER_MIN_MESSAGES,
    IDLE_TRIGGER_SECONDS,
    MODEL_INPUT_PRICE_PER_MILLION,
    MODEL_NAME,
    MODEL_OUTPUT_PRICE_PER_MILLION,
    MODEL_PRICES,
    ROOM_CLEANUP_INTERVAL_SECONDS,
    ROOM_CLEANUP_WARNING_DAYS,
    ROOM_RETENTION_DAYS,
    UPLOAD_DIR,
)
from .database import database, initialize_database
from .realtime import manager, visitor_for_session
from .services.brief import ROOM_TEMPLATES, create_room_brief
from .services.agent import COMMAND_PROMPTS, answer_room_prompt
from .services.analysis import analyze_room
from .services.report import generate_room_report, markdown_to_docx
from .services.voting import propose_vote
from .services.web_search import SearchNotConfigured, SearchUnavailable, search_web
from .services.documents import (
    MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_CHARS,
    SUPPORTED_EXTENSIONS,
    extract_document_text,
    retrieve_document_chunks,
    split_into_chunks,
    token_vector,
)


app = FastAPI(title="RoomAI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

ALLOWED_IMAGES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_room_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def room_password_matches(stored: str, password: str) -> bool:
    if not stored:
        return True
    try:
        algorithm, salt_hex, expected = stored.split("$", 2)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def ensure_room(session_id: str) -> dict:
    with database() as connection:
        row = connection.execute("SELECT * FROM rooms WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="找不到这个房间")
    return dict(row)


def ensure_visitor(session_id: str, visitor_id: str) -> dict:
    visitor = visitor_for_session(session_id, visitor_id)
    if not visitor:
        raise HTTPException(status_code=403, detail="请先加入这个房间")
    return visitor


def ensure_owner(session_id: str, visitor_id: str, owner_token: str) -> dict:
    visitor = ensure_visitor(session_id, visitor_id)
    room = ensure_room(session_id)
    if not visitor["is_owner"] or token_hash(owner_token) != room["owner_token_hash"]:
        raise HTTPException(status_code=403, detail="只有房主可以结束讨论")
    return room


class CreateRoomRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=240)
    nickname: str = Field(min_length=1, max_length=32)
    template_id: str = Field(default="general", max_length=40)
    password: str | None = Field(default=None, min_length=4, max_length=72)


class JoinRoomRequest(BaseModel):
    invite_code: str = Field(min_length=6, max_length=6)
    nickname: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=72)


class JoinByLinkRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=72)


class MessageRequest(BaseModel):
    visitor_id: str
    content: str = Field(default="", max_length=2000)
    reply_to_message_id: int | None = None
    image_url: str | None = None


class PhaseRequest(BaseModel):
    visitor_id: str
    owner_token: str
    phase: str = Field(pattern="^(diverge|converge)$")


class ModelRequest(BaseModel):
    visitor_id: str
    owner_token: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=120)


class RedTeamRequest(BaseModel):
    visitor_id: str
    owner_token: str = Field(min_length=1)
    enabled: bool


class VoteCreateRequest(BaseModel):
    visitor_id: str
    question: str = Field(min_length=4, max_length=300)
    options: list[str] = Field(min_length=2, max_length=4)
    duration_minutes: int = Field(default=5, ge=1, le=60)


class VoteRecordRequest(BaseModel):
    visitor_id: str
    option_id: int


class VoteCloseRequest(BaseModel):
    visitor_id: str
    owner_token: str = Field(min_length=1)


class TodoUpdateRequest(BaseModel):
    visitor_id: str
    title: str = Field(min_length=1, max_length=300)
    assignee: str = Field(default="", max_length=80)
    status: str = Field(default="open", pattern="^(open|done|dropped)$")
    due_at: str | None = Field(default=None, max_length=10)


class FinishReviewRequest(BaseModel):
    visitor_id: str
    owner_token: str = Field(min_length=1)


class FinishRoomRequest(FinishReviewRequest):
    missing_dimensions: list[str] = Field(default_factory=list, max_length=5)


class ReadMentionsRequest(BaseModel):
    visitor_id: str
    message_ids: list[int] | None = None


class DocumentDeleteRequest(BaseModel):
    visitor_id: str
    owner_token: str | None = None


_idle_tasks: dict[str, asyncio.Task] = {}
_analysis_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_message_rate: defaultdict[str, deque[float]] = defaultdict(deque)
_vote_expiry_tasks: dict[int, asyncio.Task] = {}
_vote_creation_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_cleanup_task: asyncio.Task | None = None


def create_message_record(
    session_id: str,
    visitor_id: str | None,
    sender_name: str,
    sender_type: str,
    content: str,
    created_at: str,
    reply_to_message_id: int | None = None,
    image_path: str | None = None,
    *,
    touch_activity: bool = True,
) -> dict:
    with database() as connection:
        cursor = connection.execute(
            """INSERT INTO messages
               (session_id, visitor_id, sender_name, sender_type, content, reply_to_message_id, image_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, visitor_id, sender_name, sender_type, content, reply_to_message_id, image_path, created_at),
        )
        message_id = cursor.lastrowid
        if sender_type == "user" and touch_activity:
            connection.execute(
                "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
                (created_at, session_id),
            )
        if sender_type == "user":
            nicknames = connection.execute(
                "SELECT visitor_id, nickname FROM visitors WHERE session_id = ?", (session_id,)
            ).fetchall()
            for member in nicknames:
                if member["nickname"] != sender_name and re.search(
                    rf"@{re.escape(member['nickname'])}(?![A-Za-z0-9_])", content
                ):
                    connection.execute(
                        "INSERT INTO mentions (message_id, session_id, mentioned_visitor_id, created_at) VALUES (?, ?, ?, ?)",
                        (message_id, session_id, member["visitor_id"], created_at),
                    )
        return get_message(connection, session_id, message_id)


def touch_room_activity(session_id: str, at: str | None = None) -> None:
    with database() as connection:
        connection.execute(
            "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (at or now_iso(), session_id),
        )


def get_message(connection, session_id: str, message_id: int) -> dict:
    row = connection.execute(
        """SELECT m.*, r.sender_name AS reply_sender_name, r.content AS reply_content,
                  r.is_recalled AS reply_is_recalled
           FROM messages m LEFT JOIN messages r ON r.id = m.reply_to_message_id
           WHERE m.session_id = ? AND m.id = ?""",
        (session_id, message_id),
    ).fetchone()
    return message_dict(row)


def message_dict(row) -> dict:
    item = dict(row)
    image_path = item.pop("image_path", None)
    if item["is_recalled"]:
        item["content"] = ""
        item["image_url"] = None
    else:
        item["image_url"] = f"/uploads/{image_path}" if image_path else None
    if item.get("reply_to_message_id"):
        item["reply_to"] = {
            "id": item.pop("reply_to_message_id"),
            "sender_name": item.pop("reply_sender_name"),
            "content": "（该消息已撤回）" if item.pop("reply_is_recalled") else item.pop("reply_content"),
        }
    else:
        item["reply_to"] = None
        item.pop("reply_to_message_id", None)
        item.pop("reply_sender_name", None)
        item.pop("reply_content", None)
        item.pop("reply_is_recalled", None)
    return item


def pending_user_messages(session_id: str) -> int:
    with database() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS count FROM messages
               WHERE session_id = ? AND id > COALESCE((SELECT through_message_id FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1), 0)
                 AND sender_type = 'user' AND is_recalled = 0""",
            (session_id, session_id),
        ).fetchone()
    return int(row["count"])


async def publish_room_analysis(session_id: str, *, auto_vote: bool = True) -> dict | None:
    async with _analysis_locks[session_id]:
        room = ensure_room(session_id)
        if room["closed_at"]:
            return None
        result = await analyze_room(session_id, room["model"])
        if not result:
            message = create_message_record(
                session_id, None, "RoomAI", "agent", "AI 暂时没反应，请稍后再试。讨论消息已经保留。", now_iso()
            )
            await manager.broadcast(session_id, {"type": "message", "message": message})
            return None
        message = create_message_record(session_id, None, "RoomAI", "agent", result["message"], now_iso())
        await manager.broadcast(session_id, {"type": "message", "message": message})
        await manager.broadcast(session_id, {"type": "summary_update", "summary": result["summary"]})
        ideas = get_ideas_for_event(session_id)
        await manager.broadcast(session_id, {"type": "idea_update", "ideas": ideas})
        await manager.broadcast(session_id, {"type": "todo_update", "todos": get_room_todos(session_id)})
        disagreements = result["summary"].get("disagreements", [])
        if auto_vote and disagreements:
            detail = "讨论分析识别到以下分歧，请围绕这些真实观点拟定中立投票：\n" + "\n".join(
                f"- {item}" for item in disagreements[:4]
            )
            asyncio.create_task(create_ai_vote(session_id, detail))
        if room["phase"] == "converge" and room["red_team_enabled"] and result["ideas"]:
            asyncio.create_task(publish_red_team(session_id, automatic=True))
        return result


async def publish_red_team(session_id: str, detail: str = "", *, automatic: bool = False) -> None:
    reserved = False
    generated = False
    if automatic:
        with database() as connection:
            cursor = connection.execute(
                """UPDATE rooms SET red_team_count = red_team_count + 1
                   WHERE session_id = ? AND closed_at IS NULL AND phase = 'converge'
                     AND red_team_enabled = 1 AND red_team_count < 3""",
                (session_id,),
            )
            reserved = cursor.rowcount == 1
        if not reserved:
            return
    try:
        room = ensure_room(session_id)
        if room["closed_at"]:
            return
        if not room["red_team_enabled"]:
            if not automatic:
                message = create_message_record(session_id, None, "RoomAI", "agent", "红队质疑已关闭，房主可以在设置里重新开启。", now_iso())
                await manager.broadcast(session_id, {"type": "message", "message": message})
            return
        if room["phase"] != "converge":
            if not automatic:
                message = create_message_record(session_id, None, "RoomAI", "agent", "红队审查只在收敛阶段进行，请房主切换阶段后再挑战方案。", now_iso())
                await manager.broadcast(session_id, {"type": "message", "message": message})
            return
        with database() as connection:
            history = [dict(row) for row in connection.execute(
                "SELECT sender_name, sender_type, content, image_path, is_recalled FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 30",
                (session_id,),
            ).fetchall()]
            summary_row = connection.execute(
                "SELECT content FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
            ).fetchone()
            idea_rows = [dict(row) for row in connection.execute(
                """SELECT i.id, i.title, i.description, i.proposer_name,
                          (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                          (SELECT COUNT(*) FROM idea_votes iv WHERE iv.idea_id = i.id AND iv.stance = 'support') AS support_count
                   FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC LIMIT 20""",
                (session_id,),
            ).fetchall()]
        history.reverse()
        prompt = detail or (
            "请作为红队审查团队正在收敛的方案。结合上面的真实讨论与候选想法，"
            "只指出最重要的 1 到 2 个尚未验证的风险，用简短、克制的追问表达；"
            "说明相关想法标题，不要编造事实或直接否定方案。"
        )
        summary = json.loads(summary_row["content"]) if summary_row else None
        answer = await answer_room_prompt(
            room["topic"], history, prompt, model=room["model"], session_id=session_id,
            phase=room["phase"], summary=json.dumps(summary, ensure_ascii=False) if summary else None,
            structured_context=json.dumps(idea_rows, ensure_ascii=False) if idea_rows else None,
        )
        if not answer:
            raise RuntimeError("红队审查生成失败")
        message = create_message_record(session_id, None, "RoomAI", "agent", f"**红队质疑**\n\n{answer}", now_iso())
        await manager.broadcast(session_id, {"type": "message", "message": message})
        generated = True
    except Exception:
        return
    finally:
        if reserved and not generated:
            with database() as connection:
                connection.execute("UPDATE rooms SET red_team_count = MAX(0, red_team_count - 1) WHERE session_id = ?", (session_id,))


def get_ideas_for_event(session_id: str) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """SELECT i.*,
                      (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                      (SELECT rationale FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS rationale
               FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC""",
            (session_id,),
        ).fetchall()
    ideas = []
    for row in rows:
        item = dict(row)
        item["dimensions"] = json.loads(item["dimensions"] or "{}")
        scores = list(item["dimensions"].values())
        item["average_score"] = round(sum(scores) / len(scores), 1) if scores else None
        ideas.append(item)
    return ideas


def get_room_todos(session_id: str) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            "SELECT id, session_id, title, assignee, status, due_at, source_message_id, created_at, updated_at FROM todos WHERE session_id = ? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_at DESC",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def vote_state(session_id: str, vote_id: int, visitor_id: str | None = None) -> dict | None:
    with database() as connection:
        vote = connection.execute(
            "SELECT * FROM votes WHERE session_id = ? AND id = ?", (session_id, vote_id)
        ).fetchone()
        if not vote:
            return None
        options = connection.execute(
            """SELECT o.id, o.label, COUNT(r.id) AS votes
               FROM vote_options o LEFT JOIN vote_records r ON r.option_id = o.id
               WHERE o.vote_id = ? GROUP BY o.id ORDER BY o.id""",
            (vote_id,),
        ).fetchall()
        total = sum(int(option["votes"]) for option in options)
        my_vote = connection.execute(
            "SELECT option_id FROM vote_records WHERE vote_id = ? AND visitor_id = ?",
            (vote_id, visitor_id),
        ).fetchone() if visitor_id else None
    return {
        "id": vote["id"],
        "session_id": session_id,
        "message_id": vote["message_id"],
        "question": vote["question"],
        "status": vote["status"],
        "created_at": vote["created_at"],
        "closes_at": vote["closes_at"],
        "closed_at": vote["closed_at"],
        "total_votes": total,
        "my_option_id": int(my_vote["option_id"]) if my_vote else None,
        "options": [
            {
                "id": option["id"],
                "label": option["label"],
                "votes": option["votes"],
                "percentage": round(option["votes"] * 100 / total) if total else 0,
            }
            for option in options
        ],
    }


async def publish_vote(session_id: str, visitor_id: str, question: str, options: list[str], duration_minutes: int = 5) -> dict:
    question = question.strip()
    cleaned_options = list(dict.fromkeys(item.strip()[:180] for item in options if item.strip()))[:4]
    if len(question) < 4 or len(cleaned_options) < 2:
        raise HTTPException(status_code=422, detail="投票问题至少需要两个不同选项")
    created_at = now_iso()
    closes_at = datetime.now(timezone.utc).timestamp() + duration_minutes * 60
    closes_at_iso = datetime.fromtimestamp(closes_at, timezone.utc).isoformat()
    message = create_message_record(session_id, None, "RoomAI", "agent", f"**发起投票**\n\n{question}", created_at)
    with database() as connection:
        cursor = connection.execute(
            "INSERT INTO votes (session_id, message_id, question, closes_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, message["id"], question, closes_at_iso, created_at),
        )
        vote_id = cursor.lastrowid
        connection.executemany(
            "INSERT INTO vote_options (vote_id, label) VALUES (?, ?)",
            [(vote_id, label) for label in cleaned_options],
        )
    await manager.broadcast(session_id, {"type": "message", "message": message})
    state = vote_state(session_id, vote_id)
    await manager.broadcast(session_id, {"type": "vote_created", "vote": state})
    _vote_expiry_tasks[vote_id] = asyncio.create_task(expire_vote_after(session_id, vote_id, duration_minutes * 60))
    return vote_state(session_id, vote_id, visitor_id) or state


async def finalize_vote(session_id: str, vote_id: int) -> dict | None:
    with database() as connection:
        vote = connection.execute(
            "SELECT * FROM votes WHERE id = ? AND session_id = ?", (vote_id, session_id)
        ).fetchone()
        if not vote:
            return None
        if vote["status"] == "open":
            options = connection.execute(
                """SELECT o.id, o.label, COUNT(r.id) AS votes FROM vote_options o
                   LEFT JOIN vote_records r ON r.option_id = o.id WHERE o.vote_id = ? GROUP BY o.id ORDER BY o.id""",
                (vote_id,),
            ).fetchall()
            highest = max((int(option["votes"]) for option in options), default=0)
            winners = [option["label"] for option in options if int(option["votes"]) == highest] if highest else []
            outcome = "平票：" + "、".join(winners) if len(winners) > 1 else (winners[0] if winners else "无人投票")
            closed_at = now_iso()
            connection.execute("UPDATE votes SET status = 'closed', closed_at = ? WHERE id = ?", (closed_at, vote_id))
            connection.execute(
                "INSERT INTO decisions (session_id, title, outcome, source_vote_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, vote["question"], outcome, vote_id, closed_at),
            )
    _vote_expiry_tasks.pop(vote_id, None)
    state = vote_state(session_id, vote_id)
    if state:
        await manager.broadcast(session_id, {"type": "vote_update", "vote": state})
    return state


async def expire_vote_after(session_id: str, vote_id: int, delay_seconds: int) -> None:
    try:
        await asyncio.sleep(max(0, delay_seconds))
        await finalize_vote(session_id, vote_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        return
    finally:
        _vote_expiry_tasks.pop(vote_id, None)


async def create_ai_vote(session_id: str, detail: str = "") -> None:
    try:
        async with _vote_creation_locks[session_id]:
            with database() as connection:
                active_vote = connection.execute(
                    "SELECT 1 FROM votes WHERE session_id = ? AND status = 'open' LIMIT 1", (session_id,)
                ).fetchone()
                if active_vote:
                    return
                rows = connection.execute(
                    "SELECT sender_name, content FROM messages WHERE session_id = ? AND is_recalled = 0 ORDER BY id DESC LIMIT 30",
                    (session_id,),
                ).fetchall()
                summary = connection.execute(
                    "SELECT content FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
                ).fetchone()
            room = ensure_room(session_id)
            if room["closed_at"]:
                return
            history = [dict(row) for row in reversed(rows)]
            if summary:
                history.insert(0, {"sender_name": "RoomAI 总结", "content": summary["content"]})
            result = await propose_vote(room["topic"], history, model=room["model"], session_id=session_id, detail=detail)
            if result:
                await publish_vote(session_id, "", result["question"], result["options"])
            else:
                message = create_message_record(session_id, None, "RoomAI", "agent", "我还没找到足够清晰的投票选项。请先把需要决定的问题和各方方案说具体一些。", now_iso())
                await manager.broadcast(session_id, {"type": "message", "message": message})
    except Exception:
        return


async def _wait_and_analyze(session_id: str) -> None:
    try:
        await asyncio.sleep(IDLE_TRIGGER_SECONDS)
        if pending_user_messages(session_id) >= IDLE_TRIGGER_MIN_MESSAGES:
            await publish_room_analysis(session_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        return
    finally:
        current = asyncio.current_task()
        if _idle_tasks.get(session_id) is current:
            _idle_tasks.pop(session_id, None)


def schedule_room_analysis(session_id: str) -> None:
    existing = _idle_tasks.pop(session_id, None)
    if existing and not existing.done():
        existing.cancel()
    if pending_user_messages(session_id) >= FALLBACK_TRIGGER_MESSAGES:
        asyncio.create_task(publish_room_analysis(session_id))
    else:
        _idle_tasks[session_id] = asyncio.create_task(_wait_and_analyze(session_id))


def _safe_remove_room_uploads(session_id: str) -> None:
    root = UPLOAD_DIR.resolve()
    candidate = UPLOAD_DIR / session_id
    if candidate.is_symlink():
        candidate.unlink(missing_ok=True)
        return
    target = candidate.resolve()
    if target.parent == root and target.is_dir():
        shutil.rmtree(target)


def _safe_remove_room_documents(session_id: str) -> None:
    root = DOCUMENT_DIR.resolve()
    candidate = DOCUMENT_DIR / session_id
    if candidate.is_symlink():
        candidate.unlink(missing_ok=True)
        return
    target = candidate.resolve()
    if target.parent == root and target.is_dir():
        shutil.rmtree(target)


async def cleanup_inactive_rooms() -> None:
    now = datetime.now(timezone.utc)
    warning_cutoff = now - timedelta(
        days=ROOM_RETENTION_DAYS - ROOM_CLEANUP_WARNING_DAYS
    )
    expiration_cutoff = now - timedelta(days=ROOM_RETENTION_DAYS)
    with database() as connection:
        rooms = [dict(row) for row in connection.execute(
            "SELECT session_id, last_activity_at, cleanup_warning_sent_at FROM rooms WHERE last_activity_at IS NOT NULL"
        ).fetchall()]

    expired = []
    warnings = []
    for room in rooms:
        try:
            last_activity = datetime.fromisoformat(room["last_activity_at"])
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            logger.warning("Skipping room with an invalid activity timestamp")
            continue
        if last_activity <= expiration_cutoff:
            expired.append((room["session_id"], room["last_activity_at"]))
        elif ROOM_CLEANUP_WARNING_DAYS and last_activity <= warning_cutoff and not room["cleanup_warning_sent_at"]:
            warnings.append((room["session_id"], room["last_activity_at"]))

    for session_id, last_activity_at in warnings:
        warned_at = now_iso()
        with database() as connection:
            cursor = connection.execute(
                "UPDATE rooms SET cleanup_warning_sent_at = ? WHERE session_id = ? AND last_activity_at = ? AND cleanup_warning_sent_at IS NULL",
                (warned_at, session_id, last_activity_at),
            )
        if cursor.rowcount:
            message = create_message_record(
                session_id, None, "RoomAI", "agent",
                f"**房间清理提醒**\n\n此房间已连续 {ROOM_RETENTION_DAYS - ROOM_CLEANUP_WARNING_DAYS} 天无活动；如无新活动，将在约 {ROOM_CLEANUP_WARNING_DAYS} 天后自动删除。发送新消息或更新房间内容即可保留。",
                warned_at, touch_activity=False,
            )
            await manager.broadcast(session_id, {"type": "message", "message": message})

    for session_id, last_activity_at in expired:
        with database() as connection:
            vote_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM votes WHERE session_id = ?", (session_id,)
            ).fetchall()]
        with database() as connection:
            cursor = connection.execute(
                "DELETE FROM rooms WHERE session_id = ? AND last_activity_at = ?",
                (session_id, last_activity_at),
            )
        if not cursor.rowcount:
            continue
        await manager.broadcast(session_id, {"type": "room_deleted", "reason": "inactive"})
        idle_task = _idle_tasks.pop(session_id, None)
        if idle_task and not idle_task.done():
            idle_task.cancel()
        for vote_id in vote_ids:
            task = _vote_expiry_tasks.pop(vote_id, None)
            if task and not task.done():
                task.cancel()
        try:
            _safe_remove_room_uploads(session_id)
        except OSError:
            logger.exception("Could not remove uploads for a cleaned room")
        try:
            _safe_remove_room_documents(session_id)
        except OSError:
            logger.exception("Could not remove documents for a cleaned room")


async def room_cleanup_loop() -> None:
    while True:
        try:
            await cleanup_inactive_rooms()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Room cleanup cycle failed")
        await asyncio.sleep(ROOM_CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    global _cleanup_task
    initialize_database()
    with database() as connection:
        open_votes = connection.execute(
            "SELECT id, session_id, closes_at FROM votes WHERE status = 'open' AND closes_at IS NOT NULL"
        ).fetchall()
    for vote in open_votes:
        remaining = max(0, int((datetime.fromisoformat(vote["closes_at"]) - datetime.now(timezone.utc)).total_seconds()))
        _vote_expiry_tasks[vote["id"]] = asyncio.create_task(expire_vote_after(vote["session_id"], vote["id"], remaining))
    await cleanup_inactive_rooms()
    _cleanup_task = asyncio.create_task(room_cleanup_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/api/room-templates")
def get_room_templates() -> dict:
    return {"templates": [
        {key: item[key] for key in ("id", "label", "description")}
        for item in ROOM_TEMPLATES.values()
    ]}


@app.post("/api/rooms")
async def create_room(payload: CreateRoomRequest) -> dict:
    topic = payload.topic.strip()
    nickname = payload.nickname.strip()
    if not topic or not nickname:
        raise HTTPException(status_code=422, detail="主题和昵称不能为空")
    if payload.template_id not in ROOM_TEMPLATES:
        raise HTTPException(status_code=422, detail="请选择有效的讨论模板")
    session_id = uuid.uuid4().hex
    owner_token = secrets.token_urlsafe(32)
    visitor_id = uuid.uuid4().hex
    created_at = now_iso()
    with database() as connection:
        invite_code = None
        for _ in range(20):
            candidate = f"{secrets.randbelow(1_000_000):06d}"
            exists = connection.execute("SELECT 1 FROM rooms WHERE invite_code = ?", (candidate,)).fetchone()
            if not exists:
                invite_code = candidate
                break
        if invite_code is None:
            raise HTTPException(status_code=503, detail="暂时无法生成邀请码，请重试")
        try:
            connection.execute(
                """INSERT INTO rooms
                   (session_id, topic, invite_code, owner_token_hash, owner_visitor_id, password_hash, template_id, model, phase, prep_checklist, created_at, last_activity_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'diverge', '[]', ?, ?)""",
                (session_id, topic, invite_code, token_hash(owner_token), visitor_id,
                 hash_room_password(payload.password) if payload.password else "", payload.template_id, MODEL_NAME, created_at, created_at),
            )
            connection.execute(
                "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 1, ?)",
                (visitor_id, session_id, nickname, created_at),
            )
        except Exception:
            raise

    brief = await create_room_brief(topic, session_id=session_id, model=MODEL_NAME, template_id=payload.template_id)
    with database() as connection:
        connection.execute(
            "UPDATE rooms SET prep_checklist = ? WHERE session_id = ?",
            (__import__("json").dumps(brief["prep_checklist"], ensure_ascii=False), session_id),
        )
    opening = create_message_record(
        session_id, None, "RoomAI", "agent", brief["opening"], now_iso()
    )
    return {
        "session_id": session_id,
        "invite_code": invite_code,
        "owner_token": owner_token,
        "visitor_id": visitor_id,
        "nickname": nickname,
        "opening_message": opening,
    }


@app.post("/api/rooms/join")
def join_room(payload: JoinRoomRequest) -> dict:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="昵称不能为空")
    with database() as connection:
        room = connection.execute(
            "SELECT session_id, password_hash FROM rooms WHERE invite_code = ?", (payload.invite_code.strip(),)
        ).fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="没有找到这个邀请码")
        session_id = room["session_id"]
        if not room_password_matches(room["password_hash"], payload.password):
            raise HTTPException(status_code=403, detail="房间密码不正确或未填写")
        duplicate = connection.execute(
            "SELECT 1 FROM visitors WHERE session_id = ? AND nickname = ? COLLATE NOCASE",
            (session_id, nickname),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="这个房间里已经有人使用该昵称")
        visitor_id = uuid.uuid4().hex
        activity_at = now_iso()
        connection.execute(
            "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 0, ?)",
            (visitor_id, session_id, nickname, activity_at),
        )
        connection.execute(
            "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (activity_at, session_id),
        )
    return {"session_id": session_id, "visitor_id": visitor_id, "nickname": nickname}


@app.post("/api/rooms/{session_id}/visitors")
def join_room_by_link(session_id: str, payload: JoinByLinkRequest) -> dict:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="昵称不能为空")
    room = ensure_room(session_id)
    if not room_password_matches(room["password_hash"], payload.password):
        raise HTTPException(status_code=403, detail="房间密码不正确或未填写")
    with database() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM visitors WHERE session_id = ? AND nickname = ? COLLATE NOCASE",
            (session_id, nickname),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="这个房间里已经有人使用该昵称")
        visitor_id = uuid.uuid4().hex
        activity_at = now_iso()
        connection.execute(
            "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 0, ?)",
            (visitor_id, session_id, nickname, activity_at),
        )
        connection.execute(
            "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (activity_at, session_id),
        )
    return {"session_id": session_id, "visitor_id": visitor_id, "nickname": nickname}


@app.get("/api/rooms/{session_id}")
def get_room(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        room = connection.execute("SELECT * FROM rooms WHERE session_id = ?", (session_id,)).fetchone()
        members = connection.execute(
            "SELECT visitor_id, nickname, is_owner FROM visitors WHERE session_id = ? ORDER BY rowid",
            (session_id,),
        ).fetchall()
        unread_mentions = connection.execute(
            "SELECT COUNT(*) AS count FROM mentions WHERE session_id = ? AND mentioned_visitor_id = ? AND is_read = 0",
            (session_id, visitor_id),
        ).fetchone()["count"]
    if not room:
        raise HTTPException(status_code=404, detail="找不到这个房间")
    import json

    return {
        "session_id": room["session_id"],
        "topic": room["topic"],
        "template_id": room["template_id"],
        "invite_code": room["invite_code"],
        "model": room["model"],
        "phase": room["phase"],
        "available_models": list(dict.fromkeys([room["model"], *AVAILABLE_MODELS])),
        "prep_checklist": json.loads(room["prep_checklist"]),
        "members": [dict(member) for member in members],
        "unread_mentions": unread_mentions,
        "created_at": room["created_at"],
        "closed_at": room["closed_at"],
        "red_team_enabled": bool(room["red_team_enabled"]),
        "red_team_count": room["red_team_count"],
    }


@app.get("/api/rooms/{session_id}/messages")
def get_messages(session_id: str, visitor_id: str) -> list[dict]:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute(
            """SELECT m.*, r.sender_name AS reply_sender_name, r.content AS reply_content,
                      r.is_recalled AS reply_is_recalled
               FROM messages m LEFT JOIN messages r ON r.id = m.reply_to_message_id
               WHERE m.session_id = ? ORDER BY m.id ASC""",
            (session_id,),
        ).fetchall()
        return [message_dict(row) for row in rows]


@app.get("/api/rooms/{session_id}/summary")
def get_summaries(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute(
            "SELECT version, content, through_message_id, created_at FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 20",
            (session_id,),
        ).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        item["content"] = json.loads(item["content"])
        history.append(item)
    return {"latest": history[0] if history else None, "history": history}


@app.get("/api/rooms/{session_id}/ideas")
def get_ideas(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute(
            """SELECT i.*,
                      (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                      (SELECT rationale FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS rationale,
                      (SELECT COUNT(*) FROM idea_votes iv WHERE iv.idea_id = i.id AND iv.stance = 'support') AS support_count
               FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC""",
            (session_id,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["dimensions"] = json.loads(item["dimensions"] or "{}")
        scores = list(item["dimensions"].values())
        item["average_score"] = round(sum(scores) / len(scores), 1) if scores else None
        items.append(item)
    return {"ideas": items}


@app.get("/api/rooms/{session_id}/todos")
def get_todos(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    return {"todos": get_room_todos(session_id)}


@app.put("/api/rooms/{session_id}/todos/{todo_id}")
async def update_todo(session_id: str, todo_id: int, payload: TodoUpdateRequest) -> dict:
    ensure_visitor(session_id, payload.visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，待办已锁定")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="待办内容不能为空")
    if payload.due_at:
        try:
            datetime.strptime(payload.due_at, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="截止日期格式应为 YYYY-MM-DD") from exc
    with database() as connection:
        existing = connection.execute(
            "SELECT id FROM todos WHERE id = ? AND session_id = ?", (todo_id, session_id)
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="找不到这条待办")
        activity_at = now_iso()
        connection.execute(
            "UPDATE todos SET title = ?, assignee = ?, status = ?, due_at = ?, updated_at = ? WHERE id = ?",
            (title, payload.assignee.strip(), payload.status, payload.due_at or None, activity_at, todo_id),
        )
        connection.execute(
            "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (activity_at, session_id),
        )
    todo = next(item for item in get_room_todos(session_id) if item["id"] == todo_id)
    todos = get_room_todos(session_id)
    await manager.broadcast(session_id, {"type": "todo_update", "todos": todos})
    return {"todo": todo}


@app.get("/api/rooms/{session_id}/usage")
def get_usage(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute(
            """SELECT model, SUM(prompt_tokens) AS prompt_tokens,
                      SUM(completion_tokens) AS completion_tokens, SUM(cost_estimate) AS cost_estimate
               FROM usage WHERE session_id = ? GROUP BY model ORDER BY model""",
            (session_id,),
        ).fetchall()
    models = [dict(row) for row in rows]
    return {
        "models": models,
        "prompt_tokens": sum(row["prompt_tokens"] for row in models),
        "completion_tokens": sum(row["completion_tokens"] for row in models),
        "total_tokens": sum(row["prompt_tokens"] + row["completion_tokens"] for row in models),
        "cost_estimate": round(sum(row["cost_estimate"] for row in models), 6),
        "cost_configured": (
            MODEL_INPUT_PRICE_PER_MILLION > 0
            or MODEL_OUTPUT_PRICE_PER_MILLION > 0
            or any(row["model"] in MODEL_PRICES and any(MODEL_PRICES[row["model"]].values()) for row in models)
        ),
    }


@app.get("/api/rooms/{session_id}/votes")
def get_votes(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute("SELECT id FROM votes WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
    return {"votes": [vote_state(session_id, row["id"], visitor_id) for row in rows]}


@app.post("/api/rooms/{session_id}/votes")
async def create_vote(session_id: str, payload: VoteCreateRequest) -> dict:
    ensure_visitor(session_id, payload.visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，无法发起投票")
    touch_room_activity(session_id)
    vote = await publish_vote(session_id, payload.visitor_id, payload.question, payload.options, payload.duration_minutes)
    return {"vote": vote}


@app.post("/api/rooms/{session_id}/votes/{vote_id}/records")
async def cast_vote(session_id: str, vote_id: int, payload: VoteRecordRequest) -> dict:
    ensure_visitor(session_id, payload.visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，投票已锁定")
    with database() as connection:
        vote = connection.execute("SELECT * FROM votes WHERE id = ? AND session_id = ?", (vote_id, session_id)).fetchone()
        option = connection.execute("SELECT id FROM vote_options WHERE id = ? AND vote_id = ?", (payload.option_id, vote_id)).fetchone()
        if not vote or not option:
            raise HTTPException(status_code=404, detail="找不到这张投票卡或选项")
        if vote["status"] != "open":
            raise HTTPException(status_code=409, detail="投票已经结束")
        if vote["closes_at"] and datetime.fromisoformat(vote["closes_at"]) <= datetime.now(timezone.utc):
            expired = True
        else:
            expired = False
        if not expired:
            activity_at = now_iso()
            connection.execute(
                """INSERT INTO vote_records (vote_id, visitor_id, option_id, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(vote_id, visitor_id) DO UPDATE SET option_id = excluded.option_id, updated_at = excluded.updated_at""",
                (vote_id, payload.visitor_id, payload.option_id, activity_at),
            )
            connection.execute(
                "UPDATE rooms SET last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
                (activity_at, session_id),
            )
    if expired:
        await finalize_vote(session_id, vote_id)
        raise HTTPException(status_code=409, detail="投票时间已结束")
    shared_state = vote_state(session_id, vote_id)
    await manager.broadcast(session_id, {"type": "vote_update", "vote": shared_state})
    return {"vote": vote_state(session_id, vote_id, payload.visitor_id)}


@app.post("/api/rooms/{session_id}/votes/{vote_id}/close")
async def close_vote(session_id: str, vote_id: int, payload: VoteCloseRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    room = ensure_room(session_id)
    if not visitor["is_owner"] or token_hash(payload.owner_token) != room["owner_token_hash"]:
        raise HTTPException(status_code=403, detail="只有房主可以结束投票")
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    task = _vote_expiry_tasks.pop(vote_id, None)
    if task and not task.done():
        task.cancel()
    state = await finalize_vote(session_id, vote_id)
    if not state:
        raise HTTPException(status_code=404, detail="找不到这张投票卡")
    touch_room_activity(session_id)
    return {"vote": state}


@app.get("/api/rooms/{session_id}/search")
def search_room(session_id: str, visitor_id: str, q: str = Query(min_length=1, max_length=100)) -> dict:
    ensure_visitor(session_id, visitor_id)
    query = f"%{q.strip()}%"
    if not q.strip():
        return {"results": []}
    with database() as connection:
        messages = connection.execute(
            """SELECT id, sender_name AS title, content AS excerpt, created_at, id AS source_message_id
               FROM messages WHERE session_id = ? AND is_recalled = 0 AND (content LIKE ? OR sender_name LIKE ?)
               ORDER BY id DESC LIMIT 30""",
            (session_id, query, query),
        ).fetchall()
        ideas = connection.execute(
            """SELECT id, title, description AS excerpt, updated_at AS created_at, source_message_id
               FROM ideas WHERE session_id = ? AND (title LIKE ? OR description LIKE ?) ORDER BY updated_at DESC LIMIT 20""",
            (session_id, query, query),
        ).fetchall()
        summaries = connection.execute(
            "SELECT version AS id, content, created_at FROM summaries WHERE session_id = ? AND content LIKE ? ORDER BY version DESC LIMIT 20",
            (session_id, query),
        ).fetchall()
    results = [{"type": "message", **dict(row)} for row in messages]
    results.extend({"type": "idea", **dict(row)} for row in ideas)
    for row in summaries:
        data = json.loads(row["content"])
        results.append({"type": "summary", "id": row["id"], "title": f"讨论总结 v{row['id']}", "excerpt": "；".join(data.get("consensus", [])), "created_at": row["created_at"], "source_message_id": None})
    results.sort(key=lambda item: item["created_at"], reverse=True)
    return {"results": results[:50]}


@app.get("/api/rooms/{session_id}/replay")
def get_replay(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    room = ensure_room(session_id)
    events = []
    with database() as connection:
        messages = connection.execute(
            "SELECT id, sender_name, sender_type, content, is_recalled, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        summaries = connection.execute(
            "SELECT version, content, through_message_id, created_at FROM summaries WHERE session_id = ? ORDER BY version",
            (session_id,),
        ).fetchall()
        ideas = connection.execute(
            "SELECT id, title, description, source_message_id, created_at FROM ideas WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        evaluations = connection.execute(
            """SELECT e.id, e.dimensions, e.rationale, e.created_at, i.title, i.source_message_id
               FROM evaluations e JOIN ideas i ON i.id = e.idea_id WHERE i.session_id = ? ORDER BY e.id""",
            (session_id,),
        ).fetchall()
        votes = connection.execute(
            """SELECT v.id, v.question, v.status, v.message_id, v.created_at, v.closed_at,
                      d.outcome FROM votes v LEFT JOIN decisions d ON d.source_vote_id = v.id
               WHERE v.session_id = ? ORDER BY v.id""",
            (session_id,),
        ).fetchall()
        todos = connection.execute(
            "SELECT id, title, assignee, status, source_message_id, created_at FROM todos WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        decisions = connection.execute(
            "SELECT id, title, outcome, created_at FROM decisions WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    for item in messages:
        detail = "这条消息已撤回" if item["is_recalled"] else (item["content"] or "图片附件")
        events.append({"id": f"message-{item['id']}", "type": "message", "title": f"{item['sender_name']} 发言", "detail": detail[:500], "created_at": item["created_at"], "message_id": item["id"]})
    for item in summaries:
        content = json.loads(item["content"])
        detail = "；".join(content.get("consensus", [])) or "总结已更新"
        events.append({"id": f"summary-{item['version']}", "type": "summary", "title": f"滚动总结 v{item['version']}", "detail": detail[:500], "created_at": item["created_at"], "message_id": item["through_message_id"] or None})
    for item in ideas:
        events.append({"id": f"idea-{item['id']}", "type": "idea", "title": f"新增想法：{item['title']}", "detail": item["description"], "created_at": item["created_at"], "message_id": item["source_message_id"]})
    for item in evaluations:
        dimensions = json.loads(item["dimensions"] or "{}")
        score = round(sum(dimensions.values()) / len(dimensions), 1) if dimensions else None
        detail = f"六维平均分 {score}/5。{item['rationale']}" if score is not None else item["rationale"]
        events.append({"id": f"evaluation-{item['id']}", "type": "evaluation", "title": f"评估想法：{item['title']}", "detail": detail, "created_at": item["created_at"], "message_id": item["source_message_id"]})
    for item in votes:
        events.append({"id": f"vote-{item['id']}", "type": "vote", "title": f"投票：{item['question']}", "detail": item["outcome"] or ("投票已结束" if item["status"] == "closed" else "投票进行中"), "created_at": item["created_at"], "message_id": item["message_id"]})
    for item in todos:
        events.append({"id": f"todo-{item['id']}", "type": "todo", "title": f"待办：{item['title']}", "detail": f"负责人：{item['assignee']}" if item["assignee"] else item["status"], "created_at": item["created_at"], "message_id": item["source_message_id"]})
    for item in decisions:
        events.append({"id": f"decision-{item['id']}", "type": "decision", "title": f"决策：{item['title']}", "detail": item["outcome"], "created_at": item["created_at"], "message_id": None})
    events.sort(key=lambda event: event["created_at"])
    return {"topic": room["topic"], "events": events}


@app.get("/api/rooms/{session_id}/mentions")
def get_mentions(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        rows = connection.execute(
            "SELECT message_id FROM mentions WHERE session_id = ? AND mentioned_visitor_id = ? AND is_read = 0 ORDER BY message_id",
            (session_id, visitor_id),
        ).fetchall()
    return {"message_ids": [row["message_id"] for row in rows]}


@app.post("/api/rooms/{session_id}/mentions/read")
def mark_mentions_read(session_id: str, payload: ReadMentionsRequest) -> dict:
    ensure_visitor(session_id, payload.visitor_id)
    with database() as connection:
        if payload.message_ids:
            placeholders = ",".join("?" for _ in payload.message_ids)
            connection.execute(
                f"UPDATE mentions SET is_read = 1 WHERE session_id = ? AND mentioned_visitor_id = ? AND message_id IN ({placeholders})",
                [session_id, payload.visitor_id, *payload.message_ids],
            )
        else:
            connection.execute(
                "UPDATE mentions SET is_read = 1 WHERE session_id = ? AND mentioned_visitor_id = ?",
                (session_id, payload.visitor_id),
            )
    return {"ok": True}


@app.post("/api/rooms/{session_id}/messages")
async def post_message(session_id: str, payload: MessageRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，无法继续发送消息")
    content = payload.content.strip()
    image_path = None
    if payload.image_url:
        prefix = f"/uploads/{session_id}/"
        if not payload.image_url.startswith(prefix):
            raise HTTPException(status_code=422, detail="图片附件无效")
        image_path = payload.image_url.removeprefix("/uploads/")
    if not content and not image_path:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    if payload.reply_to_message_id is not None:
        with database() as connection:
            replied = connection.execute(
                "SELECT 1 FROM messages WHERE session_id = ? AND id = ?",
                (session_id, payload.reply_to_message_id),
            ).fetchone()
        if not replied:
            raise HTTPException(status_code=404, detail="引用的消息不存在")
    rate = _message_rate[visitor["visitor_id"]]
    now_tick = time.monotonic()
    while rate and now_tick - rate[0] >= 10:
        rate.popleft()
    if len(rate) >= 5:
        raise HTTPException(status_code=429, detail="10 秒内最多发送 5 条消息，请稍后再试")
    rate.append(now_tick)
    message = create_message_record(
        session_id,
        payload.visitor_id,
        visitor["nickname"],
        "user",
        content,
        now_iso(),
        payload.reply_to_message_id,
        image_path,
    )
    await manager.broadcast(session_id, {"type": "message", "message": message})
    schedule_room_analysis(session_id)
    stop_red_team = re.search(r"(?:别|不要|停止|关闭).{0,6}(?:挑战|红队|质疑)", content, re.IGNORECASE)
    manual_red_team = content.startswith("/挑战") or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE)
        and re.search(r"挑战|红队|质疑", content)
    )
    if stop_red_team:
        with database() as connection:
            connection.execute("UPDATE rooms SET red_team_enabled = 0 WHERE session_id = ?", (session_id,))
        await manager.broadcast(session_id, {"type": "red_team_setting", "enabled": False})
        notice = create_message_record(session_id, None, "RoomAI", "agent", "红队自动质疑已暂停。房主可以在房间设置里重新开启。", now_iso())
        await manager.broadcast(session_id, {"type": "message", "message": notice})
    elif manual_red_team:
        detail = content[len("/挑战"):].strip() if content.startswith("/挑战") else content
        asyncio.create_task(publish_red_team(session_id, detail))
        return message
    prompt = ai_prompt(content)
    is_vote_request = content.startswith("/投票") or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE) and "投票" in content
    )
    is_summary_request = content.startswith(("/总结", "/评估")) or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE)
        and re.search(r"总结|梳理共识|评估", content)
    )
    is_research_request = content.startswith("/联网评估") or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE)
        and re.search(r"联网评估|联网检索", content)
    )
    if is_vote_request:
        detail = content[len("/投票"):].strip() if content.startswith("/投票") else content
        asyncio.create_task(create_ai_vote(session_id, detail))
    elif is_research_request:
        detail = content.removeprefix("/联网评估").strip()
        detail = re.sub(r"@(AI|RoomAI)\b", "", detail, flags=re.IGNORECASE)
        detail = re.sub(r"^联网(?:评估|检索)", "", detail).strip(" ：:，,。")
        asyncio.create_task(generate_agent_reply(
            session_id,
            prompt or COMMAND_PROMPTS["/联网评估"],
            research_query=detail,
        ))
    elif is_summary_request:
        asyncio.create_task(publish_room_analysis(session_id, auto_vote=False))
    elif content.startswith("/对比"):
        asyncio.create_task(publish_idea_comparison(session_id))
    elif prompt:
        asyncio.create_task(generate_agent_reply(session_id, prompt))
    return message


def ai_prompt(content: str) -> str | None:
    stripped = content.strip()
    if stripped.startswith("/帮助"):
        return "__HELP__"
    command = next((item for item in COMMAND_PROMPTS if stripped.startswith(item)), None)
    if command:
        detail = stripped[len(command):].strip()
        return f"{COMMAND_PROMPTS[command]}\n成员补充要求：{detail}" if detail else COMMAND_PROMPTS[command]
    if re.search(r"@(AI|RoomAI)\b", stripped, re.IGNORECASE):
        request = re.sub(r"@(AI|RoomAI)\b", "", stripped, flags=re.IGNORECASE).strip()
        if re.match(r"^联网(?:评估|检索)", request):
            detail = re.sub(r"^联网(?:评估|检索)", "", request).strip(" ：:，,。")
            return f"{COMMAND_PROMPTS['/联网评估']}\n需要核对：{detail}" if detail else COMMAND_PROMPTS["/联网评估"]
        return request or "请根据目前的讨论，整理共识与下一步。"
    return None


def _comparison_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


async def publish_idea_comparison(session_id: str) -> None:
    await manager.broadcast(
        session_id,
        {"type": "typing", "visitor_id": "roomai", "nickname": "RoomAI", "active": True},
    )
    try:
        room = ensure_room(session_id)
        if room["closed_at"]:
            return
        if room["phase"] != "converge":
            answer = "方案对比表适合在收敛阶段使用。请先由房主切换到收敛阶段，再输入 `/对比`。"
        else:
            with database() as connection:
                ideas = [dict(row) for row in connection.execute(
                    """SELECT i.*,
                              (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                              (SELECT rationale FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS rationale,
                              (SELECT COUNT(*) FROM idea_votes v WHERE v.idea_id = i.id AND v.stance = 'support') AS support_count,
                              (SELECT COUNT(*) FROM idea_votes v WHERE v.idea_id = i.id AND v.stance = 'oppose') AS oppose_count
                       FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC LIMIT 8""",
                    (session_id,),
                ).fetchall()]
            if len(ideas) < 2:
                answer = "想法池里还没有足够的候选方案。先记录至少两个方向，再输入 `/对比`。"
            else:
                dimensions = [item for item in EVALUATION_DIMENSIONS]
                headers = ["方案", "说明", *[item["label"] for item in dimensions], "优势", "代价 / 风险", "评估依据", "支持 / 反对"]
                lines = ["### 候选方案对比", "", "| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
                for idea in ideas:
                    scores = json.loads(idea["dimensions"] or "{}")
                    cells = [
                        idea["title"], idea["description"],
                        *[f"{scores[key]}/5" if isinstance(scores.get(key), (int, float)) else "未评估" for key in (item["key"] for item in dimensions)],
                        idea["strengths"] or "未记录",
                        idea["tradeoffs"] or "未记录",
                        idea["rationale"] or "未记录",
                        f"{idea['support_count']} / {idea['oppose_count']}",
                    ]
                    lines.append("| " + " | ".join(_comparison_cell(value) for value in cells) + " |")
                lines.extend(["", "评分与优势/代价来自房间已记录的评估；空缺项表示讨论中尚无明确记录。表格用于辅助讨论，不代表最终决定。"])
                answer = "\n".join(lines)
        message = create_message_record(session_id, None, "RoomAI", "agent", answer, now_iso())
        await manager.broadcast(session_id, {"type": "message", "message": message})
    except Exception:
        return
    finally:
        await manager.broadcast(
            session_id,
            {"type": "typing", "visitor_id": "roomai", "nickname": "RoomAI", "active": False},
        )


async def generate_agent_reply(
    session_id: str,
    prompt: str,
    *,
    research_query: str | None = None,
) -> None:
    await manager.broadcast(
        session_id,
        {"type": "typing", "visitor_id": "roomai", "nickname": "RoomAI", "active": True},
    )
    try:
        room = ensure_room(session_id)
        if room["closed_at"]:
            return
        with database() as connection:
            history = [dict(row) for row in connection.execute(
                """SELECT m.sender_name, m.sender_type, m.content, m.image_path, m.is_recalled,
                          r.sender_name AS reply_sender_name, r.content AS reply_content
                   FROM messages m LEFT JOIN messages r ON r.id = m.reply_to_message_id
                   WHERE m.session_id = ? ORDER BY m.id DESC LIMIT 30""",
                (session_id,),
            ).fetchall()]
            summary_row = connection.execute(
                "SELECT content FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
            ).fetchone()
            idea_rows = connection.execute(
                "SELECT title, description, proposer_name FROM ideas WHERE session_id = ? ORDER BY updated_at DESC LIMIT 20", (session_id,)
            ).fetchall()
            member_names = [row["nickname"] for row in connection.execute(
                "SELECT nickname FROM visitors WHERE session_id = ? ORDER BY rowid", (session_id,)
            ).fetchall()]
        history.reverse()
        summary_context = json.loads(summary_row["content"]) if summary_row else None
        structured_context = json.dumps([dict(row) for row in idea_rows], ensure_ascii=False) if idea_rows else None
        answer = None
        if prompt == "__HELP__":
            answer = "可以试试这些快捷指令：/总结 梳理共识、分歧和待办；/评估 评估核心想法；/联网评估 结合外部资料核对方向；/投票整理待决定的问题；/对比并排比较候选方案；/画图生成方案流程；/挑战检查风险；/待办提取下一步。也可以直接 @RoomAI 提问；房间资料会按问题自动检索。"
        else:
            references = retrieve_document_chunks(
                session_id,
                " ".join([
                    room["topic"],
                    research_query or " ".join(
                        item["content"] for item in history
                        if item.get("sender_type") == "user" and item.get("content")
                    ),
                    prompt,
                ])[-1800:],
            )
            search_notice = ""
            if research_query is not None:
                query = " ".join(research_query.split())[:500] or room["topic"]
                try:
                    web_sources = await search_web(f"{room['topic']} {query}")
                except SearchNotConfigured:
                    web_sources = []
                    search_notice = "服务器尚未配置联网检索；以下回答只依据讨论内容和房间资料。"
                except SearchUnavailable:
                    web_sources = []
                    search_notice = "联网检索暂时不可用；以下回答只依据讨论内容和房间资料。"
                references.extend(web_sources)
                if not web_sources and not references:
                    answer = search_notice or "没有检索到可用来源。可以换更具体的关键词后重试，或使用 `/评估` 按讨论内容评估。"
                elif not web_sources:
                    prompt += f"\n{search_notice or '没有检索到外部网页来源，请仅使用下方房间资料。'}"
            if answer is None:
                answer = await answer_room_prompt(
                    room["topic"], history, prompt, model=room["model"], session_id=session_id,
                    phase=room["phase"],
                    summary=json.dumps(summary_context, ensure_ascii=False) if summary_context else None,
                    structured_context=structured_context,
                    member_names=member_names,
                    external_sources=references,
                )
                if answer and references:
                    source_lines = []
                    for index, source in enumerate(references, 1):
                        if source.get("kind") == "document":
                            source_lines.append(f"- [来源 {index}] 房间资料《{source['title']}》，第 {source['chunk_index']} 段")
                        else:
                            source_lines.append(
                                f"- [来源 {index}] [{source['title']}]({source['url']})"
                                + (f"（{source['published_date']}）" if source.get("published_date") else "")
                            )
                    answer += "\n\n**参考来源**\n" + "\n".join(
                        source_lines
                    )
            if answer:
                suggestions = re.findall(r"\[\[suggest_mention:([^\]]+)\]\]", answer)
                selected = next((name.strip() for name in reversed(suggestions) if name.strip() in member_names), None)
                answer = re.sub(r"\n?\[\[suggest_mention:[^\]]+\]\]\s*", "", answer).strip()
                if selected:
                    answer += f"\n\n[[suggest_mention:{selected}]]"
        if not answer:
            answer = "我暂时没能连上模型，请稍后重试。你也可以先继续讨论，消息会保留在房间里。"
        message = create_message_record(session_id, None, "RoomAI", "agent", answer, now_iso())
        await manager.broadcast(session_id, {"type": "message", "message": message})
    except Exception:
        return
    finally:
        await manager.broadcast(
            session_id,
            {"type": "typing", "visitor_id": "roomai", "nickname": "RoomAI", "active": False},
        )


def _room_document_list(session_id: str) -> list[dict]:
    with database() as connection:
        rows = connection.execute(
            """SELECT d.id, d.filename, d.media_type, d.size, d.chunk_count, d.created_at,
                      v.nickname AS uploaded_by
               FROM room_documents d JOIN visitors v ON v.visitor_id = d.visitor_id
               WHERE d.session_id = ? ORDER BY d.created_at DESC, d.id DESC""",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/rooms/{session_id}/documents")
def get_room_documents(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    return {"documents": _room_document_list(session_id)}


@app.post("/api/rooms/{session_id}/documents")
async def upload_room_document(
    session_id: str,
    visitor_id: str = Form(...),
    document: UploadFile = File(...),
) -> dict:
    visitor = ensure_visitor(session_id, visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已结束，无法添加资料")
    filename = (document.filename or "").replace("\\", "/").split("/")[-1].strip()[:180]
    extension = Path(filename).suffix.lower()
    if not filename or extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="支持 PDF、Word、PowerPoint、TXT 和 Markdown 文件")
    data = await document.read(MAX_DOCUMENT_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="文件内容为空")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="单个资料文件不能超过 10MB")
    try:
        extracted = await asyncio.to_thread(extract_document_text, filename, data)
    except Exception as error:
        logger.info("Could not extract room document (%s)", extension)
        raise HTTPException(status_code=422, detail="无法读取这个文件，请确认文件未损坏") from error
    if not extracted:
        raise HTTPException(status_code=422, detail="文件中没有可检索的文字；扫描版 PDF 暂不支持 OCR")
    if len(extracted) > MAX_DOCUMENT_CHARS:
        raise HTTPException(status_code=413, detail="提取文本超过 30 万字，请拆分文件后再添加")
    chunks = split_into_chunks(extracted)
    with database() as connection:
        document_count = connection.execute(
            "SELECT COUNT(*) AS count FROM room_documents WHERE session_id = ?", (session_id,)
        ).fetchone()["count"]
    if document_count >= 20:
        raise HTTPException(status_code=409, detail="每个房间最多保存 20 份讨论资料")

    stored_name = f"{uuid.uuid4().hex}{extension}"
    room_folder = DOCUMENT_DIR / session_id
    target = (room_folder / stored_name).resolve()
    if not target.is_relative_to(DOCUMENT_DIR.resolve() / session_id):
        raise HTTPException(status_code=422, detail="文件路径无效")
    room_folder.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, data)
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }[extension]
    created_at = now_iso()
    try:
        with database() as connection:
            cursor = connection.execute(
                """INSERT INTO room_documents
                   (session_id, visitor_id, filename, storage_path, media_type, size, chunk_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, visitor["visitor_id"], filename, f"{session_id}/{stored_name}", media_type, len(data), len(chunks), created_at),
            )
            document_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO document_chunks (document_id, chunk_index, content, vector_json) VALUES (?, ?, ?, ?)",
                [(document_id, index, chunk, token_vector(chunk)) for index, chunk in enumerate(chunks)],
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    touch_room_activity(session_id)
    documents = _room_document_list(session_id)
    await manager.broadcast(session_id, {"type": "documents_update", "documents": documents})
    created = next(item for item in documents if item["id"] == document_id)
    return {"document": created}


@app.delete("/api/rooms/{session_id}/documents/{document_id}")
async def delete_room_document(session_id: str, document_id: int, payload: DocumentDeleteRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    ensure_room(session_id)
    with database() as connection:
        row = connection.execute(
            "SELECT id, visitor_id, storage_path FROM room_documents WHERE session_id = ? AND id = ?",
            (session_id, document_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="找不到这份讨论资料")
    if row["visitor_id"] != payload.visitor_id:
        if not payload.owner_token:
            raise HTTPException(status_code=403, detail="只有上传者或房主可以删除这份资料")
        ensure_owner(session_id, payload.visitor_id, payload.owner_token)
    stored_path = (DOCUMENT_DIR / row["storage_path"]).resolve()
    if not stored_path.is_relative_to(DOCUMENT_DIR.resolve() / session_id):
        raise HTTPException(status_code=422, detail="资料路径无效")
    with database() as connection:
        connection.execute(
            "DELETE FROM room_documents WHERE session_id = ? AND id = ?", (session_id, document_id)
        )
    if stored_path.is_file() and not stored_path.is_symlink():
        stored_path.unlink(missing_ok=True)
    touch_room_activity(session_id)
    documents = _room_document_list(session_id)
    await manager.broadcast(session_id, {"type": "documents_update", "documents": documents})
    return {"ok": True}


@app.post("/api/rooms/{session_id}/upload")
async def upload_image(
    session_id: str,
    visitor_id: str = Form(...),
    image: UploadFile = File(...),
) -> dict:
    ensure_visitor(session_id, visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，无法上传图片")
    extension = ALLOWED_IMAGES.get(image.content_type or "")
    if not extension:
        raise HTTPException(status_code=415, detail="仅支持 JPG、PNG、WebP 或 GIF 图片")
    content = await image.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片大小不能超过 5MB")
    room_dir = UPLOAD_DIR / session_id
    room_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    (room_dir / filename).write_bytes(content)
    touch_room_activity(session_id)
    return {"image_url": f"/uploads/{session_id}/{filename}"}


@app.post("/api/rooms/{session_id}/messages/{message_id}/recall")
async def recall_message(session_id: str, message_id: int, visitor_id: str) -> dict:
    visitor = ensure_visitor(session_id, visitor_id)
    if ensure_room(session_id)["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束，消息已锁定")
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM messages WHERE session_id = ? AND id = ?", (session_id, message_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="找不到这条消息")
        created_at = datetime.fromisoformat(row["created_at"])
        age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        if row["visitor_id"] != visitor["visitor_id"] or row["sender_type"] != "user":
            raise HTTPException(status_code=403, detail="只能撤回自己发送的消息")
        if age_seconds > 120:
            raise HTTPException(status_code=409, detail="消息发送超过两分钟，无法撤回")
        image_path = row["image_path"]
        connection.execute(
            "UPDATE messages SET content = '', image_path = NULL, is_recalled = 1 WHERE id = ?",
            (message_id,),
        )
        message = get_message(connection, session_id, message_id)
    if image_path:
        image_file = UPLOAD_DIR / image_path
        if image_file.is_file() and image_file.resolve().is_relative_to(UPLOAD_DIR.resolve()):
            image_file.unlink(missing_ok=True)
    await manager.broadcast(session_id, {"type": "message_recalled", "message": message})
    return message


@app.put("/api/rooms/{session_id}/phase")
async def update_phase(session_id: str, payload: PhaseRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    room = ensure_room(session_id)
    if not visitor["is_owner"] or token_hash(payload.owner_token) != room["owner_token_hash"]:
        raise HTTPException(status_code=403, detail="只有房主可以切换阶段")
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    with database() as connection:
        connection.execute(
            "UPDATE rooms SET phase = ?, last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (payload.phase, now_iso(), session_id),
        )
    await manager.broadcast(session_id, {"type": "phase_changed", "phase": payload.phase})
    return {"phase": payload.phase}


@app.put("/api/rooms/{session_id}/red-team")
async def update_red_team(session_id: str, payload: RedTeamRequest) -> dict:
    room = ensure_owner(session_id, payload.visitor_id, payload.owner_token)
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    with database() as connection:
        connection.execute(
            "UPDATE rooms SET red_team_enabled = ?, last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (int(payload.enabled), now_iso(), session_id),
        )
    await manager.broadcast(session_id, {"type": "red_team_setting", "enabled": payload.enabled})
    return {"enabled": payload.enabled}


@app.post("/api/rooms/{session_id}/finish/review")
async def review_finish(session_id: str, payload: FinishReviewRequest) -> dict:
    room = ensure_owner(session_id, payload.visitor_id, payload.owner_token)
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    async with _analysis_locks[session_id]:
        result = await analyze_room(session_id, room["model"], force=True)
    if result:
        await manager.broadcast(session_id, {"type": "summary_update", "summary": result["summary"]})
        await manager.broadcast(session_id, {"type": "idea_update", "ideas": get_ideas_for_event(session_id)})
        await manager.broadcast(session_id, {"type": "todo_update", "todos": get_room_todos(session_id)})
        summary = result["summary"]
    else:
        with database() as connection:
            latest = connection.execute(
                "SELECT content FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
            ).fetchone()
        summary = json.loads(latest["content"]) if latest else {}
    labels = {
        "user": ("用户", "目标用户是谁？有哪些需求证据？"),
        "technology": ("技术", "核心技术路线是什么？团队是否具备实现能力？"),
        "time": ("时间", "首个可用版本需要多久？关键里程碑是什么？"),
        "cost": ("成本", "需要投入哪些人力、资金或资源？"),
        "risk": ("风险", "最大的未验证假设和风险是什么？如何降低？"),
    }
    coverage = summary.get("dimension_coverage", {}) if isinstance(summary, dict) else {}
    missing = [
        {"key": key, "label": label, "question": question}
        for key, (label, question) in labels.items()
        if not coverage.get(key, False)
    ]
    return {"coverage": coverage, "missing_dimensions": missing}


@app.post("/api/rooms/{session_id}/finish")
async def finish_room(session_id: str, payload: FinishRoomRequest) -> dict:
    room = ensure_owner(session_id, payload.visitor_id, payload.owner_token)
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    labels = {"user": "用户", "technology": "技术", "time": "时间", "cost": "成本", "risk": "风险"}
    missing = [
        {"key": key, "label": labels[key], "question": "房主确认结束时仍未充分讨论。"}
        for key in payload.missing_dimensions if key in labels
    ]
    with database() as connection:
        open_vote_ids = [row["id"] for row in connection.execute(
            "SELECT id FROM votes WHERE session_id = ? AND status = 'open'", (session_id,)
        ).fetchall()]
    for vote_id in open_vote_ids:
        await finalize_vote(session_id, vote_id)
    report = await generate_room_report(session_id, room["model"], missing)
    created_at = now_iso()
    with database() as connection:
        connection.execute(
            """INSERT INTO room_reports (session_id, minutes_markdown, retrospective_markdown, missing_dimensions, created_at)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET
               minutes_markdown = excluded.minutes_markdown, retrospective_markdown = excluded.retrospective_markdown,
               missing_dimensions = excluded.missing_dimensions, created_at = excluded.created_at""",
            (session_id, report["minutes_markdown"], report["retrospective_markdown"], json.dumps(missing, ensure_ascii=False), created_at),
        )
        connection.execute(
            "UPDATE rooms SET closed_at = ?, last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (created_at, created_at, session_id),
        )
    await manager.broadcast(session_id, {"type": "room_closed", "closed_at": created_at})
    return {**report, "created_at": created_at, "missing_dimensions": missing}


@app.get("/api/rooms/{session_id}/report")
def get_room_report(session_id: str, visitor_id: str) -> dict:
    ensure_visitor(session_id, visitor_id)
    with database() as connection:
        row = connection.execute(
            "SELECT minutes_markdown, retrospective_markdown, missing_dimensions, created_at FROM room_reports WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="讨论纪要尚未生成")
    return {**dict(row), "missing_dimensions": json.loads(row["missing_dimensions"] or "[]")}


@app.get("/api/rooms/{session_id}/report/export.md")
def export_room_report_markdown(session_id: str, visitor_id: str) -> Response:
    report = get_room_report(session_id, visitor_id)
    content = f"{report['minutes_markdown']}\n\n---\n\n{report['retrospective_markdown']}\n"
    return Response(content, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": "attachment; filename=roomai-report.md"})


@app.get("/api/rooms/{session_id}/report/export.docx")
def export_room_report_docx(session_id: str, visitor_id: str) -> Response:
    report = get_room_report(session_id, visitor_id)
    content = f"{report['minutes_markdown']}\n\n---\n\n{report['retrospective_markdown']}"
    try:
        document = markdown_to_docx(content)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="Word 导出依赖未安装，请稍后重试") from exc
    return Response(
        document,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=roomai-report.docx"},
    )


@app.put("/api/rooms/{session_id}/model")
async def update_model(session_id: str, payload: ModelRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    room = ensure_room(session_id)
    available = list(dict.fromkeys([room["model"], *AVAILABLE_MODELS]))
    if not visitor["is_owner"] or token_hash(payload.owner_token) != room["owner_token_hash"]:
        raise HTTPException(status_code=403, detail="只有房主可以切换模型")
    if room["closed_at"]:
        raise HTTPException(status_code=409, detail="讨论已经结束")
    if payload.model not in available:
        raise HTTPException(status_code=422, detail="这个模型不在可选列表中")
    with database() as connection:
        connection.execute(
            "UPDATE rooms SET model = ?, last_activity_at = ?, cleanup_warning_sent_at = NULL WHERE session_id = ?",
            (payload.model, now_iso(), session_id),
        )
    await manager.broadcast(session_id, {"type": "model_changed", "model": payload.model})
    return {"model": payload.model}


@app.websocket("/ws/{session_id}")
async def room_socket(websocket: WebSocket, session_id: str, visitor_id: str) -> None:
    visitor = visitor_for_session(session_id, visitor_id)
    if not visitor:
        await websocket.close(code=4403)
        return
    with database() as connection:
        connection.execute("UPDATE visitors SET last_seen = ? WHERE visitor_id = ?", (now_iso(), visitor_id))
    await manager.connect(session_id, visitor_id, visitor["nickname"], websocket)
    try:
        while True:
            event = await websocket.receive_json()
            if event.get("type") == "typing":
                await manager.broadcast(
                    session_id,
                    {
                        "type": "typing",
                        "visitor_id": visitor_id,
                        "nickname": visitor["nickname"],
                        "active": bool(event.get("active", True)),
                    },
                )
    except WebSocketDisconnect:
        await manager.disconnect(session_id, websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
