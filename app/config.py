"""读取环境变量和本地 .env。不覆盖已经存在的系统变量。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str) -> str:
    return os.getenv(name, "").strip().lower()


def use_mock() -> bool:
    flag = _flag("MOCK")
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return not bool(os.getenv("OPENAI_API_KEY", "").strip())


def llm_settings() -> dict[str, str]:
    return {
        "key": os.getenv("OPENAI_API_KEY", "").strip(),
        "base": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
    }


def stt_enabled() -> bool:
    if not llm_settings()["key"]:
        return False
    if _flag("ENABLE_STT") in {"1", "true", "yes", "on"}:
        return True
    return not use_mock()


def tts_enabled() -> bool:
    if not llm_settings()["key"]:
        return False
    if _flag("ENABLE_TTS") in {"1", "true", "yes", "on"}:
        return True
    return not use_mock()
