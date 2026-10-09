"""把一轮对话收成页面能执行的角色指令。"""

from __future__ import annotations

import json

EMOTIONS = {"wary", "warm", "sad"}
ACTIONS = {"none", "hold_cup", "check_phone"}
SCENES = {"rain", "rain_heavier", "lights_dim"}
POSES = {"idle", "listen", "think", "speak"}

WHO_WORDS = ("等谁", "等人", "等什么人", "等哪位", "等的人", "谁要来")
WHY_WORDS = ("为什么", "怎么还不", "放鸽子", "不来")
DOOR_WORDS = ("门口", "那道光", "再看一次")


def normalize(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    say = data.get("say")
    if not isinstance(say, str):
        return None
    say = say.strip()
    if not say or len(say) > 80:
        return None
    emotion = data.get("emotion")
    action = data.get("action")
    scene = data.get("scene")
    if emotion not in EMOTIONS or action not in ACTIONS or scene not in SCENES:
        return None
    pose = data.get("pose")
    if pose not in POSES:
        pose = "speak"
    media = data.get("media")
    clean_media = None
    if isinstance(media, dict) and media.get("type") == "sequence" and media.get("id") == "door_light":
        clean_media = {"type": "sequence", "id": "door_light"}
    fx = "lightning" if data.get("fx") == "lightning" else None
    return {
        "say": say,
        "emotion": emotion,
        "pose": pose,
        "action": action,
        "scene": scene,
        "fx": fx,
        "media": clean_media,
    }


def parse_directive(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return normalize(data)


def _pack(
    say: str,
    emotion: str,
    action: str,
    scene: str,
    media: dict | None = None,
    fx: str | None = None,
) -> dict:
    cleaned = normalize(
        {
            "say": say,
            "emotion": emotion,
            "pose": "speak",
            "action": action,
            "scene": scene,
            "fx": fx,
            "media": media,
        }
    )
    if cleaned is None:
        raise RuntimeError("剧本指令不合法")
    return cleaned


OPENING = _pack("雨还没停。你也是来躲雨的？", "wary", "hold_cup", "rain")


def opening_directive() -> dict:
    return dict(OPENING)


def fallback_directive(state: dict) -> dict:
    scene = state.get("scene") if state.get("scene") in SCENES else "rain"
    emotion = state.get("emotion") if state.get("emotion") in EMOTIONS else "wary"
    return _pack("雨声有点大，你再说一遍好吗。", emotion, "none", scene)


def reply_for(state: dict, text: str) -> dict:
    """按当前剧情状态选择下一句。视觉事件只挂在对应台词上。"""
    line = text.strip()
    asked = int(state.get("asked_who", 0))
    scene = state.get("scene") if state.get("scene") in SCENES else "rain"
    who = any(word in line for word in WHO_WORDS)
    why = any(word in line for word in WHY_WORDS)
    door = _pack(
        "你听见门口那声了吗？不是他。",
        "sad",
        "check_phone",
        scene,
        {"type": "sequence", "id": "door_light"},
    )

    if asked >= 1 and (who or why or any(word in line for word in DOOR_WORDS)):
        return door
    if who:
        return _pack("……一个说好要来的人。雨这么大，也许不会来了。", "sad", "check_phone", "rain")
    if why:
        return _pack("有些话，雨声大的时候反而不想讲。", "wary", "hold_cup", "rain_heavier", fx="lightning")
    if any(word in line for word in ("躲雨", "躲一下", "咖啡", "坐一会", "坐一会儿", "喝一杯", "借坐", "可以坐")):
        return _pack("那就坐一会儿吧。厨房已经关了，杯子倒还是热的。", "warm", "hold_cup", "lights_dim")
    if any(word in line for word in ("你是谁", "名字", "叫什么", "摄影师", "拍照")):
        return _pack("Mira。我平时靠拍照走路，不过今晚相机一直在包里。", "warm", "hold_cup", scene)
    if any(word in line for word in ("发夹", "星星", "雨衣")):
        return _pack("这枚发夹是在海边摊上买的。老板说它夜里会反光。", "warm", "none", "lights_dim")
    if any(word in line for word in ("再见", "我走", "先走", "打烊")):
        return _pack("雨要是小一点，我就走。你也早点回去。", "warm", "none", scene)
    if line in {"嗯", "啊", "哦", "对", "是", "好"}:
        return _pack("嗯。雨还在下，你慢慢说。", "wary", "hold_cup", scene)

    defaults = (
        _pack("这雨一时停不了。你要是不急，就在灯底下站一会儿。", "wary", "hold_cup", scene),
        _pack("我在看玻璃上的水痕。它们把对面的灯拉得很长。", "wary", "none", scene),
    )
    return defaults[int(state.get("turns", 0)) % len(defaults)]


def reduce_state(state: dict, directive: dict) -> None:
    say = directive["say"]
    media = directive.get("media") or {}
    if "说好要来" in say or media.get("id") == "door_light":
        state["asked_who"] = int(state.get("asked_who", 0)) + 1
    state["emotion"] = directive["emotion"]
    state["scene"] = directive["scene"]
    state["action"] = directive["action"]


SYSTEM_PROMPT = """你是 Mira，26 岁，旅行摄影师。此刻是暴雨后的夜晚，城南一家即将打烊的咖啡馆。你穿着琥珀色雨衣，戴着银色星星发夹，在等一个说好要来的人，但不肯第一句就讲明。
只输出一个 JSON 对象，不要 Markdown，不要解释。
字段：
say：一句中文对白，不超过 40 字
emotion：wary、warm、sad 之一
pose：speak
action：none、hold_cup、check_phone 之一
scene：rain、rain_heavier、lights_dim 之一
fx：null，或 lightning
media：null，或 {"type":"sequence","id":"door_light"}
只有用户已经追问过她在等谁、并且再次追问或提到门口时，media 才能是 door_light。
保持人物、雨衣和咖啡馆连续。不要提系统、模型或 JSON。"""
