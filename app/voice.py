"""云端听写和语音合成。没有密钥时由页面使用浏览器语音。"""

from __future__ import annotations

import base64

import httpx

from app.config import llm_settings, stt_enabled, tts_enabled


class SttUnavailable(Exception):
    """当前没有可调用的听写服务。"""


async def transcribe(data: bytes, filename: str, content_type: str) -> str:
    if not stt_enabled():
        raise SttUnavailable()
    settings = llm_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings['base']}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings['key']}"},
            data={"model": "whisper-1"},
            files={"file": (filename or "speech.webm", data, content_type or "application/octet-stream")},
        )
        response.raise_for_status()
        text = str(response.json().get("text", "")).strip()
    if not text:
        raise SttUnavailable()
    return text[:80]


async def synthesize(text: str) -> dict[str, str] | None:
    if not tts_enabled():
        return None
    settings = llm_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings['base']}/audio/speech",
            headers={"Authorization": f"Bearer {settings['key']}"},
            json={"model": "tts-1", "voice": "nova", "input": text},
        )
        response.raise_for_status()
        payload = base64.b64encode(response.content).decode("ascii")
    return {"mime": "audio/mpeg", "base64": payload}
