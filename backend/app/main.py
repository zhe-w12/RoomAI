from __future__ import annotations

import hashlib
import asyncio
import re
import secrets
import uuid
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    AVAILABLE_MODELS,
    FALLBACK_TRIGGER_MESSAGES,
    FRONTEND_ORIGIN,
    IDLE_TRIGGER_MIN_MESSAGES,
    IDLE_TRIGGER_SECONDS,
    MODEL_INPUT_PRICE_PER_MILLION,
    MODEL_NAME,
    MODEL_OUTPUT_PRICE_PER_MILLION,
    MODEL_PRICES,
    UPLOAD_DIR,
)
from .database import database, initialize_database
from .realtime import manager, visitor_for_session
from .services.brief import create_room_brief
from .services.agent import COMMAND_PROMPTS, answer_room_prompt
from .services.analysis import analyze_room
from .services.voting import propose_vote


app = FastAPI(title="RoomAI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

ALLOWED_IMAGES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


class CreateRoomRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=240)
    nickname: str = Field(min_length=1, max_length=32)


class JoinRoomRequest(BaseModel):
    invite_code: str = Field(min_length=6, max_length=6)
    nickname: str = Field(min_length=1, max_length=32)


class JoinByLinkRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)


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


class ReadMentionsRequest(BaseModel):
    visitor_id: str
    message_ids: list[int] | None = None


_idle_tasks: dict[str, asyncio.Task] = {}
_analysis_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_message_rate: defaultdict[str, deque[float]] = defaultdict(deque)
_vote_expiry_tasks: dict[int, asyncio.Task] = {}
_vote_creation_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def create_message_record(
    session_id: str,
    visitor_id: str | None,
    sender_name: str,
    sender_type: str,
    content: str,
    created_at: str,
    reply_to_message_id: int | None = None,
    image_path: str | None = None,
) -> dict:
    with database() as connection:
        cursor = connection.execute(
            """INSERT INTO messages
               (session_id, visitor_id, sender_name, sender_type, content, reply_to_message_id, image_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, visitor_id, sender_name, sender_type, content, reply_to_message_id, image_path, created_at),
        )
        message_id = cursor.lastrowid
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
        disagreements = result["summary"].get("disagreements", [])
        if auto_vote and disagreements:
            detail = "讨论分析识别到以下分歧，请围绕这些真实观点拟定中立投票：\n" + "\n".join(
                f"- {item}" for item in disagreements[:4]
            )
            asyncio.create_task(create_ai_vote(session_id, detail))
        return result


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


@app.on_event("startup")
async def startup() -> None:
    initialize_database()
    with database() as connection:
        open_votes = connection.execute(
            "SELECT id, session_id, closes_at FROM votes WHERE status = 'open' AND closes_at IS NOT NULL"
        ).fetchall()
    for vote in open_votes:
        remaining = max(0, int((datetime.fromisoformat(vote["closes_at"]) - datetime.now(timezone.utc)).total_seconds()))
        _vote_expiry_tasks[vote["id"]] = asyncio.create_task(expire_vote_after(vote["session_id"], vote["id"], remaining))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/api/rooms")
async def create_room(payload: CreateRoomRequest) -> dict:
    topic = payload.topic.strip()
    nickname = payload.nickname.strip()
    if not topic or not nickname:
        raise HTTPException(status_code=422, detail="主题和昵称不能为空")
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
                   (session_id, topic, invite_code, owner_token_hash, owner_visitor_id, model, phase, prep_checklist, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'diverge', '[]', ?)""",
                (session_id, topic, invite_code, token_hash(owner_token), visitor_id, MODEL_NAME, created_at),
            )
            connection.execute(
                "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 1, ?)",
                (visitor_id, session_id, nickname, created_at),
            )
        except Exception:
            raise

    brief = await create_room_brief(topic, session_id=session_id, model=MODEL_NAME)
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
            "SELECT session_id FROM rooms WHERE invite_code = ?", (payload.invite_code.strip(),)
        ).fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="没有找到这个邀请码")
        session_id = room["session_id"]
        duplicate = connection.execute(
            "SELECT 1 FROM visitors WHERE session_id = ? AND nickname = ? COLLATE NOCASE",
            (session_id, nickname),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="这个房间里已经有人使用该昵称")
        visitor_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 0, ?)",
            (visitor_id, session_id, nickname, now_iso()),
        )
    return {"session_id": session_id, "visitor_id": visitor_id, "nickname": nickname}


