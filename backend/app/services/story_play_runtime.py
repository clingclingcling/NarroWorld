"""
聊天流驱动的剧情运行时
"""

import re
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..models.story import DecisionOption, PlotNode
from .story_graph import NarrativeGraphService
from .world_state import NarrativePlanner, PlayerInteractionService, WorldState, WorldStateEngine


def _now_dt() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now_dt().isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _world_state_dict(story_data: Dict[str, Any]) -> Dict[str, Any]:
    world_state = story_data.get("world_state") or {}
    if isinstance(world_state, dict):
        return world_state
    return dict(vars(world_state))


class NarrativeEventAdapter:
    INTERNAL_PATTERNS = [
        "正在围绕",
        "试图掌握局势",
        "回应《",
        "世界脉络正在接入",
        "主线提示：",
        "剧情推进：",
        "已进入你的联络视野",
    ]

    @classmethod
    def intro_messages(cls, story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        title = story_data.get("title", "NarraWorld")
        mainline = cls._clean_text(story_data.get("main_storyline") or story_data.get("summary") or "")
        messages = [
            cls._message(
                "system",
                f"你已进入《{title}》。",
                metadata={"kind": "world_intro", "layer": "system"},
            ),
            cls._message(
                "scene",
                cls._scene_notice(story_data),
                delay_ms=760,
                metadata={"scene_id": _world_state_dict(story_data).get("current_scene_id"), "layer": "system"},
            ),
        ]
        if mainline:
            messages.append(
                cls._message(
                    "system",
                    cls._truncate(mainline, 72),
                    delay_ms=980,
                    metadata={"kind": "world_hook", "layer": "system"},
                )
            )
        for character in cls._visible_speakers(story_data)[:1]:
            messages.append(
                cls._message(
                    "character",
                    cls._intro_character_line(character),
                    author=character.get("canonical_name") or character.get("name"),
                    character_id=character.get("id"),
                    delay_ms=1200,
                    metadata={"kind": "character_intro", "layer": "character"},
                )
            )
        return messages

    @classmethod
    def event_messages(cls, story_data: Dict[str, Any], event: Dict[str, Any]) -> List[Dict[str, Any]]:
        messages = []
        if event.get("scenes"):
            scene = next((item for item in story_data.get("scenes", []) if item["id"] == event["scenes"][0]), None)
            if scene:
                messages.append(
                    cls._message(
                        "scene",
                        cls._scene_notice(story_data, scene),
                        delay_ms=640,
                        metadata={"scene_id": scene["id"], "kind": "scene_shift", "layer": "system"},
                    )
                )

        system_line = cls._event_system_line(event)
        if system_line:
            messages.append(
                cls._message(
                    "system",
                    system_line,
                    delay_ms=980,
                    metadata={"event_id": event["id"], "kind": "narration", "layer": "system"},
                )
            )

        character = cls._pick_event_speaker(story_data, event)
        character_line = cls._character_line(character, event) if character else ""
        if character and character_line:
            messages.append(
                cls._message(
                    "character",
                    character_line,
                    author=character.get("canonical_name") or character.get("name"),
                    character_id=character.get("id"),
                    delay_ms=1320,
                    metadata={"event_id": event["id"], "kind": "character_update", "layer": "character"},
                )
            )

        for clue_id in event.get("clues", [])[:1]:
            clue = next((item for item in story_data.get("clues", []) if item["id"] == clue_id), None)
            if clue:
                messages.append(
                    cls._message(
                        "clue",
                        f"新线索已解锁：{cls._clean_text(clue['title'])}",
                        delay_ms=1160,
                        metadata={"clue_id": clue_id, "kind": "clue_unlock", "layer": "system"},
                    )
                )
        return messages

    @classmethod
    def player_feedback_messages(cls, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        text = cls._player_feedback_line(result)
        return [
            cls._message(
                "system",
                text,
                delay_ms=520,
                metadata={"kind": "player_feedback", "intent": result.get("intent"), "layer": "system"},
            )
        ]

    @classmethod
    def choice_feedback_messages(cls, selected: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            cls._message(
                "system",
                cls._choice_feedback_line(selected),
                delay_ms=900,
                metadata={"kind": "choice_result", "layer": "system"},
            )
        ]

    @classmethod
    def decision_prompt_messages(cls, plot_node: PlotNode) -> List[Dict[str, Any]]:
        return [
            cls._message(
                "decision",
                plot_node.prompt or plot_node.summary or "你要怎么做？",
                metadata={"plot_node_id": plot_node.id, "kind": "decision_prompt", "layer": "decision"},
            )
        ]

    @classmethod
    def sanitize_visible_messages(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned = []
        seen = set()
        for message in messages:
            normalized = cls._sanitize_message(message)
            if not normalized:
                continue
            key = (
                normalized.get("type"),
                normalized.get("author"),
                normalized.get("text"),
                normalized.get("character_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    @classmethod
    def _sanitize_message(cls, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = dict(message)
        text = cls._clean_text(payload.get("text", ""))
        msg_type = payload.get("type", "system")
        author = payload.get("author", "")

        if any(pattern in text for pattern in cls.INTERNAL_PATTERNS):
            if msg_type == "character" and author:
                text = "我这边有情况，稍后告诉你。"
            elif text.startswith("剧情推进："):
                text = cls._clean_text(text.replace("剧情推进：", "", 1))
                msg_type = "system"
            else:
                return None

        if text.startswith("主线提示："):
            text = cls._clean_text(text.replace("主线提示：", "", 1))
        if text == "剧情正在根据你的选择重新编排。":
            text = "你的决定正在改变接下来的走向。"
        if not text:
            return None

        payload["text"] = text
        payload["type"] = msg_type
        return payload

    @classmethod
    def _character_line(cls, character: Dict[str, Any], event: Dict[str, Any]) -> str:
        if not character:
            return ""
        hook = cls._event_hook_text(event)
        name = character.get("canonical_name") or character.get("name") or "他"
        for alias in [name, character.get("name", ""), *(character.get("aliases") or [])]:
            alias = cls._clean_text(alias)
            if alias:
                hook = hook.replace(alias, "我")
        hook = cls._clean_text(hook)

        if "不要相信" in hook or "别相信" in hook:
            return f"我刚收到一条匿名消息：{cls._truncate(hook, 42)}"
        if any(keyword in hook for keyword in ["短信", "消息", "来电", "电话", "震动"]):
            return f"我这边刚有新动静。{cls._truncate(hook, 48)}"
        if any(keyword in hook for keyword in ["追踪", "定位", "监控", "失联"]):
            return f"我在盯这条线。{cls._truncate(hook, 46)}"
        if hook:
            if hook.startswith("我"):
                return cls._truncate(hook, 52)
            return f"我这边有情况。{cls._truncate(hook, 44)}"
        return "我这边有点不对劲，先别惊动别人。"

    @classmethod
    def _intro_character_line(cls, character: Dict[str, Any]) -> str:
        name = character.get("canonical_name") or character.get("name") or "角色"
        persona = cls._clean_text(character.get("summary") or character.get("persona") or "")
        if persona:
            return f"我是{name}。{cls._truncate(persona, 38)}"
        return f"我是{name}。你现在可以直接和我说话。"

    @classmethod
    def _event_system_line(cls, event: Dict[str, Any]) -> str:
        hook = cls._event_hook_text(event)
        consequence = cls._clean_text((event.get("consequences") or [""])[0])
        if hook and hook != consequence:
            return cls._truncate(hook, 64)
        if consequence:
            return cls._truncate(consequence, 64)
        return ""

    @classmethod
    def _event_hook_text(cls, event: Dict[str, Any]) -> str:
        candidates = [
            event.get("summary", ""),
            event.get("title", ""),
        ]
        for evidence in event.get("evidence", [])[:2]:
            if isinstance(evidence, dict):
                candidates.append(evidence.get("quote", ""))
        for item in candidates:
            cleaned = cls._clean_text(item)
            if cleaned:
                return cleaned
        return "局势出现了新的变化。"

    @classmethod
    def _pick_event_speaker(cls, story_data: Dict[str, Any], event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        characters = {item["id"]: item for item in story_data.get("characters", [])}
        candidates = [characters[item] for item in event.get("participants", []) if item in characters]
        if not candidates:
            candidates = cls._visible_speakers(story_data)
        ranked = sorted(
            candidates,
            key=lambda item: (
                item.get("importance_score", 0.0),
                item.get("role_type") in {"core", "major", "protagonist"},
            ),
            reverse=True,
        )
        for item in ranked:
            name = cls._clean_text(item.get("canonical_name") or item.get("name") or "")
            if cls._is_valid_speaker(name):
                return item
        fallback_pool = cls._visible_speakers(story_data)
        return fallback_pool[0] if fallback_pool else None

    @classmethod
    def _visible_speakers(cls, story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        characters = sorted(
            story_data.get("characters", []),
            key=lambda item: item.get("importance_score", 0.0),
            reverse=True,
        )
        return [
            item for item in characters
            if cls._is_valid_speaker(cls._clean_text(item.get("canonical_name") or item.get("name") or ""))
        ]

    @classmethod
    def _is_valid_speaker(cls, name: str) -> bool:
        if not name:
            return False
        if name in {"消息", "一秒", "公司", "警方", "监控", "线索", "秘密", "系统", "不要相信", "群像推演", "式剧情游"}:
            return False
        if name.startswith("char_"):
            return False
        if re.search(r"[=《》:：/]", name):
            return False
        return True

    @classmethod
    def _player_feedback_line(cls, result: Dict[str, Any]) -> str:
        intent = result.get("intent")
        data = result.get("data") or {}
        if intent == "intervene_character":
            target = data.get("targets", [None])[0]
            if target:
                return "你决定把注意力转向这个人。"
            return "你决定介入这条人物线。"
        if intent == "inspect_relationships":
            return "你重新梳理了人物之间的站位。"
        if intent == "inspect_clues":
            return "你把目前掌握的线索重新摆到台面上。"
        if intent == "inspect_storyline":
            return "你重新确认了当前的主线脉络。"
        if intent == "advance_story":
            return "你决定继续往前走。"
        if intent == "freeform":
            return "世界记住了你的这句话。"
        return "你的干预已经生效。"

    @classmethod
    def _choice_feedback_line(cls, selected: Dict[str, Any]) -> str:
        label = cls._clean_text(selected.get("label", ""))
        if label:
            return f"你决定：{label}。"
        return "你的决定已经生效。"

    @classmethod
    def _current_scene_label(cls, story_data: Dict[str, Any]) -> str:
        current_scene_id = _world_state_dict(story_data).get("current_scene_id")
        scene = next((item for item in story_data.get("scenes", []) if item["id"] == current_scene_id), None)
        return scene["name"] if scene else "世界入口"

    @classmethod
    def _scene_notice(cls, story_data: Dict[str, Any], scene: Optional[Dict[str, Any]] = None) -> str:
        scene = scene or next(
            (item for item in story_data.get("scenes", []) if item["id"] == _world_state_dict(story_data).get("current_scene_id")),
            None,
        )
        if not scene:
            return "场景切换：世界入口"
        location = cls._clean_text(scene.get("location", ""))
        if location and location != scene.get("name"):
            return f"场景切换：{scene['name']} · {location}"
        return f"场景切换：{scene['name']}"

    @classmethod
    def _truncate(cls, text: str, max_length: int) -> str:
        cleaned = cls._clean_text(text)
        if len(cleaned) <= max_length:
            return cleaned
        return f"{cleaned[:max_length].rstrip('，。、；：,.!?！？ ')}…"

    @classmethod
    def _clean_text(cls, text: Any) -> str:
        if text is None:
            return ""
        value = str(text)
        value = re.sub(r"===\s*[^=\n]+\s*===", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        value = value.strip("“”\"'`")
        return value

    @classmethod
    def _message(
        cls,
        msg_type: str,
        text: str,
        author: str = "",
        character_id: Optional[str] = None,
        delay_ms: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "id": f"msg_{uuid.uuid4().hex[:10]}",
            "type": msg_type,
            "text": text,
            "timestamp": _now_iso(),
            "author": author,
            "character_id": character_id,
            "delay_ms": delay_ms,
            "metadata": metadata or {},
        }


class MainPlotNodeManager:
    @classmethod
    def build_plot_node(cls, story_data: Dict[str, Any], event: Dict[str, Any]) -> PlotNode:
        characters = story_data.get("characters", [])
        clues = story_data.get("clues", [])
        primary_character = next((item for item in characters if item["id"] in event.get("participants", [])), None)
        primary_clue = next((item for item in clues if item["id"] in event.get("clues", [])), None)

        trust_label = f"相信{primary_character.get('canonical_name') or primary_character.get('name')}" if primary_character else "相信当前线索"
        investigate_label = f"深挖 {primary_clue['title']}" if primary_clue else "继续深挖"
        return PlotNode(
            id=f"plot_{event['id']}",
            title=NarrativeEventAdapter._event_system_line(event) or NarrativeEventAdapter._clean_text(event["title"]),
            summary=NarrativeEventAdapter._clean_text(event.get("summary", "")),
            event_id=event["id"],
            required=True,
            prompt="你要怎么做？",
            options=[
                DecisionOption(id=f"{event['id']}_trust", label=trust_label, impact="你决定先跟着这条线走。", target_event_id=event["id"]),
                DecisionOption(id=f"{event['id']}_investigate", label=investigate_label, impact="你决定继续往更深处查。", target_event_id=event["id"]),
                DecisionOption(id=f"{event['id']}_hold", label="暂时按兵不动", impact="你决定先观察局势，不急着暴露自己。", target_event_id=event["id"]),
            ],
        )


class PlotDirector:
    PHASE_CADENCE_MS = {
        "setup": 2400,
        "confrontation": 1500,
        "climax": 900,
        "resolution": 2200,
    }
    PHASE_TENSION = {
        "setup": "low",
        "confrontation": "rising",
        "climax": "high",
        "resolution": "falling",
    }

    @classmethod
    def ensure_state(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> Dict[str, Any]:
        world_state = _world_state_dict(story_data)
        phase = world_state.get("phase", "setup")
        director = play_state.setdefault("director", {})
        director["phase"] = phase
        director["tension"] = cls.PHASE_TENSION.get(phase, "low")
        director["cadence_ms"] = cls.PHASE_CADENCE_MS.get(phase, 1800)
        director.setdefault("last_event_id", world_state.get("current_event_id"))
        director.setdefault("last_released_at", "")
        director.setdefault("next_story_beat_at", _now_iso())
        director["queue_depth"] = len(play_state.get("pending_messages", []))
        return director

    @classmethod
    def queue_messages(
        cls,
        play_state: Dict[str, Any],
        story_data: Dict[str, Any],
        messages: List[Dict[str, Any]],
        immediate: bool = False,
    ) -> List[Dict[str, Any]]:
        if not messages:
            return []
        director = cls.ensure_state(play_state, story_data)
        pending = play_state.setdefault("pending_messages", [])
        base_time = _now_dt()
        queued_times = [_parse_iso(item.get("available_at")) for item in pending]
        queued_times = [item for item in queued_times if item]
        if queued_times:
            base_time = max(base_time, max(queued_times))

        min_gap = max(int(director["cadence_ms"] * 0.55), 360)
        cursor = base_time
        scheduled = []
        for index, message in enumerate(messages):
            payload = dict(message)
            requested_delay = int(payload.get("delay_ms", 0) or 0)
            if index == 0 and immediate and not pending:
                available_at = cursor
            else:
                step_ms = requested_delay or min_gap
                if index > 0:
                    step_ms = max(step_ms, min_gap)
                cursor = cursor + timedelta(milliseconds=step_ms)
                available_at = cursor
            payload["available_at"] = available_at.isoformat()
            scheduled.append(payload)
            pending.append(payload)
        pending.sort(key=lambda item: item.get("available_at", ""))
        director["queue_depth"] = len(pending)
        return scheduled

    @classmethod
    def release_due_messages(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        director = cls.ensure_state(play_state, story_data)
        pending = play_state.setdefault("pending_messages", [])
        if not pending:
            return []

        now = _now_dt()
        ready = []
        future = []
        for message in pending:
            available_at = _parse_iso(message.get("available_at")) or now
            if available_at <= now:
                ready.append(message)
            else:
                future.append(message)

        if not ready:
            return []

        burst_limit = 2 if director.get("tension") in {"high", "rising"} else 1
        selected = ready[:burst_limit]
        play_state["pending_messages"] = ready[burst_limit:] + future
        director["queue_depth"] = len(play_state["pending_messages"])
        director["last_released_at"] = now.isoformat()
        play_state["current_scene_id"] = _world_state_dict(story_data).get("current_scene_id")

        released = []
        for message in selected:
            payload = {k: v for k, v in message.items() if k != "available_at"}
            released.append(payload)
            play_state.setdefault("feed", []).append(payload)
        return released

    @classmethod
    def should_advance_story(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> bool:
        if play_state.get("pending_messages") or play_state.get("current_decision"):
            return False
        director = cls.ensure_state(play_state, story_data)
        next_story_beat_at = _parse_iso(director.get("next_story_beat_at"))
        return not next_story_beat_at or next_story_beat_at <= _now_dt()

    @classmethod
    def after_event_scheduled(cls, play_state: Dict[str, Any], story_data: Dict[str, Any], event: Dict[str, Any]):
        director = cls.ensure_state(play_state, story_data)
        beat_span_ms = max(director["cadence_ms"], 800)
        if event.get("is_key_node"):
            beat_span_ms += 800
        director["last_event_id"] = event["id"]
        director["next_story_beat_at"] = (_now_dt() + timedelta(milliseconds=beat_span_ms)).isoformat()
        director["queue_depth"] = len(play_state.get("pending_messages", []))

    @classmethod
    def nudge_after_player_action(cls, play_state: Dict[str, Any], story_data: Dict[str, Any], fast: bool = False):
        director = cls.ensure_state(play_state, story_data)
        offset = 700 if fast else max(int(director["cadence_ms"] * 0.75), 900)
        director["next_story_beat_at"] = (_now_dt() + timedelta(milliseconds=offset)).isoformat()


class ChatDrivenPlayRuntimeService:
    @classmethod
    def ensure_play_state(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        play_state = story_data.setdefault("play_state", {})
        play_state.setdefault("session_started", False)
        play_state.setdefault("auto_mode", True)
        play_state.setdefault("active_plot_node_id", None)
        play_state.setdefault("current_scene_id", _world_state_dict(story_data).get("current_scene_id"))
        play_state.setdefault("pending_messages", [])
        play_state.setdefault("feed", [])
        play_state.setdefault("current_decision", None)
        play_state.setdefault("unlocked_tasks", [])
        play_state.setdefault("last_tick_at", _now_iso())
        play_state["feed"] = NarrativeEventAdapter.sanitize_visible_messages(play_state.get("feed", []))
        play_state["pending_messages"] = NarrativeEventAdapter.sanitize_visible_messages(play_state.get("pending_messages", []))
        decision = play_state.get("current_decision") or None
        if decision:
            decision["title"] = NarrativeEventAdapter._clean_text(decision.get("title", "")) or "关键节点"
            decision["summary"] = NarrativeEventAdapter._clean_text(decision.get("summary", ""))
            decision["prompt"] = "你要怎么做？"
        PlotDirector.ensure_state(play_state, story_data)
        return play_state

    @classmethod
    def snapshot(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        return {
            "play_state": play_state,
            "world_state": _world_state_dict(story_data),
            "director": play_state.get("director", {}),
            "cursor": len(play_state.get("feed", [])),
        }

    @classmethod
    def start_session(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        if not play_state["session_started"]:
            play_state["session_started"] = True
            PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.intro_messages(story_data), immediate=True)
        return play_state

    @classmethod
    def tick(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        if not play_state["session_started"]:
            cls.start_session(story_data)

        released = PlotDirector.release_due_messages(play_state, story_data)
        if not released and PlotDirector.should_advance_story(play_state, story_data):
            world_state = WorldState(**_world_state_dict(story_data))
            next_event = NarrativePlanner.choose_next_event(story_data, world_state)
            if next_event:
                result = WorldStateEngine.apply_event(story_data, world_state, next_event["id"])
                story_data["world_state"] = result["world_state"].__dict__
                PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.event_messages(story_data, next_event), immediate=True)
                if next_event.get("is_key_node") or "main" in next_event.get("tags", []):
                    plot_node = MainPlotNodeManager.build_plot_node(story_data, next_event)
                    play_state["current_decision"] = asdict(plot_node)
                    play_state["active_plot_node_id"] = plot_node.id
                    PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.decision_prompt_messages(plot_node))
                PlotDirector.after_event_scheduled(play_state, story_data, next_event)
                PlotDirector.release_due_messages(play_state, story_data)

        play_state["last_tick_at"] = _now_iso()
        return play_state

    @classmethod
    def submit_player_input(cls, story_data: Dict[str, Any], player_input: str) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        world_state = WorldState(**_world_state_dict(story_data))
        result = PlayerInteractionService.execute(story_data, world_state, player_input)
        story_data["world_state"] = world_state.__dict__
        NarrativeGraphService.apply_runtime_update(story_data, player_action=player_input)
        play_state["feed"].append(
            NarrativeEventAdapter._message(
                "player",
                player_input,
                author="你",
                metadata={"kind": "player_input"},
            )
        )
        PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(result), immediate=False)
        PlotDirector.nudge_after_player_action(play_state, story_data)
        return result

    @classmethod
    def submit_choice(cls, story_data: Dict[str, Any], option_id: str) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        decision = play_state.get("current_decision") or {}
        selected = next((item for item in decision.get("options", []) if item["id"] == option_id), None)
        if not selected:
            return {"success": False, "message": "无效选项。"}

        play_state["feed"].append(
            NarrativeEventAdapter._message(
                "player",
                f"你选择：{selected['label']}",
                author="你",
                metadata={"kind": "choice", "option_id": option_id},
            )
        )
        PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.choice_feedback_messages(selected))
        play_state["current_decision"] = None
        play_state["active_plot_node_id"] = None
        PlotDirector.nudge_after_player_action(play_state, story_data, fast=True)
        return {"success": True, "choice": selected}
