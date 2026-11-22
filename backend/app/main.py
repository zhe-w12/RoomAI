from __future__ import annotations

import hashlib
import asyncio
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import FRONTEND_ORIGIN, MODEL_NAME, UPLOAD_DIR
from .database import database, initialize_database
from .realtime import manager, visitor_for_session
from .services.brief import create_room_brief
from .services.agent import COMMAND_PROMPTS, answer_room_prompt


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
    content: str = Field(default="", max_length=10000)
    reply_to_message_id: int | None = None
    image_url: str | None = None


class PhaseRequest(BaseModel):
    visitor_id: str
    owner_token: str
    phase: str = Field(pattern="^(diverge|converge)$")


class ReadMentionsRequest(BaseModel):
    visitor_id: str
    message_ids: list[int] | None = None


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


@app.on_event("startup")
def startup() -> None:
    initialize_database()


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

    brief = await create_room_brief(topic)
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
    prompt = ai_prompt(content)
    if prompt:
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
                "SELECT sender_name, sender_type, content, image_path, is_recalled FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 30",
                (session_id,),
            ).fetchall()]
        history.reverse()
        if prompt == "__HELP__":
            answer = "可以试试这些快捷指令：/总结 梳理共识、分歧和待办；/评估 评估核心想法；/投票整理待决定的问题；/画图生成方案流程；/挑战检查风险；/待办提取下一步。也可以直接 @RoomAI 提问。"
        else:
            answer = await answer_room_prompt(room["topic"], history, prompt)
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
