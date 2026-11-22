from __future__ import annotations

import base64
import mimetypes
import math
from pathlib import Path

import httpx

from ..config import (
    MODEL_API_KEY,
    MODEL_BASE_URL,
    MODEL_INPUT_PRICE_PER_MILLION,
    MODEL_PRICES,
    MODEL_NAME,
    MODEL_OUTPUT_PRICE_PER_MILLION,
    MODEL_TIMEOUT_SECONDS,
    UPLOAD_DIR,
)
from ..database import database


COMMAND_PROMPTS = {
    "/总结": "请根据当前讨论，分成共识、分歧、待办三部分做简洁总结。不要补充讨论里没有的信息。",
    "/评估": "评估最近提出的核心想法：技术可行性、人力成本、时间周期、风险、创新性、团队匹配度，每项 1 到 5 分并简述理由。",
    "/投票": "整理讨论中最需要团队决定的分歧，给出清楚的问题和 2 到 4 个中立选项。",
    "/画图": "根据讨论里已经确定的方案，用 Mermaid flowchart 代码画出主要步骤，并简短解释。",
    "/挑战": "站在红队角度，提出当前推荐方向最关键的 1 到 2 个风险，用问题表达，不要长篇否定。",
    "/待办": "从讨论中找出明确的下一步待办，并列出负责人（如果讨论中提到）和待确认事项。",
    "/结束": "先检查用户、技术、时间、成本、风险五个维度是否讨论充分。指出尚未覆盖的内容，等待房主确认后再结束。",
}


def _usage_from_response(payload: dict, messages: list[dict]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None:
        prompt = math.ceil(sum(len(str(item.get("content", ""))) for item in messages) / 4)
    if completion is None:
        completion = math.ceil(len(str(payload.get("choices", [{}])[0].get("message", {}).get("content", ""))) / 4)
    return {"prompt_tokens": int(prompt), "completion_tokens": int(completion)}


async def complete_chat(
    messages: list[dict],
    *,
    model: str | None = None,
    session_id: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.45,
) -> dict | None:
    """Call the configured OpenAI-compatible endpoint, retry once, and record usage."""
    if not MODEL_API_KEY:
        return None
    selected_model = model or MODEL_NAME
    body = {"model": selected_model, "temperature": temperature, "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{MODEL_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {MODEL_API_KEY}"},
                    json=body,
                )
                response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model response")
            usage = _usage_from_response(payload, messages)
            prices = MODEL_PRICES.get(selected_model, {})
            input_price = prices.get("input", MODEL_INPUT_PRICE_PER_MILLION)
            output_price = prices.get("output", MODEL_OUTPUT_PRICE_PER_MILLION)
            cost = (usage["prompt_tokens"] * input_price + usage["completion_tokens"] * output_price) / 1_000_000
            if session_id:
                with database() as connection:
                    connection.execute(
                        "INSERT INTO usage (session_id, model, prompt_tokens, completion_tokens, cost_estimate, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (session_id, selected_model, usage["prompt_tokens"], usage["completion_tokens"], cost),
                    )
            return {"content": content.strip(), "usage": usage, "model": selected_model}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            if attempt == 1:
                return None
    return None


def _image_content(image_path: str) -> dict | None:
    path = (UPLOAD_DIR / image_path).resolve()
    if not path.is_relative_to(UPLOAD_DIR.resolve()) or not path.is_file():
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def history_message(item: dict) -> dict:
    role = "assistant" if item.get("sender_type") == "agent" else "user"
    text = f"{item.get('sender_name', '成员')}: {item.get('content') or ''}"
    if item.get("reply_content"):
        text += f"\n（回复 {item.get('reply_sender_name', '成员')}：{item['reply_content']}）"
    image = _image_content(item["image_path"]) if item.get("image_path") and not item.get("is_recalled") else None
    if image:
        return {"role": role, "content": [{"type": "text", "text": text}, image]}
    if item.get("image_path") and not item.get("is_recalled"):
        text += "（消息附有图片；图片文件已不可用）"
    return {"role": role, "content": text}


async def answer_room_prompt(
    topic: str,
    history: list[dict],
    prompt: str,
    *,
    model: str | None = None,
    session_id: str | None = None,
    summary: str | None = None,
    structured_context: str | None = None,
) -> str | None:
    system = (
        "你是 RoomAI，一个帮助团队讨论立项想法的中文协作助手。紧扣房间主题和已有对话，"
        "回答具体、克制、易读；不要伪造成员观点、决定、评分或待办。若依据不足，明确说还需要讨论什么。"
        f"\n房间主题：{topic}"
    )
    if summary:
        system += f"\n滚动总结：{summary}"
    if structured_context:
        system += f"\n结构化记录：{structured_context}"
    messages = [{"role": "system", "content": system}]
    messages.extend(history_message(item) for item in history[-30:] if not item.get("is_recalled"))
    messages.append({"role": "user", "content": prompt})
    result = await complete_chat(messages, model=model, session_id=session_id)
    return result["content"] if result else None