@app.post("/api/rooms/{session_id}/visitors")
def join_room_by_link(session_id: str, payload: JoinByLinkRequest) -> dict:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="昵称不能为空")
    ensure_room(session_id)
    with database() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM visitors WHERE session_id = ? AND nickname = ? COLLATE NOCASE",
            (session_id, nickname),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="这个房间里已经有人使用该昵称")
        visitor_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO visitors (visitor_id, session_id, nickname, is_owner, last_seen) VALUES (?, ?, ?, 0, ?)",
            (visitor_id, session_id, nickname, now_iso()),
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
        "invite_code": room["invite_code"],
        "model": room["model"],
        "phase": room["phase"],
        "available_models": list(dict.fromkeys([room["model"], *AVAILABLE_MODELS])),
        "prep_checklist": json.loads(room["prep_checklist"]),
        "members": [dict(member) for member in members],
        "unread_mentions": unread_mentions,
        "created_at": room["created_at"],
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
    vote = await publish_vote(session_id, payload.visitor_id, payload.question, payload.options, payload.duration_minutes)
    return {"vote": vote}


@app.post("/api/rooms/{session_id}/votes/{vote_id}/records")
async def cast_vote(session_id: str, vote_id: int, payload: VoteRecordRequest) -> dict:
    ensure_visitor(session_id, payload.visitor_id)
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
            connection.execute(
                """INSERT INTO vote_records (vote_id, visitor_id, option_id, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(vote_id, visitor_id) DO UPDATE SET option_id = excluded.option_id, updated_at = excluded.updated_at""",
                (vote_id, payload.visitor_id, payload.option_id, now_iso()),
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
    task = _vote_expiry_tasks.pop(vote_id, None)
    if task and not task.done():
        task.cancel()
    state = await finalize_vote(session_id, vote_id)
    if not state:
        raise HTTPException(status_code=404, detail="找不到这张投票卡")
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
    prompt = ai_prompt(content)
    is_vote_request = content.startswith("/投票") or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE) and "投票" in content
    )
    is_summary_request = content.startswith(("/总结", "/评估")) or (
        re.search(r"@(AI|RoomAI)\b", content, re.IGNORECASE)
        and re.search(r"总结|梳理共识|评估", content)
    )
    if is_vote_request:
        detail = content[len("/投票"):].strip() if content.startswith("/投票") else content
        asyncio.create_task(create_ai_vote(session_id, detail))
    elif is_summary_request:
        asyncio.create_task(publish_room_analysis(session_id, auto_vote=False))
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
        return re.sub(r"@(AI|RoomAI)\b", "", stripped, flags=re.IGNORECASE).strip() or "请根据目前的讨论，整理共识与下一步。"
    return None


async def generate_agent_reply(session_id: str, prompt: str) -> None:
    await manager.broadcast(
        session_id,
        {"type": "typing", "visitor_id": "roomai", "nickname": "RoomAI", "active": True},
    )
    try:
        room = ensure_room(session_id)
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
                "SELECT title, description FROM ideas WHERE session_id = ? ORDER BY updated_at DESC LIMIT 20", (session_id,)
            ).fetchall()
        history.reverse()
        summary_context = json.loads(summary_row["content"]) if summary_row else None
        structured_context = json.dumps([dict(row) for row in idea_rows], ensure_ascii=False) if idea_rows else None
        if prompt == "__HELP__":
            answer = "可以试试这些快捷指令：/总结 梳理共识、分歧和待办；/评估 评估核心想法；/投票整理待决定的问题；/画图生成方案流程；/挑战检查风险；/待办提取下一步。也可以直接 @RoomAI 提问。"
        else:
            answer = await answer_room_prompt(
                room["topic"], history, prompt, model=room["model"], session_id=session_id,
                summary=json.dumps(summary_context, ensure_ascii=False) if summary_context else None,
                structured_context=structured_context,
            )
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


@app.post("/api/rooms/{session_id}/upload")
async def upload_image(
    session_id: str,
    visitor_id: str = Form(...),
    image: UploadFile = File(...),
) -> dict:
    ensure_visitor(session_id, visitor_id)
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
    return {"image_url": f"/uploads/{session_id}/{filename}"}


@app.post("/api/rooms/{session_id}/messages/{message_id}/recall")
async def recall_message(session_id: str, message_id: int, visitor_id: str) -> dict:
    visitor = ensure_visitor(session_id, visitor_id)
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
    with database() as connection:
        connection.execute("UPDATE rooms SET phase = ? WHERE session_id = ?", (payload.phase, session_id))
    await manager.broadcast(session_id, {"type": "phase_changed", "phase": payload.phase})
    return {"phase": payload.phase}


@app.put("/api/rooms/{session_id}/model")
async def update_model(session_id: str, payload: ModelRequest) -> dict:
    visitor = ensure_visitor(session_id, payload.visitor_id)
    room = ensure_room(session_id)
    available = list(dict.fromkeys([room["model"], *AVAILABLE_MODELS]))
    if not visitor["is_owner"] or token_hash(payload.owner_token) != room["owner_token_hash"]:
        raise HTTPException(status_code=403, detail="只有房主可以切换模型")
    if payload.model not in available:
        raise HTTPException(status_code=422, detail="这个模型不在可选列表中")
    with database() as connection:
        connection.execute("UPDATE rooms SET model = ? WHERE session_id = ?", (payload.model, session_id))
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
