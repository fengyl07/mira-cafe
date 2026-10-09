"""场景服务：一轮对话一个 turnId，过期结果不能再改画面。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import WEB_DIR, load_dotenv, llm_settings, use_mock
from app.director import (
    SYSTEM_PROMPT,
    fallback_directive,
    opening_directive,
    parse_directive,
    reply_for,
)
from app.session import Session, SessionStore
from app.voice import SttUnavailable, synthesize, transcribe

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mira")

app = FastAPI(title="雨夜咖啡馆", version="0.1.0")
store = SessionStore()
MAX_AUDIO_BYTES = 5 * 1024 * 1024


class TurnIn(BaseModel):
    turnId: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=80)


class CancelIn(BaseModel):
    turnId: int = Field(ge=1)


def _cancelled(turn_id: int) -> dict[str, Any]:
    return {"cancelled": True, "turnId": turn_id}


def _error(turn_id: int, code: str, message: str) -> dict[str, Any]:
    return {"cancelled": False, "turnId": turn_id, "error": {"code": code, "message": message}}


def _require(session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


async def _chat(session: Session, text: str) -> str:
    settings = llm_settings()
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in session.history[-6:]:
        if item["user"]:
            messages.append({"role": "user", "content": item["user"]})
        if item["say"]:
            messages.append({"role": "assistant", "content": item["say"]})
    state = session.public_state()
    messages.append(
        {
            "role": "user",
            "content": (
                f"当前情绪：{state['emotion']}。当前场景：{state['scene']}。"
                f"已经问过等谁的次数：{state['asked_who']}。\n用户说：{text}"
            ),
        }
    )
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            f"{settings['base']}/chat/completions",
            headers={"Authorization": f"Bearer {settings['key']}"},
            json={"model": settings["model"], "temperature": 0.7, "messages": messages},
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"])


async def _directive(session: Session, text: str) -> dict:
    if use_mock():
        return reply_for(session.state, text)
    last_error: Exception | None = None
    for _ in range(2):
        try:
            parsed = parse_directive(await _chat(session, text))
        except (httpx.TimeoutException, httpx.HTTPError):
            raise
        except Exception as exc:
            last_error = exc
            continue
        if parsed is not None:
            return parsed
        last_error = ValueError("模型没有返回合法指令")
    logger.warning("模型指令不可用，改用保底台词：%s", last_error)
    return fallback_directive(session.state)


async def _produce(session: Session, text: str) -> tuple[dict, dict | None]:
    if text == "__timeout__":
        raise TimeoutError()
    if text == "__slow__":
        await asyncio.sleep(1.2)
    elif use_mock():
        await asyncio.sleep(0.45)
    directive = await _directive(session, text)
    speech = None
    try:
        speech = await synthesize(directive["say"])
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("语音合成失败，改由浏览器朗读：%s", exc)
    return directive, speech


async def run_turn(session: Session, turn_id: int, text: str, request: Request) -> dict[str, Any]:
    if not session.begin(turn_id):
        return _cancelled(turn_id)

    task = asyncio.create_task(_produce(session, text))
    session.attach_task(turn_id, task)
    try:
        directive, speech = await task
    except asyncio.CancelledError:
        session.mark("dropped", turn_id)
        return _cancelled(turn_id)
    except (httpx.TimeoutException, TimeoutError):
        if not session.current(turn_id):
            return _cancelled(turn_id)
        session.mark("timeout", turn_id)
        return _error(turn_id, "timeout", "她没接上话")
    except Exception:
        logger.exception("回合失败")
        if not session.current(turn_id):
            return _cancelled(turn_id)
        session.mark("error", turn_id)
        return _error(turn_id, "upstream", "她这会儿接不上话")

    if not session.current(turn_id) or await request.is_disconnected():
        session.mark("dropped", turn_id)
        return _cancelled(turn_id)

    session.commit(turn_id, text, directive)
    return {
        "cancelled": False,
        "turnId": turn_id,
        "mode": "mock" if use_mock() else "live",
        "directive": directive,
        "speech": speech,
        "state": session.public_state(),
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"ok": "true", "mode": "mock" if use_mock() else "live"}


@app.post("/api/sessions")
def create_session() -> dict[str, Any]:
    session = store.create()
    return {
        "sessionId": session.id,
        "mode": "mock" if use_mock() else "live",
        "opening": opening_directive(),
        "state": session.public_state(),
    }


@app.get("/api/sessions/{session_id}")
def read_session(session_id: str) -> dict[str, Any]:
    session = _require(session_id)
    return {
        "sessionId": session.id,
        "mode": "mock" if use_mock() else "live",
        "state": session.public_state(),
        "history": session.history,
        "events": session.events[-40:],
    }


@app.post("/api/sessions/{session_id}/turns")
async def create_turn(session_id: str, body: TurnIn, request: Request) -> dict[str, Any]:
    session = _require(session_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="请说点什么")
    return await run_turn(session, body.turnId, text, request)


@app.post("/api/sessions/{session_id}/cancel")
def cancel_turn(session_id: str, body: CancelIn) -> dict[str, Any]:
    session = _require(session_id)
    session.cancel(body.turnId)
    return {"ok": True, "turnId": body.turnId}


@app.post("/api/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    turnId: int = Form(default=0),
) -> dict[str, Any]:
    data = await file.read()
    if not data or len(data) > MAX_AUDIO_BYTES:
        return {"turnId": turnId, "error": {"code": "audio", "message": "没听清，再说一次"}}
    try:
        text = await transcribe(data, file.filename or "speech.webm", file.content_type or "")
    except SttUnavailable:
        return {"turnId": turnId, "error": {"code": "stt_unavailable", "message": "没听清，再说一次"}}
    except (httpx.TimeoutException, httpx.HTTPError):
        logger.exception("听写失败")
        return {"turnId": turnId, "error": {"code": "stt_failed", "message": "没听清，再说一次"}}
    return {"turnId": turnId, "text": text}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
