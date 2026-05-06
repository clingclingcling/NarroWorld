"""
第一人称主角视角的剧情运行时
"""

import re
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..models.story import DecisionOption, PlotNode
from .story_graph import NarrativeGraphService
from .world_state import CharacterRegistry, ContinuationEngine, NarrativePlanner, PlayEventQueue, PlayerInteractionService, WorldState, WorldStateEngine
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger


logger = get_logger("narraworld.story_play")


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


def _event_debug_label(event: Optional[Dict[str, Any]]) -> str:
    if not event:
        return "unknown-event"
    title = NarrativeEventAdapter._clean_text((event or {}).get("title", "")) if "NarrativeEventAdapter" in globals() else str((event or {}).get("title", "") or "")
    if not title:
        title = str((event or {}).get("id", "unknown-event"))
    return f"{event.get('id', 'unknown')}<{title}>"


class ProtagonistResolver:
    PREFERRED_NAMES = ("滑膛",)

    @classmethod
    def resolve(cls, story_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidates = [
            item
            for item_id in CharacterRegistry.playable_ids(story_data)
            for item in [CharacterRegistry.get_character(story_data, item_id, require_playable=True)]
            if item and NarrativeEventAdapter._is_valid_speaker(
                NarrativeEventAdapter._clean_text(item.get("canonical_name") or item.get("name") or "")
            )
        ]
        preferred_named = [
            item
            for item in candidates
            if NarrativeEventAdapter._clean_text(item.get("canonical_name") or item.get("name") or "") in cls.PREFERRED_NAMES
        ]
        if preferred_named:
            preferred_named.sort(key=cls._score, reverse=True)
            return preferred_named[0]
        preferred_id = story_data.get("protagonist_id") or ((_world_state_dict(story_data).get("player_state") or {}).get("protagonist_id"))
        preferred = next((item for item in candidates if item.get("id") == preferred_id), None) if preferred_id else None
        ranked = sorted(
            candidates,
            key=cls._score,
            reverse=True,
        )
        best = ranked[0] if ranked else None
        if preferred and best:
            if cls._score(preferred) >= cls._score(best):
                return preferred
        return best or preferred

    @classmethod
    def _score(cls, item: Dict[str, Any]) -> tuple:
        name = NarrativeEventAdapter._clean_text(item.get("canonical_name") or item.get("name") or "")
        role_type = item.get("role_type", "")
        return (
            any(name == preferred for preferred in cls.PREFERRED_NAMES),
            role_type == "protagonist",
            role_type in {"core", "major"},
            float(item.get("importance_score", 0.0)),
        )


class SpeakingEligibility:
    @classmethod
    def select_intro(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        scene: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        return cls.select_for_turn(
            story_data,
            protagonist,
            None,
            None,
            scene,
            candidates,
            limit=1,
        )

    @classmethod
    def select_for_turn(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
        scene: Optional[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        limit: int = 2,
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        world_state = _world_state_dict(story_data)
        event = event or {}
        beat = beat or {}
        scene = scene or {}
        protagonist_id = (protagonist or {}).get("id")
        player_targets = set(((world_state.get("player_state") or {}).get("targets") or []))
        clue_ids = set((event.get("clues") or []) + (beat.get("revealed_clue_ids") or []))
        clue_holders = set()
        for clue in story_data.get("clues", []):
            if clue.get("id") in clue_ids:
                clue_holders.update(clue.get("holders") or [])
        candidate_map = {item.get("id"): item for item in candidates if item.get("id")}
        supplemental_ids = set(clue_holders) | player_targets
        supplemental_ids.update(
            char_id
            for char_id, state in (world_state.get("character_states") or {}).items()
            if (state or {}).get("status") == "engaged"
        )
        for character_id in supplemental_ids:
            if character_id in candidate_map:
                continue
            character = CharacterRegistry.get_character(story_data, character_id, require_speaking=True)
            if character:
                candidate_map[character_id] = character

        ranked = []
        for character in candidate_map.values():
            character_id = character.get("id")
            if not CharacterRegistry.get_character(story_data, character_id, require_speaking=True):
                continue
            reasons = []
            score = 0.0
            if character_id in set(scene.get("participants") or []) or character_id in set(beat.get("present_character_ids") or []):
                reasons.append("in_scene")
                score += 3.0
            if character_id in set(event.get("participants") or []) or character_id in {event.get("actor"), event.get("target")}:
                reasons.append("event_actor_or_target")
                score += 3.2
            if character_id in clue_holders:
                reasons.append("holds_current_clue")
                score += 2.8
            if character_id in player_targets or float((world_state.get("relationship_tension") or {}).get(f"player:{character_id}", 0.0) or 0.0) > 0:
                reasons.append("conflicts_with_player")
                score += 2.5
            if ((world_state.get("character_states") or {}).get(character_id, {}) or {}).get("status") == "engaged":
                reasons.append("attitude_shift")
                score += 1.8
            if beat.get("importance") in {"major", "minor"} and character.get("role_type") in {"protagonist", "core", "supporting", "hidden"}:
                reasons.append("can_push_decision")
                score += 1.5
            if protagonist_id and character_id == protagonist_id:
                continue
            if not reasons:
                continue
            score += float(character.get("importance_score", 0.0) or 0.0)
            ranked.append((score, reasons, character))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[2].get("role_type") in {"protagonist", "core"},
                float(item[2].get("importance_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return [item[2] for item in ranked[:limit]]


class TurnQualityGate:
    @classmethod
    def evaluate(
        cls,
        story_data: Dict[str, Any],
        event: Optional[Dict[str, Any]],
        turn: Dict[str, Any],
        beat: Optional[Dict[str, Any]],
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = event or {}
        beat = beat or {}
        previous_turn = previous_turn or {}
        clue_ids = list(dict.fromkeys((beat.get("revealed_clue_ids") or []) + (event.get("clues") or [])))
        consequence_text = NarrativeEventAdapter._clean_text((event.get("consequences") or [""])[0])
        new_information = cls._has_playable_new_information(event, clue_ids, consequence_text)
        relationship_change = cls._relationship_signal(story_data, event, turn)
        risk_change = cls._text_changed(turn.get("risk"), previous_turn.get("risk"))
        objective_change = cls._text_changed(turn.get("objective"), previous_turn.get("objective"))
        meaningful_actions = cls._has_meaningful_actions(turn.get("actions") or [])
        main_conflict = bool(
            beat.get("importance") == "major"
            or event.get("is_key_node")
            or "main" in (event.get("tags") or [])
        )
        clue_or_secret = bool(
            clue_ids
            or any(
                keyword in NarrativeEventAdapter._clean_text(text)
                for text in (event.get("consequences") or [])
                for keyword in ("秘密", "暴露", "线索")
            )
        )
        signals = {
            "new_information": new_information,
            "relationship_change": relationship_change,
            "risk_change": risk_change,
            "objective_change": objective_change,
            "meaningful_actions": meaningful_actions,
            "main_conflict": main_conflict,
            "clue_or_secret": clue_or_secret,
        }
        importance = beat.get("importance") or ("major" if event.get("is_key_node") else event.get("event_type") or "minor")
        allow_full_turn = cls._allow_full_turn(turn, signals, importance)
        compression_mode = "full"
        if not allow_full_turn:
            if importance == "background":
                compression_mode = "background"
            elif importance == "transition" or event.get("event_type") == "transition":
                compression_mode = "transition"
            else:
                compression_mode = "minor"
        return {
            "allow_full_turn": allow_full_turn,
            "compression_mode": compression_mode,
            "signals": signals,
            "importance": importance,
            "reasons": cls._reasons(signals),
        }

    @classmethod
    def _has_playable_new_information(
        cls,
        event: Dict[str, Any],
        clue_ids: List[str],
        consequence_text: str,
    ) -> bool:
        if clue_ids:
            return True
        if any(
            keyword in consequence_text
            for keyword in ("得知", "意识到", "证实", "原来", "其实", "暴露", "线索", "秘密")
        ) and not NarrativeEventAdapter._is_generic_narrative_fragment(consequence_text):
            return True
        for evidence in (event.get("evidence") or [])[:3]:
            quote = NarrativeEventAdapter._clean_text((evidence or {}).get("quote", ""))
            if not quote:
                continue
            if CharacterDialogueDirector._looks_like_exposition_fragment(quote):
                continue
            if NarrativeEventAdapter._is_generic_narrative_fragment(quote):
                continue
            return True
        return False

    @classmethod
    def _allow_full_turn(cls, turn: Dict[str, Any], signals: Dict[str, bool], importance: str) -> bool:
        if not bool(turn.get("should_render_full_turn", True)):
            return False
        if importance == "background":
            return False
        hard_signals = sum(
            1 for key in ("new_information", "relationship_change", "main_conflict", "clue_or_secret")
            if signals.get(key)
        )
        soft_signals = sum(
            1 for key in ("risk_change", "objective_change", "meaningful_actions")
            if signals.get(key)
        )
        if importance == "major":
            return hard_signals >= 1 or soft_signals >= 2
        if importance == "transition":
            return signals.get("main_conflict") or signals.get("relationship_change") or (
                signals.get("new_information") and signals.get("meaningful_actions")
            )
        return hard_signals >= 1 and (
            signals.get("meaningful_actions")
            or signals.get("clue_or_secret")
            or signals.get("relationship_change")
            or signals.get("new_information")
        )

    @classmethod
    def _reasons(cls, signals: Dict[str, bool]) -> List[str]:
        labels = {
            "new_information": "new_information",
            "relationship_change": "relationship_change",
            "risk_change": "risk_change",
            "objective_change": "objective_change",
            "meaningful_actions": "meaningful_actions",
            "main_conflict": "main_conflict",
            "clue_or_secret": "clue_or_secret",
        }
        return [labels[key] for key, value in signals.items() if value]

    @classmethod
    def _text_changed(cls, current: Any, previous: Any) -> bool:
        current_text = NarrativeEventAdapter._clean_text(current)
        previous_text = NarrativeEventAdapter._clean_text(previous)
        if not current_text:
            return False
        if not previous_text:
            return True
        return current_text != previous_text

    @classmethod
    def _has_meaningful_actions(cls, actions: List[Dict[str, Any]]) -> bool:
        if not actions:
            return False
        concrete = [
            item for item in actions
            if (item.get("action_type") or "") not in {"observe", "reposition"}
        ]
        return len(actions) >= 2 and bool(concrete or len(actions) >= 3)

    @classmethod
    def _relationship_signal(cls, story_data: Dict[str, Any], event: Dict[str, Any], turn: Dict[str, Any]) -> bool:
        protagonist = ProtagonistResolver.resolve(story_data)
        protagonist_id = (protagonist or {}).get("id")
        if not protagonist_id:
            return False
        participant_ids = {
            item.get("id")
            for item in (turn.get("present_characters") or [])
            if isinstance(item, dict) and item.get("id")
        }
        participant_ids.update(event.get("participants") or [])
        world_state = _world_state_dict(story_data)
        player_targets = set(((world_state.get("player_state") or {}).get("targets") or []))
        if player_targets.intersection(participant_ids):
            return True
        if any(float((world_state.get("relationship_tension") or {}).get(f"player:{item_id}", 0.0) or 0.0) > 0 for item_id in participant_ids):
            return True
        text = " ".join(
            NarrativeEventAdapter._clean_text(item)
            for item in [
                event.get("summary", ""),
                *((event.get("consequences") or [])[:2]),
            ]
            if NarrativeEventAdapter._clean_text(item)
        )
        return any(keyword in text for keyword in ("怀疑", "信任", "站队", "背叛", "防", "态度", "试探"))


class NarrativeCompressor:
    MAX_BATCH = 3

    @classmethod
    def should_compress(cls, turn: Dict[str, Any]) -> bool:
        gate = turn.get("quality_gate") or {}
        return not gate.get("allow_full_turn", True)

    @classmethod
    def consume_progression(
        cls,
        story_data: Dict[str, Any],
        play_state: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        first_event: Dict[str, Any],
        first_turn: Dict[str, Any],
    ) -> Dict[str, Any]:
        world_state = WorldState(**_world_state_dict(story_data))
        batch = [{"event": first_event, "turn": first_turn}]
        compressed_ids = [first_event["id"]]

        while len(batch) < cls.MAX_BATCH:
            next_entry = PlayEventQueue.peek_next_entry(story_data, world_state, play_state)
            if not next_entry:
                break
            next_event = next((item for item in story_data.get("events", []) if item.get("id") == next_entry.get("event_id")), None)
            if not next_event:
                break
            next_beat = NarrativeEventAdapter._resolve_playable_beat(story_data, next_event) or {}
            next_importance = next_beat.get("importance") or ("major" if next_event.get("is_key_node") else next_event.get("event_type") or "minor")
            if next_importance == "major" or next_event.get("is_key_node") or "main" in (next_event.get("tags") or []):
                break
            result = WorldStateEngine.apply_event(story_data, world_state, next_event["id"])
            story_data["world_state"] = result["world_state"].__dict__
            world_state = result["world_state"]
            next_turn = NarrativeEventAdapter.build_turn(story_data, next_event, protagonist)
            batch.append({"event": next_event, "turn": next_turn})
            compressed_ids.append(next_event["id"])
            if (next_turn.get("quality_gate") or {}).get("compression_mode") == "transition":
                break

        cls._mark_compressed_entries(play_state, compressed_ids)
        world_state_payload = _world_state_dict(story_data)
        world_state_payload["candidate_event_ids"] = PlayEventQueue.derive_candidate_event_ids(play_state.get("event_queue", []))
        story_data["world_state"] = world_state_payload
        visible_items = [item for item in batch if (item["turn"].get("quality_gate") or {}).get("compression_mode") != "background"]
        if not visible_items:
            logger.info(
                "Story turn compressed to background only: %s",
                " -> ".join(_event_debug_label(item["event"]) for item in batch),
            )
            return {"messages": [], "turn": None, "event_ids": compressed_ids}

        compressed_turn = cls._compressed_turn(visible_items, protagonist)
        logger.info(
            "Story turn compressed: mode=%s events=%s",
            compressed_turn.get("compression_mode", "minor"),
            " -> ".join(_event_debug_label(item["event"]) for item in visible_items),
        )
        return {
            "messages": cls._compressed_messages(compressed_turn, story_data),
            "turn": compressed_turn,
            "event_ids": compressed_ids,
        }

    @classmethod
    def _mark_compressed_entries(cls, play_state: Dict[str, Any], event_ids: List[str]) -> None:
        event_set = set(event_ids)
        for entry in play_state.get("event_queue", []):
            if entry.get("event_id") in event_set:
                entry["status"] = "compressed"
                entry["turn_generated"] = True
                detail = NarrativeEventAdapter._clean_text(entry.get("debug_reason", ""))
                entry["debug_reason"] = "；".join(filter(None, [detail, "低价值事件已压缩"]))

    @classmethod
    def _compressed_turn(
        cls,
        batch: List[Dict[str, Any]],
        protagonist: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        turns = [item["turn"] for item in batch]
        events = [item["event"] for item in batch]
        first_turn = turns[0]
        last_turn = turns[-1]
        mode = (first_turn.get("quality_gate") or {}).get("compression_mode", "minor")
        objective = next((NarrativeEventAdapter._clean_text(turn.get("objective")) for turn in reversed(turns) if NarrativeEventAdapter._clean_text(turn.get("objective"))), "")
        risk = next((NarrativeEventAdapter._clean_text(turn.get("risk")) for turn in reversed(turns) if NarrativeEventAdapter._clean_text(turn.get("risk"))), "")
        revealed_clue_id = next(
            (
                clue_id
                for turn in turns
                for clue_id in ((turn.get("revealed_clue_ids") or []) if isinstance(turn.get("revealed_clue_ids"), list) else [])
                if clue_id
            ),
            None,
        )
        return {
            "id": f"turn_compressed_{events[0]['id']}_{events[-1]['id']}",
            "event_id": events[-1]["id"],
            "beat_id": last_turn.get("beat_id"),
            "block_id": last_turn.get("block_id"),
            "scene_id": last_turn.get("scene_id"),
            "scene_label": last_turn.get("scene_label"),
            "headline": last_turn.get("headline") or "局势继续推进",
            "situation": cls._summary_text(batch, protagonist, mode),
            "objective": NarrativeEventAdapter._personalize_protagonist_text(objective, protagonist),
            "risk": NarrativeEventAdapter._personalize_protagonist_text(risk, protagonist),
            "pressure": last_turn.get("pressure", "局势正在变化"),
            "present_characters": [],
            "dialogues": [],
            "actions": [],
            "dramatic_question": "",
            "importance": mode,
            "should_render_full_turn": False,
            "state_summary": last_turn.get("state_summary", {}),
            "mode": "first_person",
            "source_unit": "compressed_progression",
            "compression_mode": mode,
            "compressed_event_ids": [event["id"] for event in events],
            "revealed_clue_id": revealed_clue_id,
            "quality_gate": {
                "allow_full_turn": False,
                "compression_mode": mode,
                "signals": {
                    "new_information": bool(revealed_clue_id),
                    "relationship_change": False,
                    "risk_change": False,
                    "objective_change": False,
                    "meaningful_actions": False,
                    "main_conflict": False,
                    "clue_or_secret": bool(revealed_clue_id),
                },
            },
        }

    @classmethod
    def _summary_text(cls, batch: List[Dict[str, Any]], protagonist: Optional[Dict[str, Any]], mode: str) -> str:
        snippets = []
        for item in batch:
            text = NarrativeEventAdapter._clean_visible_system_text(
                item["turn"].get("situation", ""),
                NarrativeEventAdapter._system_message_kind(item["turn"], default="background"),
            )
            if text and text not in snippets:
                snippets.append(NarrativeEventAdapter._truncate(text, 56))
        joined = " ".join(snippets[:2])
        joined = NarrativeEventAdapter._personalize_protagonist_text(joined, protagonist)
        if joined:
            return joined
        if mode == "transition":
            return "你没有停下，场面也没有给你喘息的空隙。位置、视线和话头都在无声地换位。"
        return "你没有额外出手，但局面已经往前滑了一段。最要紧的不是谁说了什么，而是谁在这几步里悄悄换了位置。"

    @classmethod
    def _compressed_messages(cls, turn: Dict[str, Any], story_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not turn:
            return []
        messages = []
        scene_label = NarrativeEventAdapter._clean_text(turn.get("scene_label", ""))
        if (
            scene_label
            and turn.get("compression_mode") == "transition"
            and not scene_label.startswith("过场")
        ):
            messages.append(
                NarrativeEventAdapter._message(
                    "scene",
                    f"过场：{scene_label}",
                    metadata={"kind": "scene_transition", "layer": "system"},
                )
            )
        messages.append(
            NarrativeEventAdapter._message(
                "system",
                turn.get("situation", ""),
                metadata={"kind": NarrativeEventAdapter._system_message_kind(turn, default="background"), "layer": "system"},
            )
        )
        clue_id = turn.get("revealed_clue_id") or ((turn.get("revealed_clue_ids") or [None])[0])
        if clue_id:
            messages.append(
                NarrativeEventAdapter._message(
                    "clue",
                    f"你抓到了新线索：{clue_id}。",
                    delay_ms=320,
                    metadata={"kind": "clue_unlock", "layer": "system"},
                )
            )
        return NarrativeEventAdapter.sanitize_visible_messages(messages, story_data)


class NarrativeEventAdapter:
    VISIBLE_TYPES = {"system", "character", "player", "feedback", "clue", "scene"}
    INTERNAL_PATTERNS = [
        "正在围绕",
        "试图掌握局势",
        "回应《",
        "世界脉络正在接入",
        "主线提示：",
        "剧情推进：",
        "已进入你的联络视野",
        "响应事件《",
    ]

    PHASE_LABELS = {
        "setup": "局势刚刚拉开",
        "confrontation": "各方开始相互试探",
        "climax": "冲突正在逼近失控",
        "resolution": "局势开始朝某个结果收束",
    }
    NOISY_SPEAKERS = {
        "消息", "一秒", "公司", "警方", "监控", "线索", "秘密", "系统", "不要相信",
        "群像推演", "式剧情游", "什么", "这样", "这时", "时间", "家里", "路边",
        "于是", "花纹", "高等级", "周围", "蓝光", "陶罐", "平静",
    }

    @classmethod
    def intro_messages(cls, story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        protagonist = ProtagonistResolver.resolve(story_data)
        protagonist_name = (protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你"
        title = story_data.get("title", "NarraWorld")
        world_state = _world_state_dict(story_data)
        scene = cls._resolve_scene(story_data, world_state.get("current_scene_id"))
        messages = []
        scene_label = cls._scene_label(scene)
        if scene_label:
            messages.append(
                cls._message(
                    "scene",
                    f"场景：{scene_label}",
                    metadata={"kind": "scene_intro", "layer": "system"},
                )
            )
        messages.append(
            cls._message(
                "system",
                f"你现在以{protagonist_name}的身份进入《{title}》。",
                metadata={"kind": "world_intro", "layer": "system"},
            )
        )
        return messages

    @classmethod
    def event_messages(
        cls,
        story_data: Dict[str, Any],
        event: Dict[str, Any],
        turn: Optional[Dict[str, Any]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        protagonist = ProtagonistResolver.resolve(story_data)
        turn = turn or cls.build_turn(story_data, event, protagonist)
        if NarrativeCompressor.should_compress(turn):
            return NarrativeCompressor._compressed_messages(turn, story_data)
        previous_turn = previous_turn if previous_turn is not None else ((story_data.get("play_state") or {}).get("current_turn") or {})
        messages = []
        if (
            turn.get("scene_label")
            and turn.get("scene_label") != previous_turn.get("scene_label")
            and turn.get("should_render_full_turn", True)
        ):
            messages.append(
                cls._message(
                    "scene",
                    f"场景：{turn['scene_label']}",
                    metadata={"event_id": event["id"], "kind": "scene_change", "layer": "system"},
                )
            )
        if turn.get("situation") and turn.get("situation") != previous_turn.get("situation"):
            messages.append(
                cls._message(
                    "system",
                    turn["situation"],
                    metadata={"event_id": event["id"], "kind": cls._system_message_kind(turn), "layer": "system"},
                )
            )
        if turn.get("supplemental_hint") and turn.get("supplemental_hint") != previous_turn.get("supplemental_hint"):
            messages.append(
                cls._message(
                    "system",
                    turn["supplemental_hint"],
                    delay_ms=420,
                    metadata={"event_id": event["id"], "kind": "background", "layer": "system"},
                )
            )
        if cls._should_emit_character_dialogues(turn):
            for dialogue in turn["dialogues"][:1]:
                messages.append(
                    cls._message(
                        "character",
                        dialogue["text"],
                        author=dialogue["speaker"],
                        character_id=dialogue.get("character_id"),
                        delay_ms=900,
                        metadata={"event_id": event["id"], "kind": "character_update", "layer": "character"},
                    )
                )
        return cls.sanitize_visible_messages(messages, story_data)

    @classmethod
    def _should_emit_character_dialogues(cls, turn: Dict[str, Any]) -> bool:
        if not turn.get("dialogues"):
            return False
        if not turn.get("should_render_full_turn", True):
            return False
        gate = turn.get("quality_gate") or {}
        signals = gate.get("signals") or {}
        importance = gate.get("importance") or turn.get("importance", "minor")
        if importance == "major":
            return True
        return bool(
            signals.get("relationship_change")
            or signals.get("main_conflict")
            or (signals.get("new_information") and signals.get("meaningful_actions"))
        )

    @classmethod
    def player_feedback_messages(cls, feedback: Dict[str, Any]) -> List[Dict[str, Any]]:
        summary = cls._clean_text(feedback.get("summary", "你的动作已经改变了局势。"))
        if not summary:
            return []
        return [
            cls._message(
                "feedback",
                summary,
                delay_ms=420,
                metadata={"kind": "player_feedback", "layer": "system"},
            )
        ]

    @classmethod
    def sanitize_visible_messages(cls, messages: List[Dict[str, Any]], story_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        cleaned = []
        seen = set()
        last_scene_text = ""
        generic_system_count = 0
        transition_scene_seen = False
        for message in messages:
            normalized = cls._sanitize_message(message, story_data)
            if not normalized:
                continue
            msg_type = normalized.get("type")
            text = normalized.get("text", "")
            if msg_type == "scene":
                kind = (normalized.get("metadata") or {}).get("kind")
                if kind == "scene_transition":
                    if transition_scene_seen:
                        continue
                    transition_scene_seen = True
                if text == last_scene_text:
                    continue
                last_scene_text = text
            if msg_type == "system" and cls._is_generic_repeated_system_text(text):
                generic_system_count += 1
                if generic_system_count > 1:
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
    def build_intro_turn(cls, story_data: Dict[str, Any], protagonist: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        world_state = _world_state_dict(story_data)
        previous_turn = ((story_data.get("play_state") or {}).get("current_turn") or {})
        beat = cls._resolve_playable_beat(story_data, None)
        scene = cls._resolve_scene(story_data, (beat or {}).get("scene_id") or world_state.get("current_scene_id"))
        protagonist_name = (protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你"
        block = None
        if not beat:
            block = cls._resolve_narrative_block(story_data, None)
            if block and cls._block_needs_refresh(block):
                block = None
        participant_candidates = cls._speaker_candidates_from_ids(story_data, protagonist, (beat or {}).get("present_character_ids", []))
        if not participant_candidates:
            participant_candidates = cls._speaker_candidates(story_data, protagonist, scene, None)
        speakers = SpeakingEligibility.select_intro(story_data, protagonist, scene, participant_candidates)
        dialogues = cls._build_intro_dialogues(story_data, protagonist, speakers)
        stage_characters = cls._stage_characters_from_speakers(story_data, protagonist, speakers, dialogues)
        should_render_full_turn = (beat or {}).get("should_render_full_turn", True)
        actions = [asdict(option) for option in ActionDirector.default_actions(story_data, protagonist, beat=beat, block=block)] if should_render_full_turn else []
        objective = cls._personalize_protagonist_text((beat or {}).get("player_objective") or (block or {}).get("objective"), protagonist)
        risk = cls._personalize_protagonist_text((beat or {}).get("risk_summary") or (block or {}).get("risk"), protagonist)
        situation = (
            cls._personalize_protagonist_text((beat or {}).get("first_person_situation"), protagonist)
            or cls._compose_intro_narration(story_data, protagonist, scene, block, dialogues)
        )
        supplemental_hint = cls._supplemental_system_hint(story_data, protagonist, None, block, beat, stage_characters)
        turn = {
            "id": f"turn_intro_{uuid.uuid4().hex[:8]}",
            "event_id": None,
            "beat_id": (beat or {}).get("beat_id"),
            "block_id": (block or {}).get("id"),
            "scene_id": scene.get("id"),
            "scene_label": cls._scene_label(scene),
            "headline": f"你是{protagonist_name}。",
            "situation": situation,
            "objective": objective or "先弄清楚眼前这场会面到底是谁在布置，谁在试着把你往某个方向推。",
            "risk": risk or "你一旦在开场就把自己的判断露得太多，后面每个人都会顺着这个破绽来摸你。",
            "supplemental_hint": supplemental_hint,
            "pressure": cls.PHASE_LABELS.get(world_state.get("phase", "setup"), "局势正在变化"),
            "present_characters": stage_characters,
            "dialogues": dialogues,
            "actions": actions,
            "dramatic_question": cls._personalize_protagonist_text((beat or {}).get("dramatic_question"), protagonist),
            "importance": (beat or {}).get("importance", "major"),
            "should_render_full_turn": should_render_full_turn,
            "state_summary": cls._state_summary(story_data, world_state),
            "mode": "first_person",
            "source_unit": "playable_beat" if beat else "narrative_block",
        }
        gate = TurnQualityGate.evaluate(story_data, None, turn, beat, previous_turn)
        turn["quality_gate"] = gate
        turn["compression_mode"] = gate.get("compression_mode", "full")
        if not gate.get("allow_full_turn", True):
            turn["dialogues"] = []
            turn["actions"] = []
            turn["present_characters"] = []
            turn["should_render_full_turn"] = False
        elif not cls._should_emit_character_dialogues(turn):
            turn["dialogues"] = []
            turn["present_characters"] = []
        return turn

    @classmethod
    def build_turn(cls, story_data: Dict[str, Any], event: Dict[str, Any], protagonist: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        world_state = _world_state_dict(story_data)
        previous_turn = ((story_data.get("play_state") or {}).get("current_turn") or {})
        beat = cls._resolve_playable_beat(story_data, event)
        block = None
        if not beat:
            block = cls._resolve_narrative_block(story_data, event)
            if block and cls._block_needs_refresh(block):
                block = None
        scene = cls._resolve_scene(
            story_data,
            (beat or {}).get("scene_id") or (event.get("scenes") or [world_state.get("current_scene_id")])[0],
        )
        explicit_character_ids = list((beat or {}).get("present_character_ids", [])) + list(event.get("participants", []))
        participant_candidates = cls._speaker_candidates_from_ids(story_data, protagonist, (beat or {}).get("present_character_ids", []))
        if not participant_candidates and cls._allow_speaker_fallback(story_data, protagonist, event, beat, explicit_character_ids):
            participant_candidates = cls._speaker_candidates(story_data, protagonist, scene, event)
        speakers = SpeakingEligibility.select_for_turn(story_data, protagonist, event, beat, scene, participant_candidates, limit=1)
        dialogues = cls._build_dialogues(story_data, protagonist, event, speakers, beat=beat)
        stage_characters = cls._stage_characters_from_speakers(story_data, protagonist, speakers, dialogues)
        context_only = cls._is_context_only_event(story_data, protagonist, event, beat, stage_characters, explicit_character_ids)
        should_render_full_turn = (beat or {}).get("should_render_full_turn", True) and not context_only
        actions = (
            [asdict(option) for option in ActionDirector.build_actions(story_data, event, protagonist, stage_characters, beat=beat, block=block, scene=scene)]
            if should_render_full_turn else []
        )
        clue_hint = cls._event_clue_hint(story_data, event)
        objective = (beat or {}).get("player_objective") or (block or {}).get("objective") or cls._turn_objective(event, stage_characters, clue_hint)
        risk = (beat or {}).get("risk_summary") or (block or {}).get("risk") or cls._turn_risk(story_data, event, stage_characters)
        fact_anchors = cls._turn_fact_anchors(story_data, event, block, beat)
        context_characters = cls._context_characters(fact_anchors)
        situation = (
            cls._personalize_protagonist_text((beat or {}).get("first_person_situation"), protagonist)
            or cls._compose_turn_narration(story_data, protagonist, scene, event, block, dialogues, stage_characters)
        )
        situation = cls._strengthen_situation_with_facts(situation, fact_anchors)
        if context_only and fact_anchors:
            situation = cls._personalize_protagonist_text(cls._truncate(" ".join(fact_anchors[:2]), 150), protagonist)
        objective = cls._clean_play_objective(objective, fact_anchors, protagonist)
        supplemental_hint = cls._supplemental_system_hint(story_data, protagonist, event, block, beat, stage_characters)
        if not supplemental_hint and fact_anchors:
            supplemental_hint = cls._fact_hint(fact_anchors)
        if context_only:
            supplemental_hint = ""
        else:
            supplemental_hint = cls._clean_visible_system_text(supplemental_hint, "background")
        headline = f"场景：{cls._scene_label(scene)}"

        turn = {
            "id": f"turn_{event['id']}",
            "event_id": event["id"],
            "beat_id": (beat or {}).get("beat_id"),
            "block_id": (block or {}).get("id"),
            "scene_id": scene.get("id"),
            "scene_label": cls._scene_label(scene),
            "headline": headline,
            "situation": situation,
            "objective": cls._personalize_protagonist_text(objective, protagonist),
            "risk": cls._personalize_protagonist_text(risk, protagonist),
            "supplemental_hint": supplemental_hint,
            "fact_anchors": fact_anchors,
            "pressure": cls.PHASE_LABELS.get(world_state.get("phase", "setup"), "局势正在变化"),
            "present_characters": stage_characters,
            "context_characters": context_characters,
            "dialogues": dialogues,
            "actions": actions,
            "dramatic_question": cls._personalize_protagonist_text((beat or {}).get("dramatic_question"), protagonist),
            "importance": (beat or {}).get("importance", "major" if event.get("is_key_node") else "minor"),
            "should_render_full_turn": should_render_full_turn,
            "state_summary": cls._state_summary(story_data, world_state),
            "mode": "first_person",
            "source_unit": "playable_beat" if beat else "event_block_fallback",
            "context_only": context_only,
        }
        system_kind = cls._system_message_kind(turn)
        visible_situation = cls._clean_visible_system_text(turn.get("situation", ""), system_kind)
        if not visible_situation and context_only:
            visible_situation = "一段背景从你脑中掠过，但它暂时没有形成新的行动窗口。"
        if visible_situation:
            turn["situation"] = visible_situation
        gate = TurnQualityGate.evaluate(story_data, event, turn, beat, previous_turn)
        turn["quality_gate"] = gate
        turn["compression_mode"] = gate.get("compression_mode", "full")
        turn["revealed_clue_ids"] = list(dict.fromkeys((beat or {}).get("revealed_clue_ids", []) + (event.get("clues") or [])))[:1]
        if not gate.get("allow_full_turn", True):
            turn["dialogues"] = []
            turn["actions"] = []
            turn["present_characters"] = []
            turn["should_render_full_turn"] = False
        elif not cls._should_emit_character_dialogues(turn):
            turn["dialogues"] = []
            turn["present_characters"] = []
        return turn

    @classmethod
    def _is_context_only_event(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Dict[str, Any],
        beat: Optional[Dict[str, Any]],
        stage_characters: List[Dict[str, Any]],
        explicit_character_ids: List[str],
    ) -> bool:
        if event.get("is_key_node") or "main" in (event.get("tags") or []):
            return False
        if stage_characters:
            return False
        protagonist_id = (protagonist or {}).get("id")
        explicit_ids = [item_id for item_id in explicit_character_ids if item_id]
        if explicit_ids and any(item_id != protagonist_id for item_id in explicit_ids):
            return False
        text_blob = " ".join([
            cls._clean_text(event.get("summary", "")),
            cls._clean_text((event.get("consequences") or [""])[0]),
            cls._clean_text((beat or {}).get("first_person_situation", "")),
        ])
        if explicit_ids and all(item_id == protagonist_id for item_id in explicit_ids):
            if cls._looks_like_raw_fragment(text_blob) or CharacterDialogueDirector._looks_like_exposition_fragment(text_blob):
                return True
        if ActionDirector._is_memory_context(event, beat):
            return True
        return not (event.get("clues") or []) and (beat or {}).get("importance", "minor") != "major"

    @classmethod
    def feedback_payload(
        cls,
        summary: str,
        gains: Optional[List[str]] = None,
        risks: Optional[List[str]] = None,
        relationship_changes: Optional[List[str]] = None,
        next_pressure: str = "",
    ) -> Dict[str, Any]:
        return {
            "summary": cls._clean_text(summary),
            "gains": [cls._clean_text(item) for item in gains or [] if cls._clean_text(item)],
            "risks": [cls._clean_text(item) for item in risks or [] if cls._clean_text(item)],
            "relationship_changes": [cls._clean_text(item) for item in relationship_changes or [] if cls._clean_text(item)],
            "next_pressure": cls._clean_text(next_pressure),
        }

    @classmethod
    def _sanitize_message(cls, message: Dict[str, Any], story_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        payload = dict(message)
        text = cls._clean_text(payload.get("text", ""))
        msg_type = cls._normalize_message_type(payload.get("type", "system"), payload.get("metadata") or {})
        author = payload.get("author", "")
        character_id = payload.get("character_id")

        if msg_type == "decision":
            return None
        if msg_type == "character" and not cls._is_valid_speaker(cls._clean_text(author)):
            return None
        if msg_type == "character" and CharacterDialogueDirector._has_runtime_title_pollution(text):
            return None
        if msg_type == "character" and story_data:
            character = CharacterRegistry.get_character(story_data, character_id, require_speaking=True)
            if not character:
                return None
            payload["author"] = character.get("canonical_name") or character.get("name") or author
            payload["character_id"] = character.get("id")

        if msg_type == "scene":
            text = cls._normalize_scene_message_text(text)
            if not text:
                return None
        if msg_type == "system":
            text = cls._clean_visible_system_text(text, (payload.get("metadata") or {}).get("kind", ""))
            if not text:
                return None
            if cls._is_low_value_system_text(text):
                return None

        if any(pattern in text for pattern in cls.INTERNAL_PATTERNS):
            return None
        if any(keyword in text for keyword in ["推进主线", "延长调查链路", "增强冲突", "降低风险", "错过窗口"]):
            return None
        if not text:
            return None

        payload["text"] = text
        payload["type"] = msg_type
        return payload

    @classmethod
    def _system_message_kind(cls, turn: Dict[str, Any], default: str = "narration") -> str:
        text = " ".join([
            cls._clean_text(turn.get("situation", "")),
            cls._clean_text(turn.get("supplemental_hint", "")),
            " ".join(cls._clean_text(item) for item in (turn.get("fact_anchors") or [])),
        ])
        if any(token in text for token in ("齿哥", "利锯", "老克", "旧记忆", "记忆", "职业圈")):
            return "memory"
        if turn.get("compression_mode") == "transition":
            return "transition"
        if turn.get("context_only") or not turn.get("should_render_full_turn", True):
            return "background"
        return default

    @classmethod
    def _clean_visible_system_text(cls, text: Any, kind: str = "") -> str:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return ""
        normalized_kind = cls._clean_text(kind).lower()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^场景：", "", cleaned).strip() if normalized_kind in {"memory", "background", "transition"} else cleaned
        cleaned = re.sub(
            r"^你站在[^，。]{1,32}[里中]，先感觉到的不是声音，而是所有人都在等你先露判断[。；，、\s]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(
            r"^你站在[^，。]{1,32}[里中]，所有人都在等你先露判断[。；，、\s]*",
            "",
            cleaned,
        ).strip()
        cleaned = re.sub(r"^你站在([^，。]{1,32})[里中]，(?=(老克|齿哥|画家|这之前|虽然|一段旧记忆))", "", cleaned).strip()
        if cleaned.count("“") > cleaned.count("”") and re.search(r"(说|问)[：:]\s*“[^”]{2,}$", cleaned):
            cleaned = re.sub(r"(说|问)[：:]\s*“", r"\1：“", cleaned)
            tail = "？”" if cleaned.rstrip().endswith(("?", "？")) else "。”"
            cleaned = f"{cleaned.rstrip('。！？!? ')}{tail}"
        if cls._looks_like_raw_fragment(cleaned):
            return ""
        if normalized_kind in {"memory", "background", "transition", "compressed_narration", "context_note"}:
            cleaned = cls._remove_duplicate_sentences(cleaned)
            cleaned = cleaned.replace("。 ”", "。”").replace("？ ”", "？”").replace("！ ”", "！”")
        return cleaned.strip(" ，；;")

    @classmethod
    def _looks_like_raw_fragment(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return True
        if cleaned.startswith(("”", "’", "\"", "'")):
            return True
        if re.search(r"^[”\"']?[\u4e00-\u9fff]{1,8}(说|问|表示)[，。]", cleaned):
            return True
        if re.search(r"^[”\"']?你指指那幅画说", cleaned):
            return True
        if cleaned.count("“") != cleaned.count("”"):
            return True
        if cleaned.startswith("[") or cleaned.endswith("]"):
            return True
        return False

    @classmethod
    def _remove_duplicate_sentences(cls, text: str) -> str:
        parts = [item.strip() for item in re.split(r"(?<=[。！？!?])\s*", cls._clean_text(text)) if item.strip()]
        if not parts:
            return cls._clean_text(text)
        seen = set()
        kept = []
        for part in parts:
            key = part.rstrip("。！？!?")
            if key in seen:
                continue
            seen.add(key)
            kept.append(part)
        return " ".join(kept)

    @classmethod
    def _normalize_message_type(cls, msg_type: str, metadata: Dict[str, Any]) -> str:
        kind = cls._clean_text(metadata.get("kind", "")).lower()
        normalized = cls._clean_text(msg_type).lower() or "system"
        if kind in {"player_feedback", "feedback"} or normalized == "feedback":
            return "feedback"
        if kind in {"scene_change", "scene_intro", "scene"} or normalized == "scene":
            return "scene"
        if kind in {"clue_unlock", "clue"} or normalized == "clue":
            return "clue"
        if normalized == "character":
            return "character"
        if normalized == "player":
            return "player"
        return normalized if normalized in cls.VISIBLE_TYPES else "system"

    @classmethod
    def _normalize_scene_message_text(cls, text: str) -> str:
        cleaned = cls._clean_text(text)
        cleaned = re.sub(r"^场景：过场：", "过场：", cleaned)
        cleaned = re.sub(r"^场景：场景：", "场景：", cleaned)
        if cleaned in {"场景：过场", "过场：过场"}:
            return ""
        return cleaned

    @classmethod
    def _is_low_value_system_text(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        low_value = (
            "你站在场景 4里，先感觉到的不是声音，而是所有人都在等你先露判断。",
            "你站在场景4中，所有人静待你的判断",
            "你站在场景4中，众人静待你的判断",
        )
        return cleaned in low_value

    @classmethod
    def _is_generic_repeated_system_text(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        return "先感觉到的不是声音，而是所有人都在等你先露判断" in cleaned

    @classmethod
    def _build_intro_dialogues(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        speakers: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not speakers:
            return []
        speaker = speakers[0]
        return [{
            "speaker": speaker.get("canonical_name") or speaker.get("name"),
            "character_id": speaker.get("id"),
            "text": CharacterDialogueDirector.intro_line(story_data, protagonist, speaker),
        }]

    @classmethod
    def _build_dialogues(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Dict[str, Any],
        participants: List[Dict[str, Any]],
        beat: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if beat and not beat.get("should_render_full_turn", True):
            return []
        dialogues = []
        non_protagonists = [item for item in participants if not protagonist or item.get("id") != protagonist.get("id")]
        max_speakers = 2 if (beat or {}).get("importance") == "major" else 1
        for character in non_protagonists[:max_speakers]:
            line = CharacterDialogueDirector.event_line(
                story_data,
                protagonist,
                character,
                event,
                cls._resolve_narrative_block(story_data, event),
                beat=beat,
            )
            if CharacterDialogueDirector._has_runtime_title_pollution(line):
                line = CharacterDialogueDirector.safe_fallback_line(story_data, protagonist, character)
            if line:
                dialogues.append({
                    "speaker": character.get("canonical_name") or character.get("name"),
                    "character_id": character.get("id"),
                    "text": line,
                })
        return dialogues

    @classmethod
    def _state_summary(cls, story_data: Dict[str, Any], world_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "phase": world_state.get("phase", "setup"),
            "current_scene_id": world_state.get("current_scene_id"),
            "triggered_events": len(world_state.get("triggered_event_ids", [])),
            "known_clues": len(world_state.get("unlocked_clue_ids", [])),
            "tension_points": len((world_state.get("relationship_tension") or {}).keys()),
        }

    @classmethod
    def _turn_objective(cls, event: Dict[str, Any], participants: List[Dict[str, Any]], clue_hint: str) -> str:
        if participants:
            target = participants[0].get("canonical_name") or participants[0].get("name") or "对方"
            return f"先弄清楚{target}刚才那句话到底是在提醒你、挡你，还是在故意把你往错处引。"
        if clue_hint:
            return f"先把“{clue_hint}”的来路摸实，再决定要不要让别人知道你已经盯上它了。"
        return "先分清眼前这一下到底是意外露底，还是有人故意做给你看的。"

    @classmethod
    def _turn_risk(cls, story_data: Dict[str, Any], event: Dict[str, Any], participants: List[Dict[str, Any]]) -> str:
        unresolved = NarrativeGraphService.get_unresolved_threads(story_data)
        if unresolved.get("hidden_secrets"):
            return "你脚下还压着别人没摊开的秘密。站队太早，等于替真正布局的人先把代价接过来。"
        if participants:
            names = "、".join([(item.get("name") or "对方") for item in participants[:2]])
            return f"{names}都在盯你的反应。你一旦把话说穿，接下来整场对话都会沿着你暴露出来的判断走。"
        return "你还没看到全局，但别人已经开始根据你的反应重新排位。"

    @classmethod
    def _turn_fact_anchors(
        cls,
        story_data: Dict[str, Any],
        event: Optional[Dict[str, Any]],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
    ) -> List[str]:
        sources: List[str] = []
        for item in [
            (event or {}).get("summary", ""),
            *((event or {}).get("consequences") or []),
            *((event or {}).get("outcomes") or []),
            (block or {}).get("summary", ""),
            (block or {}).get("situation", ""),
            (beat or {}).get("first_person_situation", ""),
        ]:
            cleaned = cls._clean_text(item)
            if cleaned:
                sources.append(cleaned)
        evidence_items = (event or {}).get("evidence") or []
        if not evidence_items and not event:
            evidence_items = (beat or {}).get("evidence") or []
        for evidence in evidence_items:
            quote = cls._clean_text((evidence or {}).get("quote", ""))
            if quote:
                sources.append(quote)

        joined = "；".join(sources)
        facts: List[str] = []
        if any(key in joined for key in ("财界精英", "雇职业杀手", "杀三个人", "第一批", "买凶")):
            facts.append("这场会面不是普通委托：十三名财界精英正在雇你杀人，而且目标不止一个。")
        if any(key in joined for key in ("多余款项", "退回", "账户", "零的个数", "出价", "款项")):
            facts.append("那笔多出来的钱不是慷慨，更像是在试探你的职业准则和底线。")
        if any(key in joined for key in ("年轻", "女性", "整洁", "三人", "第一批")):
            facts.append("目标里有一个年轻女性，她和另外两人的状态不一样，这个差异本身值得记住。")
        if any(key in joined for key in ("朱汉杨", "焦虚", "欲望", "不高贵")):
            facts.append("朱汉杨的眼神焦虚却没有欲望，这和他代表的身份并不相称。")
        if any(key in joined for key in ("上帝文明", "六个地球", "外星", "地球")):
            facts.append("上帝文明离开后的现实，是这场交易敢被摆上桌面的背景。")
        if any(key in joined for key in ("教官", "客户", "前额与后脑勺", "不得见面", "行业")):
            facts.append("按你的行规，客户本不该和你正面见面；今天这场会面已经越线。")
        if any(key in joined for key in ("齿哥", "利锯", "第二种方式", "来日方长")):
            facts.append("齿哥不是当前在场人物，而是你记忆里见过他使用利锯的人；这段回忆是在说明利锯还有更危险的用途。")
        if not facts:
            for source in sources:
                if source and not CharacterDialogueDirector._has_runtime_title_pollution(source):
                    facts.append(cls._truncate(source, 54))
                if len(facts) >= 2:
                    break
        return list(dict.fromkeys(facts))[:4]

    @classmethod
    def _context_characters(cls, facts: List[str]) -> List[Dict[str, Any]]:
        context: List[Dict[str, Any]] = []
        fact_text = " ".join(facts)
        if "齿哥" in fact_text:
            context.append({
                "id": "context_chige",
                "name": "齿哥",
                "role": "记忆人物",
                "summary": "他不在当前场景里；这是你关于利锯用途的一段旧记忆，用来说明这件工具的危险尺度。",
            })
        return context

    @classmethod
    def _strengthen_situation_with_facts(cls, situation: str, facts: List[str]) -> str:
        cleaned = cls._clean_text(situation)
        if not facts:
            return cleaned
        memory_fact = next((fact for fact in facts if "齿哥" in fact or "利锯" in fact), "")
        if memory_fact and cls._is_generic_repeated_system_text(cleaned):
            return memory_fact
        if any(fact[:12] in cleaned for fact in facts):
            return cleaned
        if "款项" in cleaned and any("多出来的钱" in fact or "款项" in fact for fact in facts):
            return cleaned
        if len(cleaned) < 96 or "不知道发生了什么" in cleaned:
            return cls._clean_text(f"{cleaned} {facts[0]}")
        return cleaned

    @classmethod
    def _clean_play_objective(
        cls,
        objective: str,
        facts: List[str],
        protagonist: Optional[Dict[str, Any]],
    ) -> str:
        cleaned = cls._personalize_protagonist_text(objective, protagonist)
        dirty = (
            not cleaned
            or CharacterDialogueDirector._has_runtime_title_pollution(cleaned)
            or any(token in cleaned for token in ("围绕“你告知”", "围绕“滑膛告知”", "围绕“滑膛发现”", "谁值得接近，谁值得提防"))
        )
        if not dirty:
            return cleaned
        if any("多出来的钱" in fact or "款项" in fact for fact in facts):
            return "先弄清楚这笔超额款项是谁放进来的，以及它究竟是在买你的刀，还是在试探你的底线。"
        if any("雇你杀人" in fact or "目标" in fact for fact in facts):
            return "先确认这场委托真正要杀的是哪几个人，以及朱汉杨为什么急着让你表态。"
        return "先把已经露出来的事实连起来，判断眼前这场委托真正想把你推向哪里。"

    @classmethod
    def _fact_hint(cls, facts: List[str]) -> str:
        if not facts:
            return ""
        return cls._truncate(" ".join(facts[:2]), 96)

    @classmethod
    def _personalize_protagonist_text(cls, text: str, protagonist: Optional[Dict[str, Any]]) -> str:
        cleaned = cls._clean_text(text)
        if not cleaned or not protagonist:
            return cleaned
        names = [
            cls._clean_text(protagonist.get("canonical_name", "")),
            cls._clean_text(protagonist.get("name", "")),
        ]
        for name in {item for item in names if item}:
            cleaned = cleaned.replace(f"对{name}", "对你")
            cleaned = cleaned.replace(f"{name}的", "你的")
            cleaned = cleaned.replace(name, "你")
        cleaned = cleaned.replace("主角的", "你的")
        cleaned = cleaned.replace("主角", "你")
        cleaned = cleaned.replace("如果你错判你的立场", "如果你现在先把人看错了")
        cleaned = cleaned.replace("如果你误判你的立场", "如果你现在先把人看错了")
        cleaned = cleaned.replace("如果你看错了你的立场", "如果你现在先把人看错了")
        cleaned = cleaned.replace("如果你错判你的立场", "如果你现在先把人看错了")
        cleaned = cleaned.replace("先弄清楚你到底是在", "先弄清楚对方到底是在")
        cleaned = cleaned.replace("你要不要当面逼问你", "你要不要当面逼问对方")
        return cleaned

    @classmethod
    def _speaker_candidates(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        scene: Dict[str, Any],
        event: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ids = []
        if event:
            ids.extend(event.get("participants", []))
        ids.extend(scene.get("participants", []) if scene else [])
        if protagonist:
            protagonist_id = protagonist.get("id")
            relationship_ids = []
            for edge in story_data.get("relationships", []):
                source = edge.get("source")
                target = edge.get("target")
                if protagonist_id == source and target:
                    relationship_ids.append(target)
                elif protagonist_id == target and source:
                    relationship_ids.append(source)
            ids.extend(relationship_ids[:4])
        if not ids:
            fallback_ids = [
                item.get("id")
                for item in cls._visible_speakers(story_data)
                if not protagonist or item.get("id") != protagonist.get("id")
            ]
            ids.extend(fallback_ids[:3])
        exclude_ids = {protagonist.get("id")} if protagonist and protagonist.get("id") else set()
        filtered_ids = CharacterRegistry.filter_character_ids(
            story_data,
            ids,
            require_playable=True,
            require_speaking=True,
            exclude_ids=exclude_ids,
            limit=4,
        )
        return [
            CharacterRegistry.get_character(story_data, item_id, require_speaking=True)
            for item_id in filtered_ids
            if CharacterRegistry.get_character(story_data, item_id, require_speaking=True)
        ]

    @classmethod
    def _allow_speaker_fallback(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
        explicit_character_ids: List[str],
    ) -> bool:
        protagonist_id = (protagonist or {}).get("id")
        explicit_ids = [item_id for item_id in explicit_character_ids if item_id]
        if explicit_ids and all(item_id == protagonist_id for item_id in explicit_ids):
            return False
        text_blob = " ".join([
            cls._clean_text((event or {}).get("summary", "")),
            cls._clean_text(((event or {}).get("consequences") or [""])[0]),
            cls._clean_text((beat or {}).get("first_person_situation", "")),
        ])
        if any(token in text_blob for token in ("齿哥", "利锯", "回忆", "听说过")):
            return False
        return True

    @classmethod
    def _speaker_candidates_from_ids(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character_ids: List[str],
    ) -> List[Dict[str, Any]]:
        if not character_ids:
            return []
        exclude_ids = {protagonist.get("id")} if protagonist and protagonist.get("id") else set()
        filtered_ids = CharacterRegistry.filter_character_ids(
            story_data,
            character_ids,
            require_playable=True,
            require_speaking=True,
            exclude_ids=exclude_ids,
            limit=4,
        )
        return [
            CharacterRegistry.get_character(story_data, item_id, require_speaking=True)
            for item_id in filtered_ids
            if CharacterRegistry.get_character(story_data, item_id, require_speaking=True)
        ]

    @classmethod
    def _stage_characters_from_speakers(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        speakers: List[Dict[str, Any]],
        dialogues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        stage = []
        line_map = {item.get("character_id"): item.get("text", "") for item in dialogues if item.get("character_id")}
        for character in speakers[:2]:
            if protagonist and character.get("id") == protagonist.get("id"):
                continue
            stage.append({
                "id": character.get("id"),
                "name": character.get("canonical_name") or character.get("name"),
                "role": CharacterDialogueDirector.display_role(character),
                "summary": cls._presence_summary(
                    story_data,
                    protagonist,
                    character,
                    line_map.get(character.get("id"), ""),
                ),
            })
        return stage[:2]

    @classmethod
    def _presence_summary(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
        line: str,
    ) -> str:
        relation = cls._relation_to_protagonist(story_data, protagonist, character)
        if relation in {"TRUSTS", "ALLIES_WITH"}:
            return "刚开口时还在给你留余地。"
        if relation in {"HATES", "CONFLICTS_WITH", "HIDES_FROM"}:
            return "说话时明显在防你，也在看你会不会露底。"
        if "低声" in line or "别" in line:
            return "语气压得很低，像是在提醒你，也像在试你的反应。"
        if "问" in line or "什么意思" in line:
            return "没有把话说满，但明显在逼你给出反应。"
        return "说完后没有移开视线，像是在等你下一步。"

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
    def _compose_intro_narration(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        scene: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        dialogues: List[Dict[str, Any]],
    ) -> str:
        title = story_data.get("title", "这个世界")
        summary = cls._clean_text(story_data.get("summary", ""))
        block_summary = cls._clean_text((block or {}).get("summary", ""))
        first_line = dialogues[0]["text"] if dialogues else ""
        sentences = [
            f"你一脚踏进{cls._scene_label(scene)}，立刻知道这不是一场能按常理说完的话。",
        ]
        if summary:
            sentences.append(cls._personalize_protagonist_text(cls._truncate(summary, 72), protagonist))
        if block_summary:
            sentences.append(cls._personalize_protagonist_text(cls._truncate(block_summary, 72), protagonist))
        if first_line:
            speaker = dialogues[0]["speaker"]
            sentences.append(f"{speaker}先开了口：{first_line}")
        sentences.append(f"这意味着在《{title}》里，你不是来旁观的；你接下来每一次停顿、每一句话，都会被人拿去判断你的底细。")
        return " ".join(sentences[:5])

    @classmethod
    def _supplemental_system_hint(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Optional[Dict[str, Any]],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
        participants: List[Dict[str, Any]],
    ) -> str:
        sources = []
        evidence_items = (event or {}).get("evidence") or []
        if not evidence_items and not event:
            evidence_items = (beat or {}).get("evidence") or []
        for evidence in evidence_items[:2]:
            quote = cls._clean_text((evidence or {}).get("quote", ""))
            if quote:
                sources.append(quote)
        for text in [
            (block or {}).get("conflict", ""),
            (block or {}).get("player_implication", ""),
            (event or {}).get("summary", ""),
            ((event or {}).get("consequences") or [""])[0],
        ]:
            cleaned = cls._clean_text(text)
            if cleaned:
                sources.append(cleaned)
        filtered = []
        for item in sources:
            if item in filtered:
                continue
            if not cls._should_surface_as_context(item):
                continue
            filtered.append(item)
        if not filtered:
            return ""
        if any("齿哥" in item or "利锯" in item for item in filtered):
            return cls._heuristic_system_hint(" ".join(filtered))
        llm_hint = cls._llm_system_hint(story_data, protagonist, event, block, beat, participants, filtered[:3])
        if llm_hint:
            return llm_hint
        fallback = filtered[0]
        return cls._heuristic_system_hint(fallback)

    @classmethod
    def _should_surface_as_context(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return False
        if CharacterDialogueDirector._looks_like_exposition_fragment(cleaned):
            return True
        context_markers = (
            "教官曾说过",
            "你听教官不止一次地说过",
            "客户",
            "上帝文明",
            "外星",
            "财界精英",
            "职业杀手",
            "第一次接触",
            "行业规矩",
            "东方3000",
            "委员会",
            "远源集团",
        )
        return any(marker in cleaned for marker in context_markers)

    @classmethod
    def _llm_system_hint(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        event: Optional[Dict[str, Any]],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
        participants: List[Dict[str, Any]],
        sources: List[str],
    ) -> str:
        llm = CharacterDialogueDirector._get_llm_client()
        if not llm:
            return ""
        protagonist_name = cls._clean_text((protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你")
        people = "、".join([item.get("name") or "对方" for item in participants[:2]]) or "这些人"
        prompt = "\n".join([
            "请把下面这些旁白/背景信息压成一条系统提示。",
            "只输出一句中文系统提示，不要写建议，不要教玩家怎么选，不要写后果分析。",
            "要告诉玩家此刻应该知道、但角色不会主动说透的背景。",
            f"主角：{protagonist_name}",
            f"当前相关人物：{people}",
            f"当前局面：{cls._clean_text((beat or {}).get('first_person_situation', '')) or cls._clean_text((event or {}).get('summary', ''))}",
            f"背景材料：{'；'.join(sources)}",
        ])
        try:
            raw = llm.chat(
                [
                    {
                        "role": "system",
                        "content": "你在写互动叙事里的系统提示。只输出一句20到48字的中文系统提示，不要用角色口吻，不要下指导命令。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=90,
            )
        except Exception as exc:
            logger.warning("Story system hint LLM fallback: %s", exc)
            return ""
        line = cls._clean_text(raw)
        if not line or len(line) < 8:
            return ""
        if any(token in line for token in ("你应该", "最好", "现在要", "立刻", "选择")):
            return ""
        source_text = cls._clean_text("；".join(sources))
        invented_locations = ("办公室", "地下室", "走廊", "电梯", "机房", "街边", "酒店房间")
        if any(token in line for token in invented_locations) and not any(token in source_text for token in invented_locations):
            return ""
        return cls._truncate(line, 54)

    @classmethod
    def _heuristic_system_hint(cls, text: str) -> str:
        cleaned = cls._clean_text(text)
        if "齿哥" in cleaned or "利锯" in cleaned:
            return "齿哥不是当前在场人物，而是你记忆中的旧人；这段回忆是在说明利锯的危险用途。"
        if "财界精英" in cleaned or "职业杀手" in cleaned:
            return "你面前这些人不是普通客户，他们今天坐在这里，是来谈买凶的。"
        if "上帝文明" in cleaned or "外星" in cleaned:
            return "上帝文明离开后的现实，是这场委托敢被摆到桌面上的底色。"
        if "教官曾说过" in cleaned or "客户" in cleaned:
            return "按这一行的规矩，你本不该和客户正面见面；今天这场会面，本身就已经越线了。"
        return cls._truncate(cleaned, 46)

    @classmethod
    def _compose_turn_narration(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        scene: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        dialogues: List[Dict[str, Any]],
        participants: List[Dict[str, Any]],
    ) -> str:
        evidence_quotes = [
            cls._clean_text((evidence or {}).get("quote", ""))
            for evidence in (event.get("evidence") or [])[:3]
            if cls._clean_text((evidence or {}).get("quote", ""))
        ]
        details = [cls._truncate(text, 72) for text in evidence_quotes[:2] if text and not cls._is_generic_narrative_fragment(text)]
        if not details:
            fallback_detail = cls._event_hook_text(event)
            if cls._is_generic_narrative_fragment(fallback_detail):
                fallback_detail = "有人把话放出来了，但真正要你注意的不是内容，而是谁故意在你面前把它说出来。"
            details = [cls._truncate(fallback_detail, 72)]
        tension = cls._clean_text((block or {}).get("conflict", "")) or cls._clean_text((event.get("consequences") or [""])[0])
        if cls._is_generic_narrative_fragment(tension):
            tension = ""
        implication = cls._clean_text((block or {}).get("player_implication", "")) or cls._turn_risk(story_data, event, participants)
        if cls._is_generic_narrative_fragment(implication):
            implication = cls._turn_risk(story_data, event, participants)
        speaker_line = f"{dialogues[0]['speaker']}的话没有说满，这反而比把话说透更危险。" if dialogues else ""
        sentences = [
            f"你站在{cls._scene_label(scene)}里，先撞上的不是声音，而是动作：{details[0]}。",
        ]
        if len(details) > 1:
            sentences.append(f"紧接着又有一处细节不对：{details[1]}。")
        if tension:
            sentences.append(f"不对劲的地方在于，{cls._personalize_protagonist_text(cls._truncate(tension, 78), protagonist)}")
        if speaker_line:
            sentences.append(speaker_line)
        if implication:
            sentences.append(f"这对你意味着，{cls._personalize_protagonist_text(cls._truncate(implication, 84), protagonist)}")
        return " ".join(sentences[:5])

    @classmethod
    def _event_clue_hint(cls, story_data: Dict[str, Any], event: Dict[str, Any]) -> str:
        clue_id = (event.get("clues") or [None])[0]
        if not clue_id:
            return ""
        clue = next((item for item in story_data.get("clues", []) if item["id"] == clue_id), None)
        if not clue:
            return ""
        return cls._truncate(clue.get("title") or clue.get("summary") or "", 28)

    @classmethod
    def _block_needs_refresh(cls, block: Dict[str, Any]) -> bool:
        text = " ".join([
            cls._clean_text(block.get("situation", "")),
            cls._clean_text(block.get("objective", "")),
            cls._clean_text(block.get("risk", "")),
        ])
        return any(pattern in text for pattern in (
            "被同一轮变化绑在了一起",
            "值不值得逼近",
            "逼问、试探还是装作没察觉",
        ))

    @classmethod
    def _resolve_playable_beat(cls, story_data: Dict[str, Any], event: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        beats = story_data.get("playable_beats", []) or []
        if not beats:
            return None
        if not event:
            return beats[0]
        event_id = event.get("id")
        for beat in beats:
            if event_id and event_id in beat.get("source_event_ids", []):
                return beat
        return None

    @classmethod
    def _resolve_narrative_block(cls, story_data: Dict[str, Any], event: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        blocks = story_data.get("narrative_blocks", []) or []
        if not blocks:
            return None
        if not event:
            return blocks[0]
        event_id = event.get("id")
        for block in blocks:
            if event_id and event_id in block.get("event_ids", []):
                return block
        return blocks[0]

    @classmethod
    def _relation_to_protagonist(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
    ) -> str:
        if not protagonist:
            return ""
        protagonist_id = protagonist.get("id")
        for edge in story_data.get("relationships", []):
            source = edge.get("source")
            target = edge.get("target")
            if {source, target} == {protagonist_id, character.get("id")}:
                return edge.get("relation", "")
        return ""

    @classmethod
    def _character_tone(cls, character: Dict[str, Any]) -> str:
        text = cls._clean_text(
            " ".join([
                character.get("summary", ""),
                character.get("persona", ""),
                " ".join(character.get("traits", [])),
            ])
        )
        if any(keyword in text for keyword in ["冷", "克制", "沉默", "谨慎", "不露声色"]):
            return "secretive"
        if any(keyword in text for keyword in ["强硬", "锋利", "直接", "激烈", "咄咄逼人"]):
            return "aggressive"
        if any(keyword in text for keyword in ["理性", "冷静", "分析", "稳重"]):
            return "calm"
        return "neutral"

    @classmethod
    def _visible_speakers(cls, story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        characters = sorted(
            [
                item
                for item_id in CharacterRegistry.speakable_ids(story_data)
                for item in [CharacterRegistry.get_character(story_data, item_id, require_speaking=True)]
                if item
            ],
            key=lambda item: item.get("importance_score", 0.0),
            reverse=True,
        )
        return [
            item for item in characters
            if item.get("entity_type", "character") == "character"
            and cls._is_valid_speaker(cls._clean_text(item.get("canonical_name") or item.get("name") or ""))
        ]

    @classmethod
    def _resolve_scene(cls, story_data: Dict[str, Any], scene_id: Optional[str]) -> Dict[str, Any]:
        scene = next((item for item in story_data.get("scenes", []) if item["id"] == scene_id), None)
        if scene:
            return scene
        scenes = story_data.get("scenes", [])
        if scenes:
            return scenes[0]
        return {"id": "scene_unknown", "name": "未知场景", "location": "", "participants": []}

    @classmethod
    def _scene_label(cls, scene: Dict[str, Any]) -> str:
        name = cls._clean_text(scene.get("name", "")) or "未知场景"
        location = cls._clean_text(scene.get("location", ""))
        if cls._is_noisy_scene_fragment(name):
            name = "当前场景"
        if cls._is_noisy_scene_fragment(location):
            location = ""
        if location and location != name:
            return f"{name} · {location}"
        return name

    @classmethod
    def _is_noisy_scene_fragment(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return True
        if cleaned in {"这个行业中", "这一段发生在这个行业中", "这个世界里", "这里"}:
            return True
        if cleaned.startswith("这一段") or cleaned.startswith("这个行业"):
            return True
        if len(cleaned) > 12 and any(keyword in cleaned for keyword in ("发生在", "这一段", "局势", "当前")):
            return True
        if len(cleaned) > 6 and any(keyword in cleaned for keyword in ("他坐在", "她坐在", "坐在", "看着", "曾说过", "发现", "相比", "其中有一个")):
            return True
        return False

    @classmethod
    def _is_generic_narrative_fragment(cls, text: str) -> bool:
        cleaned = cls._clean_text(text)
        if not cleaned:
            return True
        generic_patterns = (
            "判断当前局势尚未明朗",
            "推动当前剧情",
            "这一段发生在",
            "这个行业中",
            "最先撞上的就是",
            "真正的问题是",
            "发生在这个",
        )
        return any(pattern in cleaned for pattern in generic_patterns)

    @classmethod
    def _is_valid_speaker(cls, name: str) -> bool:
        if not name:
            return False
        if name in cls.NOISY_SPEAKERS:
            return False
        if name.startswith("char_"):
            return False
        if re.search(r"[=《》:：/]", name):
            return False
        return True

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
        value = cls._trim_wrapping_quotes(value)
        return value

    @classmethod
    def _trim_wrapping_quotes(cls, value: str) -> str:
        pairs = (("“", "”"), ('"', '"'), ("'", "'"), ("`", "`"))
        cleaned = value.strip()
        while len(cleaned) >= 2:
            for left, right in pairs:
                if cleaned.startswith(left) and cleaned.endswith(right):
                    cleaned = cleaned[1:-1].strip()
                    break
            else:
                break
        return cleaned

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


class ActionDirector:
    @classmethod
    def default_actions(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]] = None,
        block: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionOption]:
        visible = NarrativeEventAdapter._visible_speakers(story_data)
        target = next((item for item in visible if not protagonist or item.get("id") != protagonist.get("id")), None)
        options = []
        if beat and beat.get("suggested_action_intents"):
            options.extend(cls._options_from_intents("intro", beat.get("suggested_action_intents", []), story_data, target))
        if block and block.get("action_vectors"):
            options.extend(cls._options_from_vectors("intro", block.get("action_vectors", []), story_data, target))
        if target:
            target_name = target.get("canonical_name") or target.get("name") or "对方"
            options.append(
                DecisionOption(
                    id=f"intro_probe_{target['id']}",
                    label=f"看着{target_name}，慢慢问一句：“你刚才那话，到底是在提醒我，还是在拦我？”",
                    impact=f"你把第一句话直接递到{target_name}脸上，逼他先给出反应。",
                    risk="如果对方本就防备，你的试探会立刻被记住。",
                    action_type="probe_character",
                    target_character_id=target["id"],
                )
            )
        options.extend([
            DecisionOption(
                id="intro_observe",
                label="什么都不接，只把沉默留给房间里的人，等第一个沉不住气的人先开口",
                impact="你不抢话，而是逼别人先把情绪和站位露出来。",
                risk="你会错过主动塑造第一印象的机会。",
                action_type="observe",
            ),
            DecisionOption(
                id="intro_move",
                label="借口走到一边，换个角度把房间里每个人的反应重新看一遍",
                impact="你暂时拉开正面压力，把注意力放到站位和眼神变化上。",
                risk="你可能因此错过某个当场能问出口的问题。",
                action_type="reposition",
            ),
        ])
        return options[:3]

    @classmethod
    def build_actions(
        cls,
        story_data: Dict[str, Any],
        event: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        participants: List[Dict[str, Any]],
        beat: Optional[Dict[str, Any]] = None,
        block: Optional[Dict[str, Any]] = None,
        scene: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionOption]:
        options: List[DecisionOption] = []
        if beat and not beat.get("should_render_full_turn", True):
            return []
        primary_character = participants[0] if participants else None
        secondary_character = participants[1] if len(participants) > 1 else None
        clue_id = (event.get("clues") or [None])[0]
        clue = next((item for item in story_data.get("clues", []) if item["id"] == clue_id), None)
        previous_turn = ((story_data.get("play_state") or {}).get("current_turn") or {})
        response_mode = bool(previous_turn.get("last_action") or previous_turn.get("latest_feedback"))
        if response_mode and primary_character:
            return cls._response_actions(story_data, event, primary_character, clue)
        if cls._is_memory_context(event, beat) and not primary_character:
            return cls._memory_context_actions(event)

        if beat and beat.get("suggested_action_intents"):
            options.extend(
                cls._options_from_intents(
                    event["id"],
                    beat.get("suggested_action_intents", []),
                    story_data,
                    primary_character,
                    clue,
                    scene,
                )
            )
        if block and block.get("action_vectors"):
            options.extend(cls._options_from_vectors(event["id"], block.get("action_vectors", []), story_data, primary_character, clue))

        if primary_character:
            target_name = primary_character["name"]
            options.append(
                DecisionOption(
                    id=f"{event['id']}_press_{primary_character['id']}",
                    label=f"盯着{target_name}，把声音压低一点，直接问他：“你刚才那句话，准备让我怎么理解？”",
                    impact=f"你不给{target_name}绕开的空间，逼他当场表态。",
                    risk=f"{target_name}可能因此警觉，甚至开始防备你。",
                    action_type="press_character",
                    target_character_id=primary_character["id"],
                    target_event_id=event["id"],
                )
            )
            options.append(
                DecisionOption(
                    id=f"{event['id']}_probe_{primary_character['id']}",
                    label=f"把话故意停在半截，只看着{target_name}，等他自己把后半句补出来",
                    impact=f"你不给结论，只逼{target_name}先露反应。",
                    risk="如果对方比你更谨慎，他会立刻意识到你在试探。",
                    action_type="probe_character",
                    target_character_id=primary_character["id"],
                    target_event_id=event["id"],
                )
            )

        if clue:
            clue_title = NarrativeEventAdapter._truncate(clue.get("title") or clue.get("summary") or "这条线索", 22)
            options.append(
                DecisionOption(
                    id=f"{event['id']}_verify_{clue['id']}",
                    label=f"先把这句话按住，转身去确认“{clue_title}”到底是谁放出来的",
                    impact="你暂时不亮底牌，先去把真正站得住的那部分信息抓到手里。",
                    risk="你需要把当前对话往后拖，这会让对方察觉你在回避。",
                    action_type="verify_clue",
                    target_clue_id=clue["id"],
                    target_event_id=event["id"],
                )
            )
        elif secondary_character:
            target_name = secondary_character["name"]
            primary_name = primary_character["name"] if primary_character else "对方"
            options.append(
                DecisionOption(
                    id=f"{event['id']}_share_{secondary_character['id']}",
                    label=f"转向{target_name}，像随口一样漏出半句，让{primary_name}自己决定要不要插话",
                    impact=f"你不正面碰撞，而是换个对象丢石头，看真正紧张的人会不会自己跳出来。",
                    risk="你一旦给错了人，消息很快会从你手里失控。",
                    action_type="reveal_partial",
                    target_character_id=secondary_character["id"],
                    target_event_id=event["id"],
                )
            )

        scene_label = NarrativeEventAdapter._scene_label(scene or {})
        options.append(
            DecisionOption(
                id=f"{event['id']}_leave_scene",
                label=f"借口离开{scene_label if scene_label != '未知场景' else '当前位置'}，换个地方把这场局重新看一遍",
                impact="你先从正面压力里抽身，避免继续被别人牵着说话。",
                risk="你会失去继续当场施压的窗口。",
                action_type="leave_scene",
                target_event_id=event["id"],
            )
        )
        deduped = []
        seen_labels = set()
        for option in options:
            if option.label in seen_labels:
                continue
            seen_labels.add(option.label)
            deduped.append(option)
        return deduped[:4]

    @classmethod
    def _is_memory_context(cls, event: Dict[str, Any], beat: Optional[Dict[str, Any]]) -> bool:
        text = " ".join([
            NarrativeEventAdapter._clean_text(event.get("summary", "")),
            NarrativeEventAdapter._clean_text(((event.get("consequences") or [""])[0])),
            NarrativeEventAdapter._clean_text((beat or {}).get("first_person_situation", "")),
        ])
        return any(token in text for token in ("齿哥", "利锯", "第二种方式", "来日方长"))

    @classmethod
    def _memory_context_actions(cls, event: Dict[str, Any]) -> List[DecisionOption]:
        return [
            DecisionOption(
                id=f"{event['id']}_memory_hold",
                label="把齿哥和利锯这段记忆先压住，继续听眼前这场委托往下说",
                impact="你不让旧记忆打乱当下判断，只把它当成危险尺度记在心里。",
                risk="如果这段记忆正是关键提示，你可能会晚一步才意识到它和委托有关。",
                action_type="observe",
                target_event_id=event["id"],
            ),
            DecisionOption(
                id=f"{event['id']}_memory_link",
                label="先在心里把利锯、齿哥和这次委托连起来，判断它为什么会在此刻浮上来",
                impact="你暂停对话节奏，把旧记忆和当前委托之间的关系重新压实。",
                risk="你停得太久，会让场上的人察觉你想到了别的东西。",
                action_type="observe",
                target_event_id=event["id"],
            ),
        ]

    @classmethod
    def _response_actions(
        cls,
        story_data: Dict[str, Any],
        event: Dict[str, Any],
        primary_character: Dict[str, Any],
        clue: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionOption]:
        name = primary_character.get("name") or primary_character.get("canonical_name") or "对方"
        options = [
            DecisionOption(
                id=f"{event['id']}_response_probe_{primary_character['id']}",
                label=f"顺着{name}刚才那句追问：“你是在提醒我，还是在替谁挡话？”",
                impact=f"你不离开当前话题，逼{name}把那层没说透的意思再往外掀一点。",
                risk=f"{name}会立刻意识到你听懂了他刚才没说满的那半句。",
                action_type="probe_character",
                target_character_id=primary_character["id"],
                target_event_id=event["id"],
            ),
            DecisionOption(
                id=f"{event['id']}_response_press_{primary_character['id']}",
                label=f"直接打断{name}：“别绕了，你现在到底想让我替谁背书？”",
                impact=f"你把话重新钉回{name}脸上，不给他继续含混带过去。",
                risk="你会把房间里的温度再往上拽一截。",
                action_type="press_character",
                target_character_id=primary_character["id"],
                target_event_id=event["id"],
            ),
            DecisionOption(
                id=f"{event['id']}_response_listen_{primary_character['id']}",
                label=f"什么都不接，只抬一下手，示意{name}继续往下说",
                impact="你不抢结论，只逼对方在安静里把剩下那句话自己送出来。",
                risk="如果对方比你更稳，他可能会顺势把球再踢回给你。",
                action_type="continue_listen",
                target_character_id=primary_character["id"],
                target_event_id=event["id"],
            ),
        ]
        if clue:
            clue_title = NarrativeEventAdapter._truncate(clue.get("title") or clue.get("summary") or "这条线索", 18)
            options.append(
                DecisionOption(
                    id=f"{event['id']}_response_verify_{clue['id']}",
                    label=f"先不接{name}这句，转去确认“{clue_title}”到底是谁故意递到你面前的",
                    impact="你把注意力从眼前这句话抽出来，先抓住能核实的那一块。",
                    risk="你一转开话头，别人就会猜到你已经盯上别的东西了。",
                    action_type="verify_clue",
                    target_clue_id=clue["id"],
                    target_event_id=event["id"],
                )
            )
        return options[:4]

    @classmethod
    def _options_from_intents(
        cls,
        prefix: str,
        intents: List[str],
        story_data: Dict[str, Any],
        primary_character: Optional[Dict[str, Any]] = None,
        clue: Optional[Dict[str, Any]] = None,
        scene: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionOption]:
        options: List[DecisionOption] = []
        secondary_character = None
        if primary_character:
            protagonist_id = (ProtagonistResolver.resolve(story_data) or {}).get("id")
            visible = [
                item for item in NarrativeEventAdapter._visible_speakers(story_data)
                if item.get("id") != primary_character.get("id")
                and item.get("id") != protagonist_id
            ]
            secondary_character = visible[0] if visible else None
        for index, intent in enumerate(intents[:5], 1):
            action = NarrativeEventAdapter._clean_text(intent)
            if action == "press_character" and primary_character:
                name = primary_character.get("name", "对方")
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label=f"盯住{name}，当众把问题问穿：“你刚才那句，到底想让我替谁背书？”",
                        impact=f"你把冲突直接压到{name}脸上。",
                        risk=f"{name}会立刻知道你不打算再顺着他的节奏走。",
                        action_type="press_character",
                        target_character_id=primary_character.get("id"),
                    )
                )
            elif action == "probe_character" and primary_character:
                name = primary_character.get("name", "对方")
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label=f"把话停在最关键的地方，只看{name}会不会自己把后半句接出来",
                        impact=f"你不抢结论，而是逼{name}先暴露真实反应。",
                        risk="对方一旦察觉你的试探，后面会更难撬动。",
                        action_type="probe_character",
                        target_character_id=primary_character.get("id"),
                    )
                )
            elif action == "verify_clue" and clue:
                clue_title = NarrativeEventAdapter._truncate(clue.get("title") or clue.get("summary") or "这条线索", 20)
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label=f"先不接眼前这句话，转身去确认“{clue_title}”到底是谁放到你面前的",
                        impact="你把注意力从话术里抽出来，先抓可核实的东西。",
                        risk="别人会察觉你突然避开正面回答。",
                        action_type="verify_clue",
                        target_clue_id=clue.get("id"),
                    )
                )
            elif action == "reveal_partial" and secondary_character:
                name = secondary_character.get("canonical_name") or secondary_character.get("name") or "另一个人"
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label=f"转向{name}，故意漏出半句，让真正紧张的人自己来接",
                        impact="你换了受话人，逼真正心虚的人先动。",
                        risk="你交出去的半句，很可能被别人重新包装。",
                        action_type="reveal_partial",
                        target_character_id=secondary_character.get("id"),
                    )
                )
            elif action == "reposition":
                scene_label = NarrativeEventAdapter._scene_label(scene or {})
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label=f"借口离开{scene_label if scene_label != '未知场景' else '当前位置'}边缘，换个角度看谁在盯你",
                        impact="你暂时脱离正面压力，把注意力转回站位和细节。",
                        risk="你会失去当场继续施压的窗口。",
                        action_type="reposition",
                    )
                )
            elif action == "observe":
                options.append(
                    DecisionOption(
                        id=f"{prefix}_intent_{index}",
                        label="什么都不接，只把沉默留在桌面上，等第一个熬不住的人先露口风",
                        impact="你不暴露判断，逼别人先把情绪和站位露出来。",
                        risk="你可能因此把主动权让给更快的人。",
                        action_type="observe",
                    )
                )
        return options

    @classmethod
    def _options_from_vectors(
        cls,
        prefix: str,
        vectors: List[str],
        story_data: Dict[str, Any],
        primary_character: Optional[Dict[str, Any]] = None,
        clue: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionOption]:
        options: List[DecisionOption] = []
        for index, vector in enumerate(vectors[:3], 1):
            label = NarrativeEventAdapter._clean_text(vector)
            if not label:
                continue
            action_type = "observe"
            target_character_id = primary_character.get("id") if primary_character else None
            target_clue_id = clue.get("id") if clue else None
            risk = "这个动作会让别人更快看清你的站位。"
            if "逼问" in label or "迫使" in label:
                action_type = "press_character"
                risk = "对方会立刻意识到你不再只是旁观。"
                if primary_character:
                    label = f"往前一步，盯住{primary_character.get('name', '对方')}，把这句话当面问穿"
            elif "试探" in label or "半句" in label:
                action_type = "probe_character"
                risk = "你在钓对方反应的同时，也会留下试探痕迹。"
                if primary_character:
                    label = f"把话停在半截，只看{primary_character.get('name', '对方')}会不会自己补下去"
            elif "核实" in label or "隐瞒" in label:
                action_type = "verify_clue"
                risk = "你会暂时跳开当前对话，别人会察觉你的迟疑。"
                if clue:
                    clue_title = NarrativeEventAdapter._truncate(clue.get("title") or clue.get("summary") or "那条线索", 18)
                    label = f"暂时按住眼前的话，转去核实“{clue_title}”"
            elif "观察" in label or "不暴露" in label:
                action_type = "observe"
                risk = "你会保住位置，但也可能错过先手。"
                label = "什么都不接，只把沉默留给对方，看谁先熬不住"
            options.append(
                DecisionOption(
                    id=f"{prefix}_vector_{index}",
                    label=label,
                    impact=f"你决定立刻这么做：{label}。",
                    risk=risk,
                    action_type=action_type,
                    target_character_id=target_character_id,
                    target_clue_id=target_clue_id,
                )
            )
        return options


class CharacterDialogueDirector:
    _llm_client: Optional[LLMClient] = None
    _llm_init_failed: bool = False
    ROLE_LABELS = {
        "protagonist": "核心主角",
        "core": "核心角色",
        "supporting": "重要配角",
        "functional": "功能性角色",
        "hidden": "隐藏角色",
        "group": "群体角色",
    }

    @classmethod
    def safe_fallback_line(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
    ) -> str:
        name = character.get("canonical_name") or character.get("name") or "对方"
        last_action = cls._latest_player_action(story_data)
        if name == "朱汉杨":
            if "沉默" in last_action:
                return "朱汉杨看着你：“不接话也算表态。只是我得知道，你这份沉默压在哪边。”"
            if "打断" in last_action or "别绕" in last_action:
                return "朱汉杨看着你：“你想要直话，可以。但直话通常最贵。”"
            return "朱汉杨看着你：“我说到这里，剩下的要看你敢不敢接。”"
        if name == "许雪萍":
            if "沉默" in last_action:
                return "许雪萍轻声说：“你不说话，他们反而会更急。”"
            return "许雪萍轻声说：“别急着接他的节奏。先看谁最怕你问下去。”"
        return f"{name}看着你：“这句话我先放在这里，你自己判断。”"

    @classmethod
    def intro_line(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
    ) -> str:
        name = character.get("canonical_name") or character.get("name") or "对方"
        if name == "许雪萍":
            return "许雪萍把声音压得很低：“先别急着站队。这里有人说话给你听，也有人说话给别人听。”"
        if name == "朱汉杨":
            return "朱汉杨先看了你一眼，才开口：“你既然来了，就别把时间浪费在客气话上。”"
        if name == "滑膛":
            return "滑膛没急着接话，只淡淡说了一句：“先把人看明白，再谈别的。”"
        tone = NarrativeEventAdapter._character_tone(character)
        if tone == "aggressive":
            return f"{name}没有兜圈子：“你最好快一点想清楚，自己到底准备站在哪边。”"
        if tone == "secretive":
            return f"{name}没把声音抬高：“别急着问。真要紧的话，通常都不会当着这么多人说。”"
        return f"{name}先把局面看了一遍：“先别忙着表态，真正值得听的话，往往都在第二句以后。”"

    @classmethod
    def event_line(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]] = None,
    ) -> str:
        runtime = cls._runtime_state(story_data, character.get("id"))
        relation = NarrativeEventAdapter._relation_to_protagonist(story_data, protagonist, character)
        knowledge = cls._knowledge_fragment(story_data, character, event, block, runtime)
        danger = cls._danger_fragment(protagonist, character, event, block, beat)
        secret_pressure = float(runtime.get("secret_pressure", 0.0) or 0.0)
        speech_style = NarrativeEventAdapter._clean_text(runtime.get("speech_style", ""))
        name = character.get("canonical_name") or character.get("name") or "对方"
        llm_line = cls._llm_line(
            story_data,
            protagonist,
            character,
            event,
            block,
            beat,
            runtime,
            relation,
            knowledge,
            danger,
        )
        if llm_line:
            logger.info(
                "Story dialogue generated: character=%s mode=llm event=%s",
                name,
                event.get("id") or "unknown",
            )
            return cls._wrap_line(name, llm_line)

        anchored_line = cls._anchored_fallback_line(story_data, protagonist, character, event, block, beat)
        if anchored_line:
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return cls._wrap_line(name, anchored_line)

        if name == "许雪萍":
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return cls._xueping_line(knowledge, danger, relation, secret_pressure)
        if name == "朱汉杨":
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return cls._zhuhanyang_line(knowledge, danger, relation)
        if name == "滑膛":
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return cls._huatang_line(knowledge, danger)

        if relation in {"HATES", "CONFLICTS_WITH", "HIDES_FROM"}:
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return f"{name}看着你：“{cls._soften_fragment(danger)}”"
        if secret_pressure >= 0.45 and knowledge:
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return f"{name}声音压了下去：“{cls._soften_fragment(knowledge)}”"
        if knowledge:
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return f"{name}开口很慢：“{knowledge}”"
        if "谨慎" in speech_style or "保留" in speech_style:
            logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
            return f"{name}没有把话说满：“{cls._soften_fragment(danger)}”"
        logger.info("Story dialogue generated: character=%s mode=fallback event=%s", name, event.get("id") or "unknown")
        return f"{name}盯着你看了一秒：“{cls._soften_fragment(danger)}”"

    @classmethod
    def _anchored_fallback_line(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
    ) -> str:
        name = NarrativeEventAdapter._clean_text(character.get("canonical_name") or character.get("name") or "对方")
        last_action = cls._latest_player_action(story_data)
        facts = NarrativeEventAdapter._turn_fact_anchors(story_data, event, block, beat)
        fact_text = "；".join(facts)
        asks_money = any(token in last_action for token in ("款项", "钱", "多少", "零"))
        asks_task = any(token in last_action for token in ("干什么", "要我做什么", "想让我", "委托", "杀"))
        asks_target = any(token in last_action for token in ("谁", "目标", "背书", "替谁"))
        if name == "朱汉杨":
            if asks_money:
                return "钱只是第一道门槛。数额够不够，不如你肯不肯接重要。"
            if asks_task or "雇你杀人" in fact_text:
                return "要你做的事很简单，也很脏：三个人，先从第一批开始。"
            if asks_target:
                return "别急着找替谁背书。先看清是谁把刀递到你手里。"
            if "多出来的钱" in fact_text or "款项" in fact_text:
                return "多出来的那部分不是酬金，是看你会不会被价码牵着走。"
            if "年轻女性" in fact_text:
                return "那三个人里，最不该被忽略的，是那个收拾得太整齐的女人。"
        if name == "许雪萍":
            if asks_money:
                return "那笔钱不是重点。重点是谁想用钱先替你定规矩。"
            if asks_task or asks_target:
                return "他们想让你看见委托，却不想让你看清委托后面的人。"
            if "多出来的钱" in fact_text or "款项" in fact_text:
                return "他把钱摆得太亮了，亮到像是故意让你先看见。"
        return ""

    @classmethod
    def _runtime_state(cls, story_data: Dict[str, Any], character_id: Optional[str]) -> Dict[str, Any]:
        if not character_id:
            return {}
        state = (story_data.get("runtime_agents") or {}).get(character_id, {})
        return state if isinstance(state, dict) else vars(state)

    @classmethod
    def _knowledge_fragment(
        cls,
        story_data: Dict[str, Any],
        character: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        runtime: Dict[str, Any],
    ) -> str:
        character_id = character.get("id")
        name = character.get("canonical_name") or character.get("name") or "对方"
        memories = [NarrativeEventAdapter._clean_text(item) for item in runtime.get("memory", []) if NarrativeEventAdapter._clean_text(item)]
        beliefs = [NarrativeEventAdapter._clean_text(item) for item in runtime.get("belief_state", []) if NarrativeEventAdapter._clean_text(item)]
        known_clue_ids = set((event.get("clues") or []) + ((block or {}).get("clue_ids", []) or []))
        held_clues = [
            item for item in story_data.get("clues", [])
            if item.get("id") in known_clue_ids and character_id in (item.get("holders") or [])
        ]

        if held_clues:
            clue = held_clues[0]
            title = NarrativeEventAdapter._truncate(clue.get('title') or clue.get('summary') or '这条线索', 20)
            return f"{title}没表面上那么简单。"

        if memories:
            memory = memories[0]
            memory = memory.replace("我是", "").replace("见证事件：", "")
            memory = memory.replace(name, "这件事")
            memory = re.sub(rf"^{re.escape(name)}[\s：:，,]*", "", memory)
            memory = cls._sanitize_spoken_knowledge(memory)
            if memory:
                return NarrativeEventAdapter._truncate(memory, 30)

        if beliefs:
            belief = NarrativeEventAdapter._clean_text(beliefs[0])
            belief = re.sub(rf"^{re.escape(name)}[\s：:，,]*", "", belief)
            belief = cls._sanitize_spoken_knowledge(belief)
            if belief:
                return NarrativeEventAdapter._truncate(belief, 28)

        evidence = (event.get("evidence") or [])[:1]
        if evidence and character_id in event.get("participants", []):
            quote = NarrativeEventAdapter._clean_text((evidence[0] or {}).get("quote", ""))
            quote = cls._sanitize_spoken_knowledge(quote)
            if quote:
                return NarrativeEventAdapter._truncate(quote, 26)
        return ""

    @classmethod
    def _danger_fragment(
        cls,
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]] = None,
    ) -> str:
        candidate_sources = [
            (NarrativeEventAdapter._clean_text((event.get("consequences") or [""])[0]), 32),
            (NarrativeEventAdapter._clean_text((block or {}).get("risk", "")), 36),
            (NarrativeEventAdapter._clean_text((beat or {}).get("risk_summary", "")), 42),
        ]
        for source_text, limit in candidate_sources:
            if not source_text:
                continue
            normalized = cls._normalize_dialogue_danger(source_text, protagonist, character)
            if normalized and not cls._is_generic_danger_statement(normalized):
                return NarrativeEventAdapter._truncate(normalized, limit)
        return "再慢一步，场面就会先被别人带走。"

    @classmethod
    def _normalize_dialogue_danger(
        cls,
        text: str,
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
    ) -> str:
        cleaned = NarrativeEventAdapter._personalize_protagonist_text(text, protagonist)
        speaker_name = NarrativeEventAdapter._clean_text(character.get("canonical_name") or character.get("name") or "对方")
        cleaned = cleaned.replace("你的立场", "眼前这几个人的立场")
        cleaned = cleaned.replace("如果你错判你", "如果你错判眼前的人")
        cleaned = cleaned.replace("如果你误判你", "如果你看错了眼前的人")
        cleaned = cleaned.replace("如果你现在先把人看错了的立场", "如果你现在先把人看错了")
        if any(pattern in cleaned for pattern in ("主角", "判断当前局势尚未明朗", "推动当前剧情", "响应事件")):
            return ""
        if speaker_name and f"{speaker_name}的立场" in cleaned:
            cleaned = cleaned.replace(f"{speaker_name}的立场", "我这边的态度")
        if any(pattern in cleaned for pattern in ("如果你错判你的立场", "如果你误判你的立场", "如果你看错了你的立场")):
            cleaned = "你现在只要先把人看错一个，后面几轮话都会建立在错的前提上。"
        return NarrativeEventAdapter._clean_text(cleaned)

    @classmethod
    def _is_generic_danger_statement(cls, text: str) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(text)
        generic_patterns = (
            "后面几轮话都会建立在错的前提上",
            "接下来几轮对话都会建立在错误前提上",
            "你现在只要先把人看错一个",
            "如果你错判",
            "如果你误判",
            "错误前提",
            "再慢一步，场面就会先被别人带走",
        )
        return any(pattern in cleaned for pattern in generic_patterns)

    @classmethod
    def _huatang_line(cls, knowledge: str, danger: str) -> str:
        if knowledge:
            return f"滑膛只说了一句：“{cls._soften_fragment(knowledge)}”"
        return f"滑膛语气很平：“{cls._soften_fragment(danger)}”"

    @classmethod
    def _xueping_line(cls, knowledge: str, danger: str, relation: str, secret_pressure: float) -> str:
        if cls._is_generic_fragment(knowledge):
            knowledge = ""
        if relation in {"TRUSTS", "ALLIES_WITH"} and knowledge:
            return f"许雪萍靠近了一点：“{cls._soften_fragment(knowledge)}”"
        if secret_pressure >= 0.4 and knowledge:
            return f"许雪萍声音压得很低：“{cls._soften_fragment(knowledge)}”"
        if knowledge:
            return f"许雪萍轻声说：“{cls._soften_fragment(knowledge)}”"
        if cls._is_generic_danger_statement(danger) or not danger or cls._looks_like_exposition_fragment(danger):
            return "许雪萍轻声说：“先别急着把话说死。这里有人是在提醒你，也有人是在等你自己露判断。”"
        return f"许雪萍轻声说：“{cls._soften_fragment(danger)}”"

    @classmethod
    def _zhuhanyang_line(cls, knowledge: str, danger: str, relation: str) -> str:
        if cls._is_generic_fragment(knowledge):
            knowledge = ""
        if relation in {"HATES", "CONFLICTS_WITH"}:
            return f"朱汉杨没退：“{cls._soften_fragment(danger)}”"
        if knowledge:
            return f"朱汉杨看着你：“{cls._soften_fragment(knowledge)}”"
        if cls._is_generic_danger_statement(danger) or not danger or cls._looks_like_exposition_fragment(danger):
            return "朱汉杨看着你：“我话已经放在这儿了。你要怎么接，是你的事。”"
        return f"朱汉杨看着你：“{cls._soften_fragment(danger)}”"

    @classmethod
    def _llm_line(
        cls,
        story_data: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        character: Dict[str, Any],
        event: Dict[str, Any],
        block: Optional[Dict[str, Any]],
        beat: Optional[Dict[str, Any]],
        runtime: Dict[str, Any],
        relation: str,
        knowledge: str,
        danger: str,
    ) -> str:
        llm = cls._get_llm_client()
        if not llm:
            return ""
        name = NarrativeEventAdapter._clean_text(character.get("canonical_name") or character.get("name") or "对方")
        protagonist_name = NarrativeEventAdapter._clean_text((protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你")
        last_action = cls._latest_player_action(story_data)
        latest_feedback = NarrativeEventAdapter._clean_text(((story_data.get("play_state") or {}).get("latest_feedback") or {}).get("summary", ""))
        fact_anchors = NarrativeEventAdapter._turn_fact_anchors(story_data, event, block, beat)
        goals = [NarrativeEventAdapter._clean_text(item) for item in runtime.get("goals", []) if NarrativeEventAdapter._clean_text(item)]
        guardrails = [NarrativeEventAdapter._clean_text(item) for item in runtime.get("value_guardrails", []) if NarrativeEventAdapter._clean_text(item)]
        prompt_bits = [
            f"角色：{name}",
            f"主角：{protagonist_name}",
            f"角色说话风格：{NarrativeEventAdapter._clean_text(runtime.get('speech_style', '')) or '自然克制'}",
            f"角色当前意图：{NarrativeEventAdapter._clean_text(runtime.get('current_intent', '')) or '查清当前局势'}",
            f"角色目标：{'；'.join(goals[:2]) or '查清当前局势'}",
            f"角色价值边界：{'；'.join(guardrails[:2]) or '不能说自己不知道的事'}",
            f"与主角关系：{relation or '未知'}",
            f"当前局面：{NarrativeEventAdapter._clean_text((beat or {}).get('first_person_situation', '')) or NarrativeEventAdapter._clean_text((event or {}).get('summary', ''))}",
            f"玩家刚才动作：{last_action or '尚未明确动作'}",
            f"动作反馈：{latest_feedback or '场面刚刚起变化'}",
            f"本轮必须承接的事实：{'；'.join(fact_anchors) or '没有可靠事实锚点时，不要编造新事实'}",
            f"角色手里真正能说的内容：{knowledge or '此刻不宜直接说透'}",
            f"角色此刻担心的风险：{danger or '一旦说错，局面会更快失控'}",
            f"事件钩子：{NarrativeEventAdapter._clean_text((event or {}).get('title', '')) or '局势正在推进'}",
            '请只返回 json，例如 {"line":"..."}',
        ]
        system_prompt = (
            "你在写中文互动叙事里的角色台词。"
            "请用 json 对象返回结果。"
            "只输出这个角色此刻会对主角说的一句自然台词。"
            "不要输出旁白、动作描写、说话人名字、引号、解释、总结、分析。"
            "不要复述原著旁白，不要使用“但你怎么理解，是你的事”“不要当场把话说透”这种模板尾巴。"
            "必须明确承接玩家刚才动作或问题，不能只说谜语。"
            "如果玩家问的是“想让我干什么/款项/目标/谁出价”，台词必须至少触碰一个事实锚点，但可以保留角色的回避。"
            "不得提到事实锚点里没有的档案、页码、名单、名字、录音、监控或地址。"
            "必须像真人当场说出来的话，长度控制在12到36个汉字，允许一到两句短句。"
            "角色不能说自己不知道的信息。"
        )
        user_prompt = "\n".join(prompt_bits)
        try:
            result = llm.chat_json(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.85,
                max_tokens=120,
            )
        except Exception as exc:
            logger.warning("Story dialogue LLM json-mode fallback for %s: %s", name, exc)
            try:
                raw = llm.chat(
                    [
                        {"role": "system", "content": system_prompt + " 输出必须是 json。"},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.85,
                    max_tokens=120,
                )
                match = re.search(r"\{[\s\S]*\}", raw)
                if not match:
                    return ""
                import json
                result = json.loads(match.group(0))
            except Exception as retry_exc:
                logger.warning("Story dialogue LLM fallback for %s: %s", name, retry_exc)
                return ""
        line = NarrativeEventAdapter._clean_text((result or {}).get("line", ""))
        line = cls._normalize_generated_line(line, name)
        if not line:
            return ""
        if cls._line_escapes_fact_scope(line, fact_anchors):
            return ""
        return line

    @classmethod
    def _get_llm_client(cls) -> Optional[LLMClient]:
        if cls._llm_client:
            return cls._llm_client
        if cls._llm_init_failed:
            return None
        try:
            cls._llm_client = LLMClient()
        except Exception as exc:
            logger.info("Story dialogue LLM unavailable, use heuristic fallback: %s", exc)
            cls._llm_init_failed = True
            return None
        return cls._llm_client

    @classmethod
    def _latest_player_action(cls, story_data: Dict[str, Any]) -> str:
        play_state = story_data.get("play_state") or {}
        current_turn = play_state.get("current_turn") or {}
        last_action = NarrativeEventAdapter._clean_text(current_turn.get("last_action", ""))
        if last_action:
            return last_action
        for message in reversed(play_state.get("feed", []) or []):
            if message.get("type") == "player":
                text = NarrativeEventAdapter._clean_text(message.get("text", ""))
                if text:
                    return text
        return ""

    @classmethod
    def _normalize_generated_line(cls, line: str, speaker_name: str) -> str:
        line = NarrativeEventAdapter._clean_text(line)
        line = re.sub(rf"^{re.escape(speaker_name)}[\s：:，,]*", "", line)
        line = line.strip("“”\"'`")
        if not line:
            return ""
        if cls._looks_like_exposition_fragment(line):
            return ""
        if cls._is_generic_fragment(line):
            return ""
        blocked = (
            "但你怎么理解，是你的事",
            "你最好先记住，不要当场把话说透",
            "我只说一次，听不听得进去，看你",
            "判断当前局势尚未明朗",
            "推动当前剧情",
            "告知",
            "发现",
        )
        if any(token in line for token in blocked):
            return ""
        if len(line) < 5 or len(line) > 48:
            return ""
        return line

    @classmethod
    def _line_escapes_fact_scope(cls, line: str, facts: List[str]) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(line)
        fact_text = "；".join(facts)
        invented_artifacts = ("档案", "第三页", "文件夹", "名单", "名字", "排在第几", "录音", "监控录像", "地址")
        if any(token in cleaned for token in invented_artifacts) and not any(token in fact_text for token in invented_artifacts):
            return True
        if "年轻" in cleaned and "目标" not in fact_text and "女性" not in fact_text:
            return True
        return False

    @classmethod
    def _wrap_line(cls, name: str, line: str) -> str:
        if name == "许雪萍":
            return f"许雪萍轻声说：“{line}”"
        if name == "朱汉杨":
            return f"朱汉杨看着你：“{line}”"
        if name == "滑膛":
            return f"滑膛语气很平：“{line}”"
        return f"{name}开口：“{line}”"

    @classmethod
    def _soften_fragment(cls, text: str) -> str:
        cleaned = NarrativeEventAdapter._clean_text(text)
        cleaned = cleaned.rstrip("。！？!?")
        return cleaned

    @classmethod
    def _sanitize_spoken_knowledge(cls, text: str) -> str:
        cleaned = NarrativeEventAdapter._clean_text(text)
        if not cleaned:
            return ""
        cleaned = cleaned.replace("“", "").replace("”", "")
        cleaned = re.sub(r"^[，,。；;:“”\"'`]+", "", cleaned)
        cleaned = re.sub(r"^(朱汉杨|许雪萍|滑膛)[\s：:，,]*", "", cleaned)
        if cls._has_runtime_title_pollution(cleaned):
            return ""
        if any(
            pattern in cleaned
            for pattern in (
                "上帝文明离开地球已经三年了",
                "三年前，你听教官不止一次地说过",
                "教官曾说过",
                "客户是他们而不是他",
                "细看后才发现",
                "其中有一个是女性",
                "这十三名高贵的财界精英",
                "你按了一阵手机后说",
                "你拿出手机，查询了账户",
                "朱汉杨说，你抬头看看他",
                "许雪萍说，这女人的笑很动人",
                "这是第一批，请做得利索",
                "海上石油巨头薛桐说",
                "这把利锯的其他用途",
                "齿哥以第二种方式使用它",
                "不以为然地说",
                "你抬头看看他",
                "这女人的笑很动人",
            )
        ):
            return ""
        cleaned = re.sub(r"^(他说|她说|对方说|朱汉杨说|许雪萍说|滑膛说)[：:，,\s]*", "", cleaned)
        cleaned = re.sub(r"(他说|她说|朱汉杨说|许雪萍说|滑膛说)[。，“”\"'`\s]*$", "", cleaned)
        cleaned = re.sub(r"^说[：:，,\s]*", "", cleaned)
        if cls._is_generic_fragment(cleaned):
            return ""
        if cls._looks_like_exposition_fragment(cleaned):
            return ""
        if len(cleaned) < 4:
            return ""
        return cleaned

    @classmethod
    def _has_runtime_title_pollution(cls, text: str) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(text)
        if not cleaned:
            return False
        if re.search(r"[‘'“\"]?[\u4e00-\u9fff]{1,8}(告知|发现|联系|离开|进入|打开|查看|决定)[’'”\"]?", cleaned):
            return True
        return any(
            token in cleaned
            for token in (
                "刚才那句‘告知’",
                "刚才那句'告知'",
                "那句告知",
                "事件标题",
                "响应事件",
            )
        )

    @classmethod
    def _is_generic_fragment(cls, text: str) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(text)
        if not cleaned:
            return True
        if cleaned in {"这件事", "这句话", "这一下", "这个局面", "这个意思", "这件事。"}:
            return True
        if re.fullmatch(r"[\u4e00-\u9fff]{2,6}(告知|发现|联系|离开|进入|打开|查看|决定)", cleaned):
            return True
        if re.fullmatch(r"[\u4e00-\u9fff]{2,8}(说|问|表示|回答|告诉)$", cleaned):
            return True
        if cleaned in {"判断当前局势尚未明朗", "观察局势", "推动当前剧情"}:
            return True
        if any(pattern in cleaned for pattern in (
            "判断当前局势尚未明朗",
            "观察局势",
            "推动当前剧情",
            "响应事件",
            "当前局势",
            "不以为然地说",
            "刚才那一下不是巧合",
        )):
            return True
        return False

    @classmethod
    def _looks_like_exposition_fragment(cls, text: str) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(text)
        if not cleaned:
            return True
        prefixes = (
            "但现在，这",
            "教官曾说过",
            "细看后才发现",
            "这里初看",
            "三年前，",
            "三年前",
            "看到",
            "看见",
            "滑膛发现",
            "滑膛按",
            "滑膛拿出",
            "朱汉杨说",
            "许雪萍说",
            "其中有一个",
        )
        if cleaned.startswith(prefixes):
            return True
        if any(
            keyword in cleaned
            for keyword in (
                "这十三名高贵的财界精英",
                "教官曾说过",
                "你听教官不止一次地说过",
                "教官说过",
                "客户是他们而不是他",
                "细看后才发现",
                "其中有一个是女性",
                "对于自己开展业务的地区",
                "朱汉杨不以为然地说",
                "朱汉杨说，你抬头看看他",
                "许雪萍说，这女人的笑很动人",
                "海上石油巨头薛桐说",
                "这是第一批，请做得利索",
                "上帝文明离开地球已经三年了",
                "上帝文明在离去时告诉人类",
                "这把利锯的其他用途",
                "齿哥以第二种方式使用它",
                "按了一阵手机后说",
                "你按了一阵手机后说",
                "拿出手机，查询了账户",
                "你拿出手机，查询了账户",
            )
        ):
            return True
        if re.search(r"[\u4e00-\u9fff]{2,6}(说|表示|告诉|发现|看见|看到)", cleaned) and "你" not in cleaned and "我" not in cleaned:
            return True
        if any(keyword in cleaned for keyword in ("其中有一个", "与其他", "大厅", "窗帘", "窗前", "外面的天空")):
            return True
        if "，" in cleaned and "我" not in cleaned and "你" not in cleaned and "别" not in cleaned:
            if any(keyword in cleaned for keyword in ("发现", "看到", "看见", "曾说过", "细看", "初看")):
                return True
        return False

    @classmethod
    def _is_speakable_fragment(cls, text: str) -> bool:
        cleaned = NarrativeEventAdapter._clean_text(text)
        if not cleaned:
            return False
        if cls._is_generic_fragment(cleaned):
            return False
        if cls._looks_like_exposition_fragment(cleaned):
            return False
        return True

    @classmethod
    def display_role(cls, character: Dict[str, Any]) -> str:
        role_type = NarrativeEventAdapter._clean_text(character.get("role_type", ""))
        if role_type in cls.ROLE_LABELS:
            return cls.ROLE_LABELS[role_type]
        role = NarrativeEventAdapter._clean_text(character.get("role", ""))
        return role or "角色"


class MainPlotNodeManager:
    @classmethod
    def build_plot_node(cls, story_data: Dict[str, Any], event: Dict[str, Any], protagonist: Optional[Dict[str, Any]]) -> Optional[PlotNode]:
        turn = NarrativeEventAdapter.build_turn(story_data, event, protagonist)
        if not turn.get("should_render_full_turn", True) or not turn.get("actions"):
            return None
        return PlotNode(
            id=f"plot_{event['id']}",
            title=turn["headline"],
            summary=turn["situation"],
            event_id=event["id"],
            required=True,
            prompt="你现在要立刻做哪一个具体动作？",
            options=[DecisionOption(**item) for item in turn["actions"]],
        )


class ActionResolutionEngine:
    @classmethod
    def apply_choice(cls, story_data: Dict[str, Any], selected: Dict[str, Any]) -> Dict[str, Any]:
        world_state = WorldState(**_world_state_dict(story_data))
        action_type = selected.get("action_type") or ""
        target_character_id = selected.get("target_character_id")
        if target_character_id and not CharacterRegistry.get_character(story_data, target_character_id, require_playable=True):
            target_character_id = None
        target_clue_id = selected.get("target_clue_id")
        relationship_changes: List[str] = []
        gains: List[str] = []
        risks: List[str] = []
        target_name = cls._character_name(story_data, target_character_id)
        label = NarrativeEventAdapter._clean_text(selected.get("label", "")) or "这个动作"

        if target_character_id:
            world_state.player_state["targets"] = [target_character_id]
            key = f"player:{target_character_id}"
            current = float(world_state.relationship_tension.get(key, 0.0))
            if action_type == "press_character":
                world_state.relationship_tension[key] = round(min(current + 0.18, 1.0), 2)
                relationship_changes.append(f"{target_name}先停了一下，随后明显开始防你。")
                gains.append(f"你把场上的压力直接压到了{target_name}身上。")
                risks.append(f"如果{target_name}原本就在给你下套，你等于亲手把自己送进了他的节奏里。")
            elif action_type == "probe_character":
                world_state.relationship_tension[key] = round(min(current + 0.08, 1.0), 2)
                relationship_changes.append(f"{target_name}没有立刻接话，但眼神已经说明他听懂了你故意留下的空白。")
                gains.append(f"你没把话摊开，却看到了{target_name}最真实的第一反应。")
                risks.append("这种试探如果再来一次，对方就会开始反过来钓你的底。")
            elif action_type == "reveal_partial":
                world_state.relationship_tension[key] = round(max(current - 0.05, 0.0), 2)
                relationship_changes.append(f"{target_name}接住了你漏出去的那半句，态度松了一点，但也开始重新估计你知道多少。")
                gains.append("你用一小块真信息换到了继续说下去的空间。")
                risks.append("你交出去的东西，很可能会被别人拿去改写成对你不利的版本。")

            if target_character_id in world_state.character_states:
                world_state.character_states[target_character_id]["status"] = "engaged"
                world_state.character_states[target_character_id]["focus"] = selected.get("label", "回应你的动作")

        if action_type == "verify_clue" and target_clue_id:
            if target_clue_id not in world_state.unlocked_clue_ids:
                world_state.unlocked_clue_ids.append(target_clue_id)
            known_clues = world_state.player_state.setdefault("known_clues", [])
            if target_clue_id not in known_clues:
                known_clues.append(target_clue_id)
            clue_name = cls._clue_name(story_data, target_clue_id)
            gains.append(f"你把“{clue_name}”真正攥到了自己手里。")
            risks.append("你突然转开话题的动作太明显，别人会开始猜你到底发现了什么。")

        if action_type in {"leave_scene", "reposition"}:
            next_scene = cls._alternate_scene(story_data, world_state.current_scene_id)
            if next_scene:
                world_state.current_scene_id = next_scene["id"]
                gains.append(f"你从正面压力里抽了出来，把观察角度换到了{next_scene.get('name', '新的位置')}。")
            else:
                gains.append("你暂时拉开了与当下局面的距离，给自己争取到一口喘息。")
            risks.append("你一离开，所有人都会记住你是在什么时候转身的。")

        if action_type == "observe":
            gains.append("你没有暴露自己的判断，反而把压力留给了别人。")
            risks.append("你不出手，就只能等别人先把节奏抢走。")
        if action_type == "continue_listen":
            gains.append(f"你没有把话接回自己身上，而是逼{target_name}继续往下说。")
            risks.append(f"{target_name}如果及时收住，这一轮就会重新逼你先亮底。")

        world_state.debug_log.append(f"玩家执行动作: {label}")
        story_data["world_state"] = world_state.__dict__
        story_data["graph"] = NarrativeGraphService.apply_runtime_update(story_data, player_action=label)

        summary = cls._feedback_summary(action_type, target_name, story_data, target_clue_id)
        next_pressure = cls._next_pressure(action_type, target_name)
        return NarrativeEventAdapter.feedback_payload(
            summary=summary,
            gains=gains,
            risks=risks,
            relationship_changes=relationship_changes,
            next_pressure=next_pressure,
        )

    @classmethod
    def from_player_input(cls, story_data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        intent = result.get("intent")
        data = result.get("data") or {}
        if intent == "intervene_character":
            target_name = data.get("target_name", "对方")
            return NarrativeEventAdapter.feedback_payload(
                summary=f"你把视线和话头都压向了{target_name}。房间里的人也会立刻意识到，你接下来最在意的是他会不会露底。",
                gains=[f"你已经把冲突的焦点往{target_name}身上拉了一步。"],
                risks=["你的偏向一旦被看明白，别人就会顺着这个偏向来给你喂信息。"],
                next_pressure=f"接下来最先变化的，多半会发生在你和{target_name}之间。",
            )
        if intent == "inspect_clues":
            return NarrativeEventAdapter.feedback_payload(
                summary="你把手里的线索重新对了一遍。没有人当场开口，但有些原本模糊的地方已经开始连起来了。",
                gains=["你更清楚眼下缺的不是信息总量，而是关键的一块真相。"],
                risks=["你还没有逼出新的表态，场上的主动权仍然不在你这边。"],
                next_pressure="如果再不把问题丢到某个人脸上，这一轮就会被别人先带走。",
            )
        if intent == "inspect_relationships":
            return NarrativeEventAdapter.feedback_payload(
                summary="你重新校准了一遍人物站位。谁在给你留路，谁在等你露错，轮廓比刚才清楚了。",
                gains=["你下一句话该对谁说，终于不像刚才那么盲。"],
                risks=["只要你认错一个人，后面几轮对话就会沿着错误判断滚下去。"],
                next_pressure="你下一次开口，必须精准地选中对象。",
            )
        if intent == "advance_story":
            return NarrativeEventAdapter.feedback_payload(
                summary="你没有额外插手，只把场面让它自己往前滚。接下来先动的人，多半也会先露出自己的意图。",
                gains=["你暂时保住了自己的位置。"],
                risks=["你把先手让给了别人。"],
                next_pressure="下一轮变化不会等你准备好才发生。",
            )
        raw = NarrativeEventAdapter._clean_text(data.get("raw", ""))
        if any(token in raw for token in ("为什么", "什么", "谁", "哪", "吗", "？", "?")):
            return NarrativeEventAdapter.feedback_payload(
                summary="你把问题直接留在桌面上。没人能再假装这只是交易流程；下一句回答如果还绕开事实，就会显得更刻意。",
                gains=["你把对话从试探拉回到了事实本身。"],
                risks=["问得太直，会让对方先判断你已经知道多少，再决定给你哪一层答案。"],
                next_pressure="接下来最关键的是，对方会回答问题，还是先处理你为什么会这么问。",
            )
        return NarrativeEventAdapter.feedback_payload(
            summary="你把这句话说出口后，场面没有立刻炸开，但安静本身已经变了味。有人会先记住你的措辞，再决定怎么顺着它出手。",
            gains=["你主动把新的变量扔进了这场对话。"],
            risks=["你说出去的话，下一秒就不再完全属于你自己。"],
            next_pressure="很快就会有人顺着你刚才那句话来试你的底。",
        )

    @classmethod
    def _character_name(cls, story_data: Dict[str, Any], character_id: Optional[str]) -> str:
        if not character_id:
            return "对方"
        character = CharacterRegistry.get_character(story_data, character_id, require_playable=True)
        return NarrativeEventAdapter._clean_text((character or {}).get("canonical_name") or (character or {}).get("name") or "对方")

    @classmethod
    def _clue_name(cls, story_data: Dict[str, Any], clue_id: Optional[str]) -> str:
        if not clue_id:
            return "这条线索"
        clue = next((item for item in story_data.get("clues", []) if item.get("id") == clue_id), None)
        return NarrativeEventAdapter._clean_text((clue or {}).get("title") or (clue or {}).get("summary") or "这条线索")

    @classmethod
    def _feedback_summary(
        cls,
        action_type: str,
        target_name: str,
        story_data: Dict[str, Any],
        target_clue_id: Optional[str],
    ) -> str:
        if action_type == "press_character":
            return f"你把话直直砸向{target_name}。{target_name}没有立刻回答，先看了你一秒；这一秒已经足够让旁边的人听出，你们之间的空气变了。"
        if action_type == "probe_character":
            return f"你故意把话停在半截。{target_name}果然没有顺着表面接，而是先判断你到底知道多少；这一下，试探已经成立了。"
        if action_type == "reveal_partial":
            return f"你只漏出去半句。{target_name}接住了，但没有继续替你把话说完；他先在估计，你到底是在示好，还是在设钩子。"
        if action_type == "verify_clue":
            clue_name = cls._clue_name(story_data, target_clue_id)
            return f"你把当前话头压住，转去盯“{clue_name}”。等你再抬眼时，场上的人已经在重新判断你刚才为什么突然沉默。"
        if action_type in {"leave_scene", "reposition"}:
            return "你借口挪开了位置。没人拦你，但你能感觉到，有人的视线跟着你移动了一下。"
        if action_type == "observe":
            return "你什么都没接，只把沉默留在桌面上。最先扛不住这一下的人，往往也最接近真正的破口。"
        if action_type == "continue_listen":
            return f"你没有接话，只抬手示意{target_name}继续。对方如果还要往下说，接下来露出来的就不再是你，而是他自己。"
        return "你的动作没有白做。场上没人明说，但每个人都已经开始重新估计你。"

    @classmethod
    def _next_pressure(cls, action_type: str, target_name: str) -> str:
        if action_type in {"press_character", "probe_character"}:
            return f"接下来最关键的，不是{target_name}会不会说，而是他会先露出哪一种停顿。"
        if action_type == "reveal_partial":
            return "你已经把一小块真相交了出去，接下来要盯的是它会从谁嘴里绕回来。"
        if action_type == "verify_clue":
            return "你下一次回到人群里时，别人会先看你的脸色，再决定要不要继续说。"
        if action_type in {"leave_scene", "reposition"}:
            return "你暂时换了位置，但真正危险的是，有人会记住你离开的时机。"
        if action_type == "continue_listen":
            return f"接下来最关键的，不是你要不要追问，而是{target_name}会不会自己把后半句补出来。"
        return "下一轮变化很快就会贴着你刚才的动作长出来。"

    @classmethod
    def _alternate_scene(cls, story_data: Dict[str, Any], current_scene_id: Optional[str]) -> Optional[Dict[str, Any]]:
        for scene in story_data.get("scenes", []):
            if scene.get("id") != current_scene_id:
                return scene
        return None


class PlotDirector:
    PHASE_CADENCE_MS = {
        "setup": 4200,
        "confrontation": 3400,
        "climax": 2600,
        "resolution": 4200,
    }

    @classmethod
    def ensure_state(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> Dict[str, Any]:
        world_state = _world_state_dict(story_data)
        phase = world_state.get("phase", "setup")
        director = play_state.setdefault("director", {})
        director["phase"] = phase
        director["cadence_ms"] = cls.PHASE_CADENCE_MS.get(phase, 1800)
        director.setdefault("last_event_id", world_state.get("current_event_id"))
        director.setdefault("last_released_at", "")
        director.setdefault("next_story_beat_at", _now_iso())
        director["queue_depth"] = len(play_state.get("pending_messages", []))
        director["mode"] = "first_person"
        return director

    @classmethod
    def queue_messages(
        cls,
        play_state: Dict[str, Any],
        story_data: Dict[str, Any],
        messages: List[Dict[str, Any]],
        immediate: bool = False,
    ) -> None:
        if not messages:
            return
        director = cls.ensure_state(play_state, story_data)
        pending = play_state.setdefault("pending_messages", [])
        base_time = _now_dt()
        queued_times = [_parse_iso(item.get("available_at")) for item in pending]
        queued_times = [item for item in queued_times if item]
        if queued_times:
            base_time = max(base_time, max(queued_times))

        min_gap = max(int(director["cadence_ms"] * 0.55), 360)
        cursor = base_time
        history = list(play_state.get("feed", [])) + list(pending)
        last_signature = None
        for item in reversed(history):
            signature = cls._message_signature(item)
            if signature:
                last_signature = signature
                break
        for index, message in enumerate(messages):
            payload = dict(message)
            signature = cls._message_signature(payload)
            if signature and signature == last_signature:
                continue
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
            pending.append(payload)
            last_signature = signature
        pending.sort(key=lambda item: item.get("available_at", ""))
        director["queue_depth"] = len(pending)

    @classmethod
    def _message_signature(cls, message: Dict[str, Any]) -> tuple | None:
        msg_type = NarrativeEventAdapter._clean_text(message.get("type", ""))
        text = NarrativeEventAdapter._clean_text(message.get("text", ""))
        author = NarrativeEventAdapter._clean_text(message.get("author", ""))
        if not text:
            return None
        if msg_type == "scene":
            normalized_scene = NarrativeEventAdapter._normalize_scene_message_text(text)
            return (msg_type, "", normalized_scene)
        if msg_type == "system" and NarrativeEventAdapter._is_generic_repeated_system_text(text):
            return (msg_type, "", "generic_scene_tension")
        return (msg_type, author, text)

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

        burst_limit = 1
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
    def after_event_scheduled(cls, play_state: Dict[str, Any], story_data: Dict[str, Any], event: Dict[str, Any]) -> None:
        director = cls.ensure_state(play_state, story_data)
        beat_span_ms = max(director["cadence_ms"], 900)
        if event.get("is_key_node"):
            beat_span_ms += 900
        director["last_event_id"] = event["id"]
        director["next_story_beat_at"] = (_now_dt() + timedelta(milliseconds=beat_span_ms)).isoformat()
        director["queue_depth"] = len(play_state.get("pending_messages", []))

    @classmethod
    def nudge_after_player_action(cls, play_state: Dict[str, Any], story_data: Dict[str, Any], fast: bool = False) -> None:
        director = cls.ensure_state(play_state, story_data)
        offset = 700 if fast else max(int(director["cadence_ms"] * 0.75), 900)
        director["next_story_beat_at"] = (_now_dt() + timedelta(milliseconds=offset)).isoformat()


class ChatDrivenPlayRuntimeService:
    LEGACY_DECISION_PATTERNS = ("相信", "继续深挖", "暂时按兵不动")
    LEGACY_TURN_PATTERNS = (
        "被同一轮变化绑在了一起",
        "局势又收紧了一层",
        "值不值得逼近",
        "逼问、试探还是装作没察觉",
    )

    @classmethod
    def ensure_play_state(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        CharacterRegistry.ensure(story_data)
        play_state = story_data.setdefault("play_state", {})
        protagonist = ProtagonistResolver.resolve(story_data)
        protagonist_id = (protagonist or {}).get("id")
        protagonist_name = (protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你"
        story_data["protagonist_id"] = protagonist_id
        world_state = _world_state_dict(story_data)
        player_state = world_state.setdefault("player_state", {})
        player_state["protagonist_id"] = protagonist_id
        player_state["protagonist_name"] = protagonist_name
        story_data["world_state"] = world_state

        play_state.setdefault("session_started", False)
        play_state.setdefault("auto_mode", True)
        play_state["protagonist_id"] = protagonist_id
        play_state["protagonist_name"] = protagonist_name
        play_state.setdefault("active_plot_node_id", None)
        play_state.setdefault("current_scene_id", _world_state_dict(story_data).get("current_scene_id"))
        play_state.setdefault("pending_messages", [])
        play_state.setdefault("feed", [])
        play_state.setdefault("current_turn", None)
        play_state.setdefault("turn_history", [])
        play_state.setdefault("current_decision", None)
        play_state.setdefault("latest_feedback", None)
        play_state.setdefault("unlocked_tasks", [])
        play_state.setdefault("last_tick_at", _now_iso())
        play_state["feed"] = NarrativeEventAdapter.sanitize_visible_messages(play_state.get("feed", []), story_data)
        play_state["pending_messages"] = NarrativeEventAdapter.sanitize_visible_messages(play_state.get("pending_messages", []), story_data)
        queue_state = WorldState(**_world_state_dict(story_data))
        play_state["event_queue"] = PlayEventQueue.ensure(story_data, queue_state, play_state)
        cls._prune_redundant_continuation_tail(story_data, play_state)
        queue_state.candidate_event_ids = PlayEventQueue.derive_candidate_event_ids(play_state.get("event_queue", []))
        story_data["world_state"] = queue_state.__dict__

        decision = play_state.get("current_decision") or None
        if decision:
            option_labels = [NarrativeEventAdapter._clean_text(item.get("label", "")) for item in decision.get("options", [])]
            if any(label.startswith(pattern) for label in option_labels for pattern in cls.LEGACY_DECISION_PATTERNS) or any(
                not item.get("action_type") for item in decision.get("options", [])
            ):
                play_state["current_decision"] = None
                play_state["active_plot_node_id"] = None
            else:
                decision["title"] = NarrativeEventAdapter._clean_text(decision.get("title", "")) or "关键动作"
                decision["summary"] = NarrativeEventAdapter._clean_text(decision.get("summary", ""))
                decision["prompt"] = "你现在要立刻采取什么动作？"

        current_turn = play_state.get("current_turn")
        active_entry = PlayEventQueue.get_active_entry(play_state)
        active_event_id = (active_entry or {}).get("event_id")
        if not current_turn or cls._turn_needs_refresh(current_turn, play_state) or (
            active_event_id and current_turn.get("event_id") != active_event_id
        ):
            current_event_id = active_event_id or (current_turn or {}).get("event_id")
            current_event = next((item for item in story_data.get("events", []) if item.get("id") == current_event_id), None)
            if current_event:
                play_state["current_turn"] = NarrativeEventAdapter.build_turn(story_data, current_event, protagonist)
                PlayEventQueue.mark_turn_generated(play_state, current_event_id)
            else:
                play_state["current_turn"] = NarrativeEventAdapter.build_intro_turn(story_data, protagonist)
            play_state["turn_history"] = [play_state["current_turn"]]
        elif play_state["current_turn"].get("source_unit") != "chapter_complete" and not play_state["current_turn"].get("block_id"):
            current_event_id = active_event_id or play_state["current_turn"].get("event_id")
            current_event = next((item for item in story_data.get("events", []) if item.get("id") == current_event_id), None)
            beat = NarrativeEventAdapter._resolve_playable_beat(story_data, current_event)
            if beat:
                play_state["current_turn"]["beat_id"] = beat.get("beat_id")
                play_state["current_turn"]["importance"] = play_state["current_turn"].get("importance") or beat.get("importance", "minor")
                play_state["current_turn"]["dramatic_question"] = play_state["current_turn"].get("dramatic_question") or beat.get("dramatic_question", "")
                play_state["current_turn"]["should_render_full_turn"] = beat.get("should_render_full_turn", True)
            block = NarrativeEventAdapter._resolve_narrative_block(story_data, current_event)
            if block:
                play_state["current_turn"]["block_id"] = block.get("id")
                play_state["current_turn"]["objective"] = play_state["current_turn"].get("objective") or block.get("objective")
                play_state["current_turn"]["risk"] = play_state["current_turn"].get("risk") or block.get("risk")
                if (
                    play_state["current_turn"].get("should_render_full_turn", True)
                    and not play_state["current_turn"].get("actions")
                    and (block.get("action_vectors") or (beat or {}).get("suggested_action_intents"))
                ):
                    play_state["current_turn"]["actions"] = [
                        asdict(option)
                        for option in ActionDirector.default_actions(
                            story_data,
                            protagonist,
                            beat=beat,
                            block=block,
                        )
                    ]

        if cls._decision_needs_refresh(play_state.get("current_decision")) or (
            not play_state.get("current_decision")
            and (play_state.get("current_turn") or {}).get("actions")
            and not (play_state.get("current_turn") or {}).get("last_action")
        ):
            current_event_id = (play_state.get("current_turn") or {}).get("event_id")
            current_event = next((item for item in story_data.get("events", []) if item.get("id") == current_event_id), None)
            if current_event:
                plot_node = MainPlotNodeManager.build_plot_node(story_data, current_event, protagonist)
                if plot_node:
                    play_state["current_decision"] = asdict(plot_node)
                    play_state["active_plot_node_id"] = plot_node.id
                else:
                    play_state["current_decision"] = None
                    play_state["active_plot_node_id"] = None

        PlotDirector.ensure_state(play_state, story_data)
        return play_state

    @classmethod
    def _prune_redundant_continuation_tail(cls, story_data: Dict[str, Any], play_state: Dict[str, Any]) -> None:
        events_by_id = {item.get("id"): item for item in story_data.get("events", [])}
        changed = False
        for entry in play_state.get("event_queue", []):
            event_id = str(entry.get("event_id") or "")
            event = events_by_id.get(event_id) or {}
            if (
                entry.get("status") == "pending"
                and event.get("source") == "continuation_engine"
                and re.match(r"^continuation_\d+_event_([2-9]|\d{2,})$", event_id)
            ):
                entry["status"] = "skipped"
                entry["debug_reason"] = "；".join(filter(None, [
                    NarrativeEventAdapter._clean_text(entry.get("debug_reason", "")),
                    "续章尾部重复过场已跳过",
                ]))
                changed = True
        if changed:
            play_state["event_queue"] = PlayEventQueue._sort_queue(play_state.get("event_queue", []))

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
    def release_due_feed_messages(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        """Release delayed feed messages without advancing the story beat queue."""
        play_state = cls.ensure_play_state(story_data)
        cls._sync_current_turn_messages(story_data, play_state)
        released = PlotDirector.release_due_messages(play_state, story_data)
        current_turn = play_state.get("current_turn") or {}
        if (
            not released
            and not play_state.get("pending_messages")
            and not play_state.get("current_decision")
            and not current_turn.get("should_render_full_turn", True)
        ):
            play_state.setdefault("director", {})["next_story_beat_at"] = _now_iso()
        if (
            not released
            and not play_state.get("chapter_complete")
            and PlotDirector.should_advance_story(play_state, story_data)
        ):
            cls._advance_next_event_once(story_data, play_state, ProtagonistResolver.resolve(story_data))
            PlotDirector.release_due_messages(play_state, story_data)
        if (
            not play_state.get("chapter_complete")
            and not play_state.get("pending_messages")
            and not play_state.get("current_decision")
            and not any(item.get("status") == "pending" for item in play_state.get("event_queue", []))
        ):
            cls._handle_queue_exhausted(story_data, play_state, ProtagonistResolver.resolve(story_data))
            PlotDirector.release_due_messages(play_state, story_data)
        play_state["last_tick_at"] = _now_iso()
        return play_state

    @classmethod
    def start_session(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        if not play_state["session_started"]:
            play_state["session_started"] = True
            PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.intro_messages(story_data), immediate=True)
        return play_state

    @classmethod
    def tick(cls, story_data: Dict[str, Any], trigger: str = "auto") -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        protagonist = ProtagonistResolver.resolve(story_data)
        if not play_state["session_started"]:
            cls.start_session(story_data)
        play_state["last_tick_trigger"] = trigger
        if trigger == "manual" and play_state.get("current_decision"):
            cls._queue_manual_blocked_message(play_state, story_data)
            PlotDirector.release_due_messages(play_state, story_data)
            play_state["last_tick_at"] = _now_iso()
            return play_state
        if trigger == "manual" and not play_state.get("chapter_complete"):
            cls._append_manual_advance_marker(play_state)
        if play_state.get("chapter_complete"):
            # Play runtime should not silently invent a new conflict when the
            # current queue ends. The continuation page is the explicit place
            # for post-ending generation; otherwise truncated imports look like
            # the original story ended early.
            PlotDirector.release_due_messages(play_state, story_data)
            play_state["last_tick_at"] = _now_iso()
            return play_state

        released = PlotDirector.release_due_messages(play_state, story_data)
        if not released and PlotDirector.should_advance_story(play_state, story_data):
            cls._advance_next_event_once(story_data, play_state, protagonist)

        play_state["last_tick_at"] = _now_iso()
        return play_state

    @classmethod
    def _advance_next_event_once(
        cls,
        story_data: Dict[str, Any],
        play_state: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
    ) -> bool:
        protagonist = protagonist or ProtagonistResolver.resolve(story_data)
        attempts = 0
        while attempts < 4:
            attempts += 1
            world_state = WorldState(**_world_state_dict(story_data))
            next_event = NarrativePlanner.choose_next_event(story_data, world_state)
            if not next_event:
                cls._handle_queue_exhausted(story_data, play_state, protagonist)
                return False
            result = WorldStateEngine.apply_event(story_data, world_state, next_event["id"])
            story_data["world_state"] = result["world_state"].__dict__
            current_turn = NarrativeEventAdapter.build_turn(story_data, next_event, protagonist)
            PlayEventQueue.mark_turn_generated(play_state, next_event["id"])
            play_state["latest_feedback"] = None
            previous_turn = play_state.get("current_turn") or {}
            gate = current_turn.get("quality_gate") or {}
            logger.info(
                "Story tick evaluate: event=%s importance=%s allow_full_turn=%s compression=%s reasons=%s actions=%s dialogues=%s",
                _event_debug_label(next_event),
                gate.get("importance") or current_turn.get("importance"),
                gate.get("allow_full_turn"),
                gate.get("compression_mode"),
                ",".join(gate.get("reasons") or []) or "none",
                len(current_turn.get("actions") or []),
                len(current_turn.get("dialogues") or []),
            )

            if NarrativeCompressor.should_compress(current_turn):
                compressed = NarrativeCompressor.consume_progression(
                    story_data,
                    play_state,
                    protagonist,
                    next_event,
                    current_turn,
                )
                compressed_turn = compressed.get("turn")
                if compressed_turn:
                    cls._push_turn(play_state, compressed_turn)
                    play_state["current_turn"] = compressed_turn
                    play_state["current_decision"] = None
                    play_state["active_plot_node_id"] = None
                    PlotDirector.queue_messages(
                        play_state,
                        story_data,
                        compressed.get("messages", []),
                        immediate=True,
                    )
                    scheduled_event_id = (compressed.get("event_ids") or [next_event["id"]])[-1]
                    scheduled_event = next(
                        (item for item in story_data.get("events", []) if item.get("id") == scheduled_event_id),
                        next_event,
                    )
                    PlotDirector.after_event_scheduled(play_state, story_data, scheduled_event)
                    PlotDirector.release_due_messages(play_state, story_data)
                    return True
                logger.info(
                    "Story tick background progression consumed without frontend turn: root_event=%s",
                    _event_debug_label(next_event),
                )
                continue

            cls._push_turn(play_state, current_turn)
            turn_messages = NarrativeEventAdapter.event_messages(
                story_data,
                next_event,
                current_turn,
                previous_turn=previous_turn,
            )
            play_state["current_turn"] = current_turn
            logger.info(
                "Story tick full turn emitted: event=%s messages=%s actions=%s dialogues=%s",
                _event_debug_label(next_event),
                len(turn_messages),
                len(current_turn.get("actions") or []),
                len(current_turn.get("dialogues") or []),
            )
            PlotDirector.queue_messages(
                play_state,
                story_data,
                turn_messages,
                immediate=True,
            )
            if next_event.get("is_key_node") or "main" in next_event.get("tags", []):
                plot_node = MainPlotNodeManager.build_plot_node(story_data, next_event, protagonist)
                if plot_node:
                    play_state["current_decision"] = asdict(plot_node)
                    play_state["active_plot_node_id"] = plot_node.id
                else:
                    play_state["current_decision"] = None
                    play_state["active_plot_node_id"] = None
            else:
                play_state["current_decision"] = None
                play_state["active_plot_node_id"] = None
            PlotDirector.after_event_scheduled(play_state, story_data, next_event)
            PlotDirector.release_due_messages(play_state, story_data)
            return True
        return False

    @classmethod
    def _queue_manual_blocked_message(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> None:
        existing = list(play_state.get("feed") or []) + list(play_state.get("pending_messages") or [])
        if existing and (existing[-1].get("metadata") or {}).get("kind") == "manual_tick_blocked":
            return
        PlotDirector.queue_messages(
            play_state,
            story_data,
            [
                NarrativeEventAdapter._message(
                    "system",
                    "这是一个需要你表态的节点。先选一个动作，或直接用自由输入开口；系统不会替你跳过这一步。",
                    metadata={"kind": "manual_tick_blocked", "layer": "system", "trigger": "manual"},
                )
            ],
            immediate=True,
        )

    @classmethod
    def _append_manual_advance_marker(cls, play_state: Dict[str, Any]) -> None:
        feed = play_state.setdefault("feed", [])
        if feed and (feed[-1].get("metadata") or {}).get("kind") == "manual_advance":
            return
        feed.append(
            NarrativeEventAdapter._message(
                "player",
                "继续推进",
                author="你",
                metadata={"kind": "manual_advance", "trigger": "manual"},
            )
        )

    @classmethod
    def _handle_queue_exhausted(
        cls,
        story_data: Dict[str, Any],
        play_state: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
    ) -> None:
        if play_state.get("chapter_complete"):
            return
        world_state = _world_state_dict(story_data)
        for entry in play_state.get("event_queue", []):
            if entry.get("status") == "active":
                entry["status"] = "consumed" if entry.get("turn_generated") else "skipped"
                entry["debug_reason"] = f"{entry.get('debug_reason', '')}；队列结束时自动收束".strip("；")
        world_state["candidate_event_ids"] = PlayEventQueue.derive_candidate_event_ids(play_state.get("event_queue", []))
        story_data["world_state"] = world_state
        protagonist_name = (protagonist or {}).get("canonical_name") or (protagonist or {}).get("name") or "你"
        turn = {
            "id": f"turn_chapter_complete_{uuid.uuid4().hex[:8]}",
            "mode": "first_person",
            "source_unit": "chapter_complete",
            "headline": "本章告一段落",
            "situation": f"你以{protagonist_name}的身份推进到了当前导入文本的末尾。系统不会在游玩页里自动捏造后续冲突；如果原文还没结束，请重新抽取世界以补齐剩余叙事节拍。",
            "objective": "确认这是否是真正的原文终点；如果不是，回到总览重新抽取世界。",
            "risk": "如果继续停在同一现场，系统只会重复已经完成的过场。",
            "scene_id": world_state.get("current_scene_id"),
            "scene_label": "本章收束",
            "importance": "resolution",
            "compression_mode": "complete",
            "should_render_full_turn": False,
            "dialogues": [],
            "present_characters": [],
            "actions": cls._chapter_complete_actions(),
            "quality_gate": {
                "allow_full_turn": False,
                "compression_mode": "complete",
                "importance": "resolution",
                "reasons": ["chapter_complete"],
                "signals": {},
            },
            "state_summary": NarrativeEventAdapter._state_summary(story_data, world_state),
        }
        cls._push_turn(play_state, turn)
        play_state["current_turn"] = turn
        play_state["current_decision"] = None
        play_state["active_plot_node_id"] = None
        play_state["pending_messages"] = []
        play_state["chapter_complete"] = True
        PlotDirector.queue_messages(
            play_state,
            story_data,
            [
                NarrativeEventAdapter._message(
                    "system",
                    turn["situation"],
                    metadata={"kind": "chapter_complete", "layer": "system"},
                )
            ],
            immediate=True,
        )
        logger.info("Story event queue exhausted: chapter_complete world=%s", story_data.get("story_id", "unknown"))

    @classmethod
    def _chapter_complete_actions(cls) -> List[Dict[str, Any]]:
        return [
            {
                "id": "chapter_review_state",
                "label": "先停一步，把这一轮暴露的信息重新梳理清楚",
                "action_type": "review_chapter",
                "impact": "你会得到一段更清晰的局势回看。",
                "risk": "局势不会前进，但你能更稳地判断下一步。",
                "target_character_id": None,
                "target_clue_id": None,
                "target_event_id": None,
            },
            {
                "id": "chapter_open_continuation",
                "label": "进入原文结束后的续写页",
                "action_type": "open_continuation",
                "impact": "只在原文主线真的推进完后，用世界状态生成后续篇章。",
                "risk": "如果原文还没抽完，应优先重新抽取世界，而不是续写。",
                "target_character_id": None,
                "target_clue_id": None,
                "target_event_id": None,
            },
        ]

    @classmethod
    def _activate_continuation_chapter(
        cls,
        story_data: Dict[str, Any],
        play_state: Dict[str, Any],
        protagonist: Optional[Dict[str, Any]],
        reason: str = "",
    ) -> bool:
        if reason in {"choice", "manual", "tick"} and cls._continuation_repetition_count(story_data, play_state) >= 1:
            cls._queue_continuation_blocked_message(play_state, story_data)
            logger.info(
                "Story continuation blocked: repeated synthetic chapter world=%s reason=%s",
                story_data.get("story_id", "unknown"),
                reason or "unknown",
            )
            return False
        world_state = WorldState(**_world_state_dict(story_data))
        continuation = story_data.get("continuation") or ContinuationEngine.generate(story_data, world_state)
        story_data["continuation"] = continuation
        next_blocks = continuation.get("next_narrative_blocks") or []
        if not next_blocks:
            logger.info("Story continuation skipped: no next blocks world=%s", story_data.get("story_id", "unknown"))
            return False

        chapter_index = int(play_state.get("continuation_chapter_index") or 0) + 1
        event_prefix = f"continuation_{chapter_index}_event_"
        if any(str(item.get("id", "")).startswith(event_prefix) for item in story_data.get("events", [])):
            play_state["chapter_complete"] = False
            return True

        protagonist_id = (protagonist or {}).get("id") or (world_state.player_state or {}).get("protagonist_id")
        speaking_ids = cls._continuation_speakers(story_data, protagonist_id)
        scene_id = world_state.current_scene_id or ((story_data.get("scenes") or [{}])[0].get("id"))
        base_order = max([int(item.get("order", 0) or 0) for item in story_data.get("events", [])] or [0])

        new_events: List[Dict[str, Any]] = []
        new_blocks: List[Dict[str, Any]] = []
        new_beats: List[Dict[str, Any]] = []
        for idx, block in enumerate(next_blocks[:1], start=1):
            event_id = f"{event_prefix}{idx}"
            block_id = f"continuation_{chapter_index}_block_{idx}"
            beat_id = f"continuation_{chapter_index}_beat_{idx}"
            situation = cls._continuation_situation(story_data, block, idx)
            objective = NarrativeEventAdapter._clean_text(block.get("objective", "")) or "判断上一轮留下的沉默，究竟会把谁推到你面前。"
            risk = NarrativeEventAdapter._clean_text(block.get("risk", "")) or "你越急着找答案，越容易让别人先读懂你的判断。"
            title = cls._continuation_event_title(block, idx)
            participants = list(dict.fromkeys([item for item in [protagonist_id, *speaking_ids] if item]))

            new_events.append({
                "id": event_id,
                "title": title,
                "summary": situation,
                "order": base_order + idx,
                "actor": protagonist_id or "",
                "action": "推进",
                "target": speaking_ids[0] if speaking_ids else "",
                "event_type": "plot",
                "participants": participants,
                "scenes": [scene_id] if scene_id else [],
                "clues": [],
                "status": "pending",
                "trigger_conditions": ["上一章已收束"],
                "preconditions": ["玩家选择继续游玩"],
                "consequences": [situation],
                "outcomes": [situation],
                "caused_by": [world_state.current_event_id] if world_state.current_event_id else [],
                "leads_to": [],
                "tags": ["main", "continuation"],
                "is_key_node": idx == 1,
                "source": "continuation_engine",
            })
            new_blocks.append({
                "id": block_id,
                "title": title,
                "summary": situation,
                "situation": situation,
                "conflict": NarrativeEventAdapter._clean_text(block.get("conflict", "")) or "上一轮的问题没有消失，只是换了一种方式回到桌面。",
                "player_implication": "你不再是在重复试探，而是在处理上一轮选择留下的后果。",
                "risk": risk,
                "objective": objective,
                "action_vectors": [
                    "让对方继续说完，但只记录最反常的细节",
                    "抓住刚才那处矛盾，直接把问题压回去",
                    "暂时不表态，先看谁急着替局面下结论",
                ],
                "event_ids": [event_id],
                "participant_ids": participants,
                "clue_ids": [],
                "scene_id": scene_id,
                "phase": "continuation_setup" if idx == 1 else "confrontation",
            })
            new_beats.append({
                "beat_id": beat_id,
                "source_event_ids": [event_id],
                "source_block_id": block_id,
                "importance": "major",
                "first_person_situation": situation,
                "player_objective": objective,
                "dramatic_question": "你要不要把上一轮留下的问题继续追下去？",
                "present_character_ids": speaking_ids[:2],
                "suggested_action_intents": ["continue_listen", "press_character", "observe"],
                "revealed_clue_ids": [],
                "risk_summary": risk,
                "should_render_full_turn": True,
                "scene_id": scene_id,
                "phase": "continuation_setup" if idx == 1 else "confrontation",
            })

        story_data.setdefault("events", []).extend(new_events)
        story_data.setdefault("narrative_blocks", []).extend(new_blocks)
        story_data.setdefault("playable_beats", []).extend(new_beats)
        story_data.setdefault("continuation_history", []).append({
            "chapter_index": chapter_index,
            "event_ids": [item["id"] for item in new_events],
            "created_at": _now_iso(),
            "reason": reason,
        })
        play_state["continuation_chapter_index"] = chapter_index
        play_state["chapter_complete"] = False
        play_state["current_decision"] = None
        play_state["active_plot_node_id"] = None
        world_state.phase = "continuation_setup"
        world_state.current_event_id = None
        world_state.candidate_event_ids = []
        story_data["world_state"] = world_state.__dict__
        PlayEventQueue.ensure(story_data, world_state, play_state)
        PlotDirector.queue_messages(
            play_state,
            story_data,
            [
                NarrativeEventAdapter._message(
                    "system",
                    "你没有再停在原地。上一轮的委托、款项和沉默被收进记忆里，新的冲突开始沿着这些痕迹长出来。",
                    metadata={"kind": "continuation_started", "layer": "system"},
                )
            ],
            immediate=True,
        )
        logger.info(
            "Story continuation activated: world=%s chapter=%s events=%s reason=%s",
            story_data.get("story_id", "unknown"),
            chapter_index,
            len(new_events),
            reason or "unknown",
        )
        return True

    @classmethod
    def _continuation_repetition_count(cls, story_data: Dict[str, Any], play_state: Dict[str, Any]) -> int:
        history_count = len(story_data.get("continuation_history") or [])
        current_count = int(play_state.get("continuation_chapter_index") or 0)
        return max(history_count, current_count)

    @classmethod
    def _queue_continuation_blocked_message(cls, play_state: Dict[str, Any], story_data: Dict[str, Any]) -> None:
        PlotDirector.queue_messages(
            play_state,
            story_data,
            [
                NarrativeEventAdapter._message(
                    "system",
                    "这一章已经收束。继续重复同一场会面只会让款项、委托和沉默原地打转；如果要开新篇章，请去续写页生成新的冲突。",
                    metadata={"kind": "continuation_blocked", "layer": "system"},
                )
            ],
            immediate=True,
        )

    @classmethod
    def _continuation_speakers(cls, story_data: Dict[str, Any], protagonist_id: Optional[str]) -> List[str]:
        ids: List[str] = []
        world_state = _world_state_dict(story_data)
        target_ids = ((world_state.get("player_state") or {}).get("targets") or [])
        for char_id in target_ids:
            character = CharacterRegistry.get_character(story_data, char_id, require_speaking=True)
            if character and char_id != protagonist_id:
                ids.append(char_id)
        for character in story_data.get("characters", []) or []:
            char_id = character.get("id")
            name = character.get("canonical_name") or character.get("name") or ""
            if char_id == protagonist_id or char_id in ids:
                continue
            if name in {"朱汉杨", "许雪萍"} or character.get("can_speak"):
                checked = CharacterRegistry.get_character(story_data, char_id, require_speaking=True)
                if checked:
                    ids.append(char_id)
            if len(ids) >= 2:
                break
        return ids[:2]

    @classmethod
    def _continuation_situation(cls, story_data: Dict[str, Any], block: Dict[str, Any], idx: int) -> str:
        raw = NarrativeEventAdapter._clean_text(block.get("situation", ""))
        if raw and not CharacterDialogueDirector._has_runtime_title_pollution(raw) and "这一段发生在" not in raw:
            return raw[:220]
        if idx == 1:
            return "你把刚才的问题留在桌面上，空气没有立刻松开。十三个人仍在等你的下一次判断，而朱汉杨眼里的焦虚比他的措辞更诚实。"
        if idx == 2:
            return "款项已经退回，多余的零却没有从局面里消失。它像一枚明摆着的试探，逼你判断这场委托到底是谁在出价。"
        return "上一轮留下的沉默开始发酵。有人希望你只看见交易本身，也有人正等你发现这笔交易背后真正要被隐藏的人。"

    @classmethod
    def _continuation_event_title(cls, block: Dict[str, Any], idx: int) -> str:
        raw = NarrativeEventAdapter._clean_text(block.get("title", ""))
        if raw and not CharacterDialogueDirector._has_runtime_title_pollution(raw) and raw not in {"滑膛告知", "滑膛发现"}:
            return raw[:24]
        titles = ["余波重新开口", "款项后的试探", "沉默里的第二层委托"]
        return titles[min(idx - 1, len(titles) - 1)]

    @classmethod
    def submit_player_input(cls, story_data: Dict[str, Any], player_input: str) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        if play_state.get("chapter_complete"):
            play_state["feed"].append(
                NarrativeEventAdapter._message(
                    "player",
                    player_input,
                    author="你",
                    metadata={"kind": "player_input"},
                )
            )
            feedback = {
                "summary": "当前导入的可玩节拍已经到末尾。系统不会在游玩页里自动生成新冲突；如果原文还没结束，请重新抽取世界补齐后续剧情。",
                "impact": "自由输入已记录，但不会再用同一场景反复包装成新一轮对话。",
            }
            play_state["latest_feedback"] = feedback
            PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback), immediate=True)
            logger.info("Story player input ignored at exhausted queue: text=%s", NarrativeEventAdapter._truncate(player_input, 80))
            return {"intent": "chapter_complete", "message": feedback["summary"], "data": None}

        world_state = WorldState(**_world_state_dict(story_data))
        result = PlayerInteractionService.execute(story_data, world_state, player_input)
        story_data["world_state"] = world_state.__dict__
        if result.get("intent") in {"inspect_relationships", "inspect_clues", "inspect_storyline", "advance_story", "observe"}:
            NarrativeGraphService.apply_runtime_update(story_data, player_action=player_input)
        feedback = ActionResolutionEngine.from_player_input(story_data, result)

        play_state["feed"].append(
            NarrativeEventAdapter._message(
                "player",
                player_input,
                author="你",
                metadata={"kind": "player_input"},
            )
        )
        play_state["latest_feedback"] = feedback
        if play_state.get("current_turn"):
            play_state["current_turn"]["latest_feedback"] = feedback
            play_state["current_turn"]["last_action"] = player_input
            play_state["current_turn"]["actions"] = []
            play_state["current_turn"]["state_summary"] = NarrativeEventAdapter._state_summary(
                story_data,
                _world_state_dict(story_data),
            )
        PlayEventQueue.rebalance_after_player_action(
            story_data,
            world_state,
            play_state,
            context={
                "intent": result.get("intent", ""),
                "raw": player_input,
            },
        )
        PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback), immediate=False)
        play_state["current_decision"] = None
        play_state["active_plot_node_id"] = None
        PlotDirector.nudge_after_player_action(play_state, story_data, fast=result.get("intent") == "advance_story")
        auto_advanced = cls._advance_next_event_once(story_data, play_state, ProtagonistResolver.resolve(story_data))
        logger.info(
            "Story player input: intent=%s auto_advanced=%s text=%s next_targets=%s",
            result.get("intent", ""),
            auto_advanced,
            NarrativeEventAdapter._truncate(player_input, 80),
            ",".join(((world_state.player_state or {}).get("targets") or [])) or "none",
        )
        return result

    @classmethod
    def submit_choice(cls, story_data: Dict[str, Any], option_id: str) -> Dict[str, Any]:
        play_state = cls.ensure_play_state(story_data)
        decision = play_state.get("current_decision") or {}
        current_turn = play_state.get("current_turn") or {}
        option_pool = list(decision.get("options", [])) + list(current_turn.get("actions", []))
        selected = next((item for item in option_pool if item["id"] == option_id), None)
        if not selected:
            return {"success": False, "message": "无效选项。"}

        if selected.get("action_type") == "continue_chapter":
            if not play_state.get("chapter_complete"):
                if any(item.get("status") == "pending" for item in play_state.get("event_queue", [])):
                    cls._append_manual_advance_marker(play_state)
                    advanced = cls._advance_next_event_once(story_data, play_state, ProtagonistResolver.resolve(story_data))
                    logger.info("Story stale continue choice advanced pending event: option=%s advanced=%s", option_id, advanced)
                    return {
                        "success": True,
                        "choice": selected,
                        "feedback": {
                            "summary": "你继续往前走，系统接上了已经打开的下一拍。",
                            "impact": "没有再重复创建新的篇章。",
                        },
                    }
                return {"success": False, "message": "当前已经不在章节收束节点。"}
            if cls._continuation_repetition_count(story_data, play_state) >= 1:
                feedback = {
                    "summary": "这一章已经收束。再重复进入下一篇章，只会把同一场会面重新包装一遍。",
                    "impact": "如果原文还没结束，请回到总览重新抽取世界；如果原文已结束，再去续写页。",
                }
                PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback), immediate=True)
                play_state["latest_feedback"] = feedback
                return {"success": False, "message": feedback["summary"], "feedback": feedback}
            feedback = {
                "summary": "当前导入的可玩节拍已经到末尾。系统不会在游玩页里自动生成新冲突。",
                "impact": "如果原文还没结束，请重新抽取世界；如果原文已结束，再进入续写页。",
            }
            play_state["latest_feedback"] = feedback
            PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback), immediate=True)
            play_state["current_decision"] = None
            play_state["active_plot_node_id"] = None
            logger.info("Story player choice reached exhausted queue: option=%s", option_id)
            return {"success": True, "choice": selected, "feedback": feedback}

        if selected.get("action_type") == "review_chapter":
            if (current_turn.get("last_action") or "") == selected["label"]:
                return {"success": False, "message": "这一轮信息已经梳理过了。"}
            feedback = {
                "summary": "你暂时没有继续推进，而是把已经暴露的信息重新压回脑子里：委托是真的，款项是试探，沉默也是一种报价。",
                "impact": "如果原文还没结束，请重新抽取世界补齐后续剧情；如果原文已结束，再进入续写页。",
            }
            play_state["feed"].append(
                NarrativeEventAdapter._message(
                    "player",
                    selected["label"],
                    author="你",
                    metadata={"kind": "choice", "option_id": option_id},
                )
            )
            PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback), immediate=True)
            play_state["latest_feedback"] = feedback
            if play_state.get("current_turn"):
                play_state["current_turn"]["last_action"] = selected["label"]
                play_state["current_turn"]["latest_feedback"] = feedback
                play_state["current_turn"]["actions"] = [
                    item for item in cls._chapter_complete_actions()
                    if item.get("action_type") == "open_continuation"
                ]
            logger.info("Story player choice reviewed chapter: option=%s", option_id)
            return {"success": True, "choice": selected, "feedback": feedback}

        feedback = ActionResolutionEngine.apply_choice(story_data, selected)
        play_state["feed"].append(
            NarrativeEventAdapter._message(
                "player",
                selected["label"],
                author="你",
                metadata={"kind": "choice", "option_id": option_id},
            )
        )
        play_state["latest_feedback"] = feedback
        if play_state.get("current_turn"):
            play_state["current_turn"]["latest_feedback"] = feedback
            play_state["current_turn"]["last_action"] = selected["label"]
            play_state["current_turn"]["actions"] = []
            play_state["current_turn"]["state_summary"] = NarrativeEventAdapter._state_summary(
                story_data,
                _world_state_dict(story_data),
            )
        PlayEventQueue.rebalance_after_player_action(
            story_data,
            WorldState(**_world_state_dict(story_data)),
            play_state,
            context={
                "action_type": selected.get("action_type", ""),
                "target_character_id": selected.get("target_character_id"),
                "target_clue_id": selected.get("target_clue_id"),
                "label": selected.get("label", ""),
            },
        )
        PlotDirector.queue_messages(play_state, story_data, NarrativeEventAdapter.player_feedback_messages(feedback))
        play_state["current_decision"] = None
        play_state["active_plot_node_id"] = None
        PlotDirector.nudge_after_player_action(play_state, story_data, fast=True)
        auto_advanced = cls._advance_next_event_once(story_data, play_state, ProtagonistResolver.resolve(story_data))
        logger.info(
            "Story player choice: action_type=%s target=%s auto_advanced=%s label=%s",
            selected.get("action_type", ""),
            selected.get("target_character_id") or selected.get("target_clue_id") or "none",
            auto_advanced,
            NarrativeEventAdapter._truncate(selected.get("label", ""), 80),
        )
        return {"success": True, "choice": selected, "feedback": feedback}

    @classmethod
    def _push_turn(cls, play_state: Dict[str, Any], turn: Dict[str, Any]) -> None:
        history = list(play_state.get("turn_history", []))
        if history and history[-1].get("id") == turn.get("id"):
            history[-1] = turn
        else:
            history.append(turn)
        play_state["turn_history"] = history[-10:]

    @classmethod
    def _sync_current_turn_messages(cls, story_data: Dict[str, Any], play_state: Dict[str, Any]) -> None:
        current_turn = play_state.get("current_turn") or {}
        event_id = current_turn.get("event_id")
        if not event_id:
            return
        repair_key = f"feed_repaired:{event_id}"
        if (current_turn.get("compression_mode") or "") in {"transition", "background"}:
            current_turn["feed_repair_marker"] = repair_key
            return
        existing_messages = list(play_state.get("feed") or []) + list(play_state.get("pending_messages") or [])
        existing_for_event = [
            item
            for item in existing_messages
            if (item.get("metadata") or {}).get("event_id") == event_id
        ]
        has_event_message = bool(existing_for_event)
        has_character_message = any(item.get("type") == "character" for item in existing_for_event)
        needs_character_message = bool(current_turn.get("dialogues")) and not has_character_message
        if current_turn.get("feed_repair_marker") == repair_key and not needs_character_message:
            return
        if has_event_message and not needs_character_message:
            current_turn["feed_repair_marker"] = repair_key
            return
        event = next((item for item in story_data.get("events", []) if item.get("id") == event_id), None)
        if not event:
            current_turn["feed_repair_marker"] = repair_key
            return
        messages = NarrativeEventAdapter.event_messages(
            story_data,
            event,
            current_turn,
            previous_turn={},
        )
        messages = [
            item for item in NarrativeEventAdapter.sanitize_visible_messages(messages, story_data)
            if (item.get("metadata") or {}).get("kind") not in {"scene_transition"}
        ]
        if has_event_message and needs_character_message:
            messages = [item for item in messages if item.get("type") == "character"]
        if not messages:
            current_turn["feed_repair_marker"] = repair_key
            return
        logger.info(
            "Story feed repaired from current_turn: event=%s messages=%s",
            _event_debug_label(event),
            len(messages),
        )
        PlotDirector.queue_messages(play_state, story_data, messages, immediate=True)
        current_turn["feed_repair_marker"] = repair_key

    @classmethod
    def _turn_needs_refresh(cls, turn: Optional[Dict[str, Any]], play_state: Optional[Dict[str, Any]] = None) -> bool:
        if not turn:
            return True
        if (
            turn.get("should_render_full_turn", True)
            and not turn.get("actions")
            and not turn.get("last_action")
            and not (play_state or {}).get("current_decision")
            and turn.get("source_unit") == "playable_beat"
        ):
            return True
        if turn.get("source_unit") == "playable_beat" and turn.get("beat_id"):
            required = [turn.get("situation"), turn.get("objective"), turn.get("risk")]
            return not all(NarrativeEventAdapter._clean_text(item) for item in required)
        text_blob = " ".join([
            NarrativeEventAdapter._clean_text(turn.get("headline", "")),
            NarrativeEventAdapter._clean_text(turn.get("situation", "")),
            NarrativeEventAdapter._clean_text(turn.get("objective", "")),
            NarrativeEventAdapter._clean_text(turn.get("risk", "")),
            " ".join(
                NarrativeEventAdapter._clean_text(item.get("label", ""))
                for item in turn.get("actions", [])
                if isinstance(item, dict)
            ),
            " ".join(
                NarrativeEventAdapter._clean_text(item.get("text", ""))
                for item in turn.get("dialogues", [])
                if isinstance(item, dict)
            ),
        ])
        if any(pattern in text_blob for pattern in cls.LEGACY_TURN_PATTERNS):
            return True
        if any(keyword in text_blob for keyword in ["告知", "发现", "试探还是装作", "正面逼问"]):
            return True
        return False

    @classmethod
    def _decision_needs_refresh(cls, decision: Optional[Dict[str, Any]]) -> bool:
        if not decision:
            return False
        options = decision.get("options", []) if isinstance(decision, dict) else []
        for item in options:
            label = NarrativeEventAdapter._clean_text((item or {}).get("label", ""))
            if any(pattern in label for pattern in ("正面逼问", "故意说半句", "继续观察谁会先露出破绽", "试探滑膛")):
                return True
        return False
