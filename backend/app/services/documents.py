from __future__ import annotations

import json
import math
import re
from collections import Counter
from io import BytesIO
from pathlib import Path

from ..database import database


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARS = 300_000
CHUNK_CHARS = 1_200
CHUNK_OVERLAP = 160


def _clean_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_document_text(filename: str, data: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
        return _clean_text(text)

    if extension == ".pdf":
        from pypdf import PdfReader

        pages = []
        reader = PdfReader(BytesIO(data))
        for index, page in enumerate(reader.pages, 1):
            text = _clean_text(page.extract_text() or "")
            if text:
                pages.append(f"[第 {index} 页]\n{text}")
        return _clean_text("\n\n".join(pages))

    if extension == ".docx":
        from docx import Document

        document = Document(BytesIO(data))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return _clean_text("\n".join(parts))

    if extension == ".pptx":
        from pptx import Presentation

        deck = Presentation(BytesIO(data))
        parts = []
        for slide_number, slide in enumerate(deck.slides, 1):
            lines = [shape.text.strip() for shape in slide.shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()]
            if lines:
                parts.append(f"[第 {slide_number} 张幻灯片]\n" + "\n".join(lines))
        return _clean_text("\n\n".join(parts))

    raise ValueError("暂不支持这个文件格式")


def split_into_chunks(text: str) -> list[str]:
    text = _clean_text(text)
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            floor = start + CHUNK_CHARS // 2
            boundary = max(
                text.rfind("\n", floor, end),
                text.rfind("。", floor, end),
                text.rfind("！", floor, end),
                text.rfind("？", floor, end),
                text.rfind(".", floor, end),
            )
            if boundary >= floor:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _tokens(text: str) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold()):
        if "\u4e00" <= token[0] <= "\u9fff":
            if len(token) == 1:
                tokens[token] += 1
            else:
                for size in (2, 3):
                    tokens.update(token[index:index + size] for index in range(len(token) - size + 1))
        elif len(token) > 1:
            tokens[token] += 1
    return tokens


def token_vector(text: str) -> str:
    return json.dumps(_tokens(text), ensure_ascii=False, separators=(",", ":"))


def retrieve_document_chunks(session_id: str, query: str, *, limit: int = 4) -> list[dict]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    with database() as connection:
        rows = connection.execute(
            """SELECT d.id AS document_id, d.filename, c.chunk_index, c.content, c.vector_json
               FROM room_documents d JOIN document_chunks c ON c.document_id = d.id
               WHERE d.session_id = ?""",
            (session_id,),
        ).fetchall()
    if not rows:
        return []

    vectors = [json.loads(row["vector_json"]) for row in rows]
    document_frequency: Counter[str] = Counter()
    for vector in vectors:
        document_frequency.update(vector.keys())
    document_count = len(vectors)
    query_weighted = {
        token: count * (math.log((document_count + 1) / (document_frequency.get(token, 0) + 1)) + 1)
        for token, count in query_tokens.items()
    }
    query_norm = math.sqrt(sum(weight * weight for weight in query_weighted.values())) or 1

    scored = []
    for row, vector in zip(rows, vectors):
        weighted = {
            token: count * (math.log((document_count + 1) / (document_frequency.get(token, 0) + 1)) + 1)
            for token, count in vector.items()
        }
        norm = math.sqrt(sum(weight * weight for weight in weighted.values())) or 1
        score = sum(query_weighted.get(token, 0) * weight for token, weight in weighted.items()) / (query_norm * norm)
        if score >= 0.06:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "kind": "document",
            "title": row["filename"],
            "document_id": row["document_id"],
            "chunk_index": row["chunk_index"] + 1,
            "content": row["content"],
        }
        for _, row in scored[:max(1, min(limit, 6))]
    ]
