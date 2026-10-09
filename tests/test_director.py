import os
import unittest

os.environ["MOCK"] = "1"
os.environ["ENABLE_STT"] = "0"
os.environ["ENABLE_TTS"] = "0"

from app.director import (  # noqa: E402
    fallback_directive,
    opening_directive,
    parse_directive,
    reduce_state,
    reply_for,
)


class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.state = {"asked_who": 0, "emotion": "wary", "scene": "rain", "action": "hold_cup", "turns": 0}

    def test_opening_is_wary(self):
        opening = opening_directive()
        self.assertEqual(opening["emotion"], "wary")
        self.assertEqual(opening["action"], "hold_cup")
        self.assertIn("躲雨", opening["say"])

    def test_shelter_changes_the_room(self):
        directive = reply_for(self.state, "我来躲雨")
        self.assertEqual(directive["emotion"], "warm")
        self.assertEqual(directive["scene"], "lights_dim")
        self.assertEqual(directive["action"], "hold_cup")
        self.assertIsNone(directive["media"])

    def test_second_question_triggers_the_door(self):
        first = reply_for(self.state, "你在等谁")
        self.assertEqual(first["emotion"], "sad")
        self.assertEqual(first["action"], "check_phone")
        self.assertIsNone(first["media"])
        reduce_state(self.state, first)
        second = reply_for(self.state, "到底在等谁")
        self.assertEqual(second["media"]["id"], "door_light")
        self.assertEqual(second["emotion"], "sad")

    def test_why_before_trust_only_changes_weather(self):
        directive = reply_for(self.state, "为什么不说")
        self.assertEqual(directive["scene"], "rain_heavier")
        self.assertEqual(directive["fx"], "lightning")
        self.assertIsNone(directive["media"])

    def test_bad_emotion_is_rejected(self):
        raw = '{"say":"雨还在下","emotion":"angry","action":"none","scene":"rain"}'
        self.assertIsNone(parse_directive(raw))

    def test_fence_and_unknown_media_are_cleaned(self):
        raw = """```json
        {"say":"雨还在下","emotion":"warm","action":"hold_cup","scene":"lights_dim","media":{"type":"sequence","id":"nope"}}
        ```"""
        directive = parse_directive(raw)
        self.assertEqual(directive["emotion"], "warm")
        self.assertIsNone(directive["media"])

    def test_fallback_keeps_scene(self):
        self.state["scene"] = "lights_dim"
        self.state["emotion"] = "warm"
        directive = fallback_directive(self.state)
        self.assertEqual(directive["scene"], "lights_dim")
        self.assertEqual(directive["emotion"], "warm")
        self.assertIn("再说一遍", directive["say"])
