"""会话里只承认最新一轮。更早的回合不能再改状态。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.director import opening_directive, reduce_state


class Session:
    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.active_turn = 0
        self.cancelled_through = 0
        self.state: dict[str, Any] = {
            "asked_who": 0,
            "emotion": "wary",
            "scene": "rain",
            "action": "hold_cup",
            "turns": 0,
        }
        self.history: list[dict[str, str]] = [
            {"user": "（推门进来）", "say": opening_directive()["say"]}
        ]
        self.events: list[dict[str, Any]] = []
        self.task: Any = None
        self.task_turn: int | None = None

    def public_state(self) -> dict[str, Any]:
        return dict(self.state)

    def mark(self, kind: str, turn_id: int, **extra: Any) -> None:
        self.events.append({"kind": kind, "turnId": turn_id, "at": round(time.time(), 3), **extra})
        if len(self.events) > 200:
            del self.events[:-200]

    def begin(self, turn_id: int) -> bool:
        if turn_id <= self.cancelled_through or turn_id < self.active_turn:
            self.mark("reject", turn_id)
            return False
        self.active_turn = turn_id
        self.mark("begin", turn_id)
        return True

    def current(self, turn_id: int) -> bool:
        return turn_id == self.active_turn and turn_id > self.cancelled_through

    def cancel(self, turn_id: int) -> None:
        if turn_id > self.cancelled_through:
            self.cancelled_through = turn_id
        task = self.task
        if task is not None and self.task_turn is not None and self.task_turn <= turn_id and not task.done():
            task.cancel()
        self.mark("cancel", turn_id)

    def attach_task(self, turn_id: int, task: Any) -> None:
        previous = self.task
        if previous is not None and self.task_turn != turn_id and not previous.done():
            previous.cancel()
        self.task = task
        self.task_turn = turn_id

    def commit(self, turn_id: int, text: str, directive: dict) -> None:
        reduce_state(self.state, directive)
        self.state["turns"] = int(self.state.get("turns", 0)) + 1
        self.history.append({"user": text, "say": directive["say"]})
        if len(self.history) > 24:
            del self.history[:-24]
        self.mark("commit", turn_id, say=directive["say"])


class SessionStore:
    def __init__(self) -> None:
        self._items: dict[str, Session] = {}

    def create(self) -> Session:
        session_id = uuid.uuid4().hex[:12]
        session = Session(session_id)
        self._items[session_id] = session
        session.mark("open", 0)
        return session

    def get(self, session_id: str) -> Session | None:
        return self._items.get(session_id)
