import asyncio
import os
import unittest

os.environ["MOCK"] = "1"
os.environ["ENABLE_STT"] = "0"
os.environ["ENABLE_TTS"] = "0"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402


async def _client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class ApiTests(unittest.TestCase):
    def test_page_and_health(self):
        asyncio.run(self._page_and_health())

    async def _page_and_health(self):
        async with await _client() as client:
            health = await client.get("/api/health")
            page = await client.get("/")
        self.assertEqual(health.json()["mode"], "mock")
        self.assertIn("按住说话", page.text)
        self.assertIn("待机", page.text)

    def test_story_and_stale_turn(self):
        asyncio.run(self._story_and_stale_turn())

    async def _story_and_stale_turn(self):
        async with await _client() as client:
            created = await client.post("/api/sessions")
            session_id = created.json()["sessionId"]
            first = await client.post(
                f"/api/sessions/{session_id}/turns",
                json={"turnId": 1, "text": "我来躲雨"},
            )
            second = await client.post(
                f"/api/sessions/{session_id}/turns",
                json={"turnId": 2, "text": "你在等谁"},
            )
            stale = await client.post(
                f"/api/sessions/{session_id}/turns",
                json={"turnId": 1, "text": "再见"},
            )
            saved = await client.get(f"/api/sessions/{session_id}")
        self.assertEqual(first.json()["directive"]["scene"], "lights_dim")
        self.assertEqual(second.json()["directive"]["action"], "check_phone")
        self.assertTrue(stale.json()["cancelled"])
        says = [item["say"] for item in saved.json()["history"]]
        self.assertFalse(any("早点回去" in say for say in says))

    def test_inflight_turn_is_cancelled(self):
        asyncio.run(self._inflight_turn_is_cancelled())

    async def _inflight_turn_is_cancelled(self):
        async with await _client() as client:
            created = await client.post("/api/sessions")
            session_id = created.json()["sessionId"]
            slow = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={"turnId": 1, "text": "__slow__"},
                )
            )
            await asyncio.sleep(0.3)
            newer = await client.post(
                f"/api/sessions/{session_id}/turns",
                json={"turnId": 2, "text": "我来躲雨"},
            )
            older = await slow
            saved = await client.get(f"/api/sessions/{session_id}")
        self.assertTrue(older.json()["cancelled"])
        self.assertEqual(newer.json()["directive"]["emotion"], "warm")
        users = [item["user"] for item in saved.json()["history"]]
        self.assertNotIn("__slow__", users)
        self.assertIn("我来躲雨", users)

    def test_timeout_and_transcribe(self):
        asyncio.run(self._timeout_and_transcribe())

    async def _timeout_and_transcribe(self):
        async with await _client() as client:
            created = await client.post("/api/sessions")
            session_id = created.json()["sessionId"]
            timed = await client.post(
                f"/api/sessions/{session_id}/turns",
                json={"turnId": 1, "text": "__timeout__"},
            )
            heard = await client.post(
                "/api/transcribe",
                files={"file": ("speech.webm", b"abc", "audio/webm")},
            )
            saved = await client.get(f"/api/sessions/{session_id}")
        self.assertEqual(timed.json()["error"]["code"], "timeout")
        self.assertEqual(heard.json()["error"]["code"], "stt_unavailable")
        self.assertEqual(len(saved.json()["history"]), 1)
