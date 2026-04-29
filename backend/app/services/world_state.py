"""
世界状态、角色运行时、玩家互动与续写服务
"""

import re
from typing import Any, Dict, List, Optional

from ..models.story import CharacterRuntimeState, WorldState
from .story_graph import NarrativeGraphService


class CharacterRegistry:
    VERSION = "narraworld-character-registry-v1"
    BLACKLISTED_NAMES = {
        "时间", "时代", "家里", "路边", "什么", "这里", "那里", "这边", "那边",
        "许多", "高贵", "单元", "消息", "花纹", "平静", "周围", "方案",
    }

    @classmethod
    def ensure(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        characters = story_data.get("characters", []) or []
        current = story_data.get("character_registry") or {}
        current_ids = {
            str(item.get("id"))
            for item in (current.get("entries") or [])
            if isinstance(item, dict) and item.get("id")
        }
        expected_ids = {
            str(item.get("id"))
            for item in characters
            if cls._accept_character(item)
        }
        if current.get("version") != cls.VERSION or current_ids != expected_ids or not current.get("alias_map"):
            story_data["character_registry"] = cls.build_from_characters(characters)
        return story_data.get("character_registry", {})

    @classmethod
    def build_from_characters(cls, characters: List[Dict[str, Any]]) -> Dict[str, Any]:
        entries: List[Dict[str, Any]] = []
        alias_map: Dict[str, str] = {}
        playable_ids: List[str] = []
        speakable_ids: List[str] = []
        for character in characters:
            if not cls._accept_character(character):
                continue
            canonical_name = cls._clean_text(character.get("canonical_name") or character.get("name") or "")
            aliases = [
                cls._clean_text(alias)
                for alias in character.get("aliases", [])
                if cls._clean_text(alias) and cls._clean_text(alias) != canonical_name
            ]
            role_type = cls._clean_text(character.get("role_type", "")) or "supporting"
            importance = float(character.get("importance_score", 0.0) or 0.0)
            is_playable = cls._is_playable(role_type, importance)
            can_speak = cls._can_speak(role_type, importance)
            entry = {
                "id": character.get("id"),
                "canonical_name": canonical_name,
                "aliases": list(dict.fromkeys(aliases))[:8],
                "role_type": role_type,
                "is_playable": is_playable,
                "can_speak": can_speak,
                "source_evidence": cls._source_evidence(character),
            }
            entries.append(entry)
            if is_playable:
                playable_ids.append(entry["id"])
            if can_speak:
                speakable_ids.append(entry["id"])
            for token in [canonical_name, *entry["aliases"]]:
                normalized = token.lower()
                if normalized and normalized not in alias_map:
                    alias_map[normalized] = entry["id"]
        return {
            "version": cls.VERSION,
            "entries": entries,
            "alias_map": alias_map,
            "playable_ids": playable_ids,
            "speakable_ids": speakable_ids,
        }

    @classmethod
    def entries(cls, story_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list((cls.ensure(story_data).get("entries") or []))

    @classmethod
    def get_entry(cls, story_data: Dict[str, Any], character_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not character_id:
            return None
        return next((item for item in cls.entries(story_data) if item.get("id") == character_id), None)

    @classmethod
    def resolve_alias(cls, story_data: Dict[str, Any], token: str) -> Optional[str]:
        normalized = cls._clean_text(token).lower()
        if not normalized:
            return None
        registry = cls.ensure(story_data)
        alias_map = registry.get("alias_map") or {}
        return alias_map.get(normalized)

    @classmethod
    def get_character(
        cls,
        story_data: Dict[str, Any],
        character_id: Optional[str],
        require_playable: bool = False,
        require_speaking: bool = False,
    ) -> Optional[Dict[str, Any]]:
        entry = cls.get_entry(story_data, character_id)
        if not entry:
            return None
        if require_playable and not entry.get("is_playable"):
            return None
        if require_speaking and not entry.get("can_speak"):
            return None
        return next((item for item in story_data.get("characters", []) if item.get("id") == character_id), None)

    @classmethod
    def filter_character_ids(
        cls,
        story_data: Dict[str, Any],
        ids: List[str],
        require_playable: bool = False,
        require_speaking: bool = False,
        exclude_ids: Optional[set[str]] = None,
        limit: int = 8,
    ) -> List[str]:
        exclude_ids = exclude_ids or set()
        filtered: List[str] = []
        seen = set()
        for item_id in ids:
            if not item_id or item_id in seen or item_id in exclude_ids:
                continue
            if not cls.get_character(
                story_data,
                item_id,
                require_playable=require_playable,
                require_speaking=require_speaking,
            ):
                continue
            seen.add(item_id)
            filtered.append(item_id)
            if len(filtered) >= limit:
                break
        return filtered

    @classmethod
    def playable_ids(cls, story_data: Dict[str, Any]) -> List[str]:
        registry = cls.ensure(story_data)
        return list(registry.get("playable_ids") or [])

    @classmethod
    def speakable_ids(cls, story_data: Dict[str, Any]) -> List[str]:
        registry = cls.ensure(story_data)
        return list(registry.get("speakable_ids") or [])

    @classmethod
    def _accept_character(cls, character: Dict[str, Any]) -> bool:
        if character.get("entity_type", "character") != "character":
            return False
        name = cls._clean_text(character.get("canonical_name") or character.get("name") or "")
        if not name or name in cls.BLACKLISTED_NAMES:
            return False
        if name.startswith("char_") or re.search(r"[=《》/\\]", name):
            return False
        role_type = cls._clean_text(character.get("role_type", ""))
        if role_type == "group":
            return False
        importance = float(character.get("importance_score", 0.0) or 0.0)
        if importance < 0.2 and role_type not in {"protagonist", "core", "supporting"}:
            return False
        return True

    @classmethod
    def _is_playable(cls, role_type: str, importance: float) -> bool:
        if role_type in {"protagonist", "core", "supporting", "hidden"}:
            return True
        return importance >= 0.45

    @classmethod
    def _can_speak(cls, role_type: str, importance: float) -> bool:
        if role_type in {"protagonist", "core", "supporting", "hidden"}:
            return True
        return importance >= 0.55

    @classmethod
    def _source_evidence(cls, character: Dict[str, Any]) -> List[str]:
        evidence: List[str] = []
        for item in character.get("evidence", [])[:3]:
            quote = cls._clean_text((item or {}).get("quote", ""))
            note = cls._clean_text((item or {}).get("note", ""))
            text = quote or note
            if text and text not in evidence:
                evidence.append(text[:80])
        for note in character.get("review_notes", [])[:2]:
            cleaned = cls._clean_text(note)
            if cleaned and cleaned not in evidence:
                evidence.append(cleaned[:80])
        return evidence[:4]

    @classmethod
    def _clean_text(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        text = re.sub(r"\s+", " ", text).strip()
        return text.strip("“”\"'`")


class CharacterAgentRuntimeService:
    NAME_STYLE_OVERRIDES = {
        "滑膛": "冷静克制，短句，先看再动，不轻易解释自己。",
        "许雪萍": "低声试探，话只说七分，像提醒，也像留后手。",
        "朱汉杨": "掌控感强，直接开口，但每一句都给自己留余地。",
    }
    EXPOSITION_HINTS = (
        "教官曾说过",
        "你听教官不止一次地说过",
        "但现在，这",
        "客户是他们而不是他",
        "这十三名高贵的财界精英",
        "细看后才发现",
        "这女人的笑很动人",
        "海上石油巨头薛桐说",
        "上帝文明在离去时告诉人类",
        "这把利锯的其他用途",
        "齿哥以第二种方式使用它",
        "你按了一阵手机后说",
        "拿出手机，查询了账户",
    )

    @classmethod
    def bootstrap_agents(cls, story_data: Dict[str, Any]) -> Dict[str, CharacterRuntimeState]:
        runtime = {}
        CharacterRegistry.ensure(story_data)
        for character_id in CharacterRegistry.playable_ids(story_data):
            character = CharacterRegistry.get_character(story_data, character_id, require_playable=True)
            if not character:
                continue
            context = NarrativeGraphService.get_character_context(story_data, character["id"])
            goals = cls._sanitize_goal_list(character.get("goals", []), character.get("entity_type", "character"))
            beliefs = cls._sanitize_belief_list(character.get("beliefs", []))
            runtime[character["id"]] = CharacterRuntimeState(
                character_id=character["id"],
                persona=character.get("persona", "") or character.get("summary", ""),
                goals=goals,
                memory=cls._build_initial_memory(character, context, beliefs),
                belief_state=beliefs,
                relationship_state={
                    edge.get("target"): edge.get("type")
                    for edge in context.get("edges", [])
                    if edge.get("source") == character["id"] and edge.get("target")
                },
                action_policy=cls._build_action_policy(character, context, goals),
                speech_style=cls._infer_speech_style(character),
                risk_profile=cls._infer_risk_profile(character, context),
                value_guardrails=cls._infer_value_guardrails(character),
                knowledge_scope=character.get("knowledge_scope", []),
                current_intent=(goals or ["查清当前局势"])[0],
                secret_pressure=min(len(context.get("secrets", [])) * 0.15, 1.0),
            )
        return runtime

    @classmethod
    def _build_initial_memory(cls, character: Dict[str, Any], context: Dict[str, Any], beliefs: List[str]) -> List[str]:
        memory = [f"我是{character.get('canonical_name') or character.get('name')}。"]
        memory.extend(beliefs[:2])
        for edge in context.get("edges", [])[:3]:
            target = edge.get("target")
            relation = edge.get("type")
            memory.append(f"我与 {target} 的关系是 {relation}。")
        return memory[:8]

    @classmethod
    def _build_action_policy(cls, character: Dict[str, Any], context: Dict[str, Any], goals: List[str]) -> str:
        role = character.get("role") or character.get("role_type") or "角色"
        goals = "、".join(goals[:3]) or "保护自身利益"
        known = len(context.get("known_characters", []))
        return f"以{role}身份行动，围绕{goals}做选择，并在与{known}个已知关系对象互动时保持人格一致。"

    @classmethod
    def _sanitize_goal_list(cls, goals: List[str], entity_type: str) -> List[str]:
        cleaned = []
        fallback = "维持组织稳定" if entity_type == "organization" else "查清当前局势"
        for goal in goals or []:
            text = cls._sanitize_runtime_fragment(goal, max_length=40)
            if text:
                cleaned.append(text)
        if not cleaned:
            cleaned.append(fallback)
        return cleaned[:3]

    @classmethod
    def _sanitize_belief_list(cls, beliefs: List[str]) -> List[str]:
        cleaned = []
        for belief in beliefs or []:
            text = cls._sanitize_runtime_fragment(belief, max_length=56)
            if text:
                cleaned.append(text)
        return cleaned[:4]

    @classmethod
    def _sanitize_runtime_fragment(cls, value: Any, max_length: int) -> str:
        text = CharacterRegistry._clean_text(value)
        if not text:
            return ""
        if "判断当前局势尚未明朗" in text:
            return ""
        if any(pattern in text for pattern in cls.EXPOSITION_HINTS):
            return ""
        if len(text) > max_length:
            return ""
        if re.search(r"[\u4e00-\u9fff]{2,6}(说|表示|告诉|发现|看见|看到)", text) and "你" not in text and "我" not in text:
            return ""
        return text[:max_length]

    @classmethod
    def _infer_speech_style(cls, character: Dict[str, Any]) -> str:
        name = character.get("canonical_name") or character.get("name") or ""
        if name in cls.NAME_STYLE_OVERRIDES:
            return cls.NAME_STYLE_OVERRIDES[name]
        text = " ".join([
            character.get("summary", ""),
            character.get("persona", ""),
            " ".join(character.get("traits", [])),
        ])
        if any(keyword in text for keyword in ["冷静", "理性", "稳重", "分析"]):
            return "冷静克制"
        if any(keyword in text for keyword in ["强硬", "锋利", "直接", "激烈"]):
            return "强硬直接"
        if any(keyword in text for keyword in ["谨慎", "沉默", "不露声色", "隐瞒"]):
            return "谨慎保留"
        return "自然克制"

    @classmethod
    def _infer_risk_profile(cls, character: Dict[str, Any], context: Dict[str, Any]) -> str:
        if len(context.get("secrets", [])) >= 2:
            return "高风险规避"
        if any(keyword in " ".join(character.get("traits", [])) for keyword in ["急迫", "冲动"]):
            return "高风险接受"
        return "中等风险偏好"

    @classmethod
    def _infer_value_guardrails(cls, character: Dict[str, Any]) -> List[str]:
        name = character.get("canonical_name") or character.get("name") or ""
        if name == "滑膛":
            return [
                "不到必要时，不把自己的判断说穿。",
                "先看人的反应，再决定动手还是开口。",
                "在证据不够前，不替任何人下最后结论。",
            ]
        if name == "许雪萍":
            return [
                "不会当着所有人的面把真话说满。",
                "先保自己，再决定帮谁。",
                "每一次提醒都要给自己留退路。",
            ]
        if name == "朱汉杨":
            return [
                "不会轻易承认自己已经落到被动。",
                "说话要保住场面上的控制感。",
                "即使让步，也要像是自己主动给出的。",
            ]
        values = ["不能无缘无故暴露自己不知道的秘密。"]
        if any(keyword in " ".join(character.get("traits", [])) for keyword in ["冷静", "理性", "调查者"]):
            values.append("在证据不足前，不轻易下结论。")
        if character.get("role_type") in {"core", "protagonist"}:
            values.append("优先维护自己的核心目标和关键关系。")
        return values[:3]

    @classmethod
    def step_agents(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        trigger_event: Optional[Dict[str, Any]],
    ) -> Dict[str, CharacterRuntimeState]:
        updated = {}
        valid_ids = set(CharacterRegistry.playable_ids(story_data))
        for char_id, state in story_data.get("runtime_agents", {}).items():
            if char_id not in valid_ids:
                continue
            if isinstance(state, dict):
                state = CharacterRuntimeState(**state)
            memory = list(state.memory)
            if trigger_event:
                event_title = cls._sanitize_runtime_fragment(trigger_event.get("title", ""), max_length=48)
                if event_title:
                    memory.insert(0, f"见证事件：{event_title}")
                state.last_action = f"响应事件《{trigger_event.get('title')}》"
                next_intent = cls._sanitize_runtime_fragment(trigger_event.get("summary", ""), max_length=40)
                if next_intent:
                    state.current_intent = next_intent
                if any(keyword in trigger_event.get("summary", "") for keyword in ["匿名", "秘密", "隐瞒"]):
                    state.secret_pressure = min(state.secret_pressure + 0.08, 1.0)
            state.memory = memory[:8]
            updated[char_id] = state
        return updated


class WorldStateEngine:
    PROTAGONIST_NAME_HINTS = ("滑膛",)

    @classmethod
    def initialize_world_state(cls, story_data: Dict[str, Any]) -> WorldState:
        CharacterRegistry.ensure(story_data)
        scenes = story_data.get("scenes", [])
        events = story_data.get("events", [])
        secrets = story_data.get("secrets", [])
        protagonist = cls._resolve_protagonist(story_data)

        scene_states = {
            scene["id"]: {
                "name": scene["name"],
                "status": "idle",
                "participants": scene.get("participants", []),
            }
            for scene in scenes
        }
        character_states = {
            character_id: {
                "name": (CharacterRegistry.get_entry(story_data, character_id) or {}).get("canonical_name") or "角色",
                "status": (CharacterRegistry.get_character(story_data, character_id) or {}).get("status", "active"),
                "focus": ((CharacterRegistry.get_character(story_data, character_id) or {}).get("goals") or ["观察局势"])[0],
            }
            for character_id in CharacterRegistry.playable_ids(story_data)
        }

        return WorldState(
            phase=(story_data.get("arcs") or [{}])[0].get("phase", "setup"),
            time_index=0,
            current_scene_id=scenes[0]["id"] if scenes else None,
            current_plot_node_id=(story_data.get("arcs") or [{}])[0].get("id"),
            triggered_event_ids=[],
            candidate_event_ids=[],
            unlocked_clue_ids=[],
            hidden_secret_ids=[item["id"] for item in secrets if not item.get("exposed")],
            public_information=[story_data.get("main_storyline", "")] if story_data.get("main_storyline") else [],
            private_information={},
            player_state={
                "id": "player",
                "role": "protagonist",
                "protagonist_id": protagonist.get("id") if protagonist else None,
                "protagonist_name": protagonist.get("canonical_name") or protagonist.get("name") if protagonist else "你",
                "known_clues": [],
                "targets": [],
            },
            character_states=character_states,
            scene_states=scene_states,
            relationship_tension={},
            pending_tasks=[],
            pending_decision=None,
            debug_log=["NarraWorld 世界已初始化"],
        )

    @classmethod
    def apply_event(cls, story_data: Dict[str, Any], world_state: WorldState, event_id: str) -> Dict[str, Any]:
        event = next((item for item in story_data.get("events", []) if item["id"] == event_id), None)
        if not event:
            return {"success": False, "message": f"事件不存在: {event_id}"}

        if event_id not in world_state.triggered_event_ids:
            world_state.triggered_event_ids.append(event_id)

        world_state.time_index += 1
        world_state.current_event_id = event_id
        world_state.phase = cls._derive_phase(story_data, world_state)
        if event.get("scenes"):
            world_state.current_scene_id = event["scenes"][0]
        for participant in event.get("participants", []):
            if participant in world_state.character_states:
                world_state.character_states[participant]["status"] = "engaged"
                world_state.character_states[participant]["focus"] = event["title"]
        for clue_id in event.get("clues", []):
            if clue_id not in world_state.unlocked_clue_ids:
                world_state.unlocked_clue_ids.append(clue_id)
        for consequence in event.get("consequences", []):
            if consequence not in world_state.public_information:
                world_state.public_information.append(consequence)
        world_state.debug_log.append(f"T{world_state.time_index}: 触发事件《{event['title']}》")

        runtime_agents = CharacterAgentRuntimeService.step_agents(story_data, world_state, event)
        story_data["runtime_agents"] = {k: vars(v) for k, v in runtime_agents.items()}
        story_data["world_state"] = vars(world_state)
        PlayEventQueue.sync_after_event(story_data, world_state, event_id, reason="event_applied")
        story_data["graph"] = NarrativeGraphService.apply_runtime_update(story_data, event=event)
        return {"success": True, "event": event, "world_state": world_state}

    @classmethod
    def _resolve_protagonist(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        CharacterRegistry.ensure(story_data)
        characters = [
            item
            for item_id in CharacterRegistry.playable_ids(story_data)
            for item in [CharacterRegistry.get_character(story_data, item_id, require_playable=True)]
            if item
        ]
        ranked = sorted(characters, key=cls._protagonist_score, reverse=True)
        return ranked[0] if ranked else {}

    @classmethod
    def _protagonist_score(cls, item: Dict[str, Any]) -> tuple:
        name = item.get("canonical_name") or item.get("name") or ""
        role_type = item.get("role_type", "")
        return (
            any(name == hint for hint in cls.PROTAGONIST_NAME_HINTS),
            role_type == "protagonist",
            role_type in {"core", "major"},
            float(item.get("importance_score", 0.0)),
        )

    @classmethod
    def _derive_phase(cls, story_data: Dict[str, Any], world_state: WorldState) -> str:
        total = max(len(story_data.get("events", [])), 1)
        ratio = len(world_state.triggered_event_ids) / total
        if ratio < 0.25:
            return "setup"
        if ratio < 0.6:
            return "confrontation"
        if ratio < 0.85:
            return "climax"
        return "resolution"


class PlayEventQueue:
    VALID_STATUSES = {"pending", "active", "consumed", "skipped", "compressed"}

    @classmethod
    def ensure(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        play_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        play_state = play_state if play_state is not None else story_data.setdefault("play_state", {})
        queue = cls._build_queue(story_data, world_state, play_state)
        play_state["event_queue"] = queue
        world_state.candidate_event_ids = cls.derive_candidate_event_ids(queue)
        story_data["play_state"] = play_state
        story_data["world_state"] = world_state.__dict__
        return queue

    @classmethod
    def get_active_entry(cls, play_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return next((item for item in play_state.get("event_queue", []) if item.get("status") == "active"), None)

    @classmethod
    def peek_next_entry(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        play_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        queue = cls.ensure(story_data, world_state, play_state)
        return next((item for item in queue if item.get("status") == "pending"), None)

    @classmethod
    def mark_turn_generated(cls, play_state: Dict[str, Any], event_id: Optional[str]) -> None:
        if not event_id:
            return
        for entry in play_state.get("event_queue", []):
            if entry.get("event_id") == event_id:
                entry["turn_generated"] = True
                if entry.get("status") == "pending":
                    entry["status"] = "active"
                break

    @classmethod
    def sync_after_event(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        event_id: str,
        reason: str = "",
    ) -> None:
        play_state = story_data.setdefault("play_state", {})
        queue = cls.ensure(story_data, world_state, play_state)
        for entry in queue:
            if entry.get("status") == "active" and entry.get("event_id") != event_id:
                entry["status"] = "consumed"
            if entry.get("event_id") == event_id:
                entry["status"] = "active"
                entry["debug_reason"] = cls._join_reason(entry.get("debug_reason", ""), reason or "当前推进事件")
        play_state["event_queue"] = cls._sort_queue(queue)
        world_state.candidate_event_ids = cls.derive_candidate_event_ids(play_state["event_queue"])
        story_data["play_state"] = play_state
        story_data["world_state"] = world_state.__dict__

    @classmethod
    def rebalance_after_player_action(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        play_state: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        context = context or {}
        queue = cls.ensure(story_data, world_state, play_state)
        events_by_id = {item["id"]: item for item in story_data.get("events", [])}
        target_ids = set((world_state.player_state or {}).get("targets", []))
        clue_ids = set(world_state.unlocked_clue_ids or [])
        scene_id = world_state.current_scene_id
        action_type = context.get("action_type") or context.get("intent") or "player_action"

        for entry in queue:
            if entry.get("status") != "pending":
                continue
            event = events_by_id.get(entry.get("event_id"))
            if not event:
                continue
            priority, reason, debug_reason = cls._priority_payload(story_data, world_state, event)
            if target_ids and target_ids.intersection(set(event.get("participants", []))):
                priority += 0.18
                debug_reason = cls._join_reason(debug_reason, "玩家目标涉及该事件角色")
            if clue_ids and clue_ids.intersection(set(event.get("clues", []))):
                priority += 0.12
                debug_reason = cls._join_reason(debug_reason, "玩家已掌握相关线索")
            if scene_id and scene_id in (event.get("scenes") or []):
                priority += 0.05
                debug_reason = cls._join_reason(debug_reason, "当前场景关联")
            if action_type == "advance_story":
                priority += 0.3
                debug_reason = cls._join_reason(debug_reason, "玩家主动要求推进")
            entry["priority"] = round(priority, 2)
            entry["reason"] = reason
            entry["debug_reason"] = debug_reason

        active_event_id = ((play_state.get("current_turn") or {}).get("event_id")) or world_state.current_event_id
        if active_event_id:
            for entry in queue:
                if entry.get("event_id") == active_event_id:
                    entry["debug_reason"] = cls._join_reason(entry.get("debug_reason", ""), f"玩家动作后重排：{action_type}")
                    break

        play_state["event_queue"] = cls._sort_queue(queue)
        world_state.candidate_event_ids = cls.derive_candidate_event_ids(play_state["event_queue"])
        story_data["play_state"] = play_state
        story_data["world_state"] = world_state.__dict__
        return play_state["event_queue"]

    @classmethod
    def derive_candidate_event_ids(cls, queue: List[Dict[str, Any]]) -> List[str]:
        return [item["event_id"] for item in queue if item.get("status") == "pending"][:6]

    @classmethod
    def _build_queue(cls, story_data: Dict[str, Any], world_state: WorldState, play_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = story_data.get("events", []) or []
        existing = {
            item.get("event_id"): item
            for item in (play_state.get("event_queue") or [])
            if item.get("event_id")
        }
        generated_turn_ids = {
            item.get("event_id")
            for item in [play_state.get("current_turn") or {}, *(play_state.get("turn_history") or [])]
            if item.get("event_id")
        }
        current_event_id = (
            (play_state.get("current_turn") or {}).get("event_id")
            or world_state.current_event_id
            or next((item.get("event_id") for item in existing.values() if item.get("status") == "active"), None)
        )

        queue = []
        for event in events:
            event_id = event["id"]
            previous = existing.get(event_id, {})
            priority, reason, debug_reason = cls._priority_payload(story_data, world_state, event)
            status = cls._normalize_status(previous.get("status", "pending"))
            if event_id == current_event_id:
                status = "active"
            elif event_id in world_state.triggered_event_ids:
                status = "consumed"
            elif status not in {"skipped", "compressed"}:
                status = "pending"

            queue.append({
                "event_id": event_id,
                "status": status,
                "importance": cls._importance(event),
                "source_block_id": cls._source_block_id(story_data, event_id),
                "reason": reason,
                "debug_reason": debug_reason,
                "turn_generated": bool(previous.get("turn_generated")) or event_id in generated_turn_ids,
                "priority": round(previous.get("priority", priority) if status in {"skipped", "compressed"} else priority, 2),
                "order": event.get("order", 0),
            })

        return cls._sort_queue(queue)

    @classmethod
    def _priority_payload(cls, story_data: Dict[str, Any], world_state: WorldState, event: Dict[str, Any]) -> tuple[float, str, str]:
        unresolved = NarrativeGraphService.get_unresolved_threads(story_data)
        hidden_secrets = len(unresolved.get("hidden_secrets", []))
        priority = 0.45
        reasons = ["基础推进优先级"]
        if event.get("order", 0) <= len(world_state.triggered_event_ids) + 2:
            priority += 0.25
            reasons.append("顺序接近当前进度")
        if "main" in event.get("tags", []):
            priority += 0.2
            reasons.append("主线事件")
        if event.get("is_key_node"):
            priority += 0.15
            reasons.append("关键节点")
        if hidden_secrets and event.get("clues"):
            priority += 0.08
            reasons.append("可承接隐藏秘密")
        return round(priority, 2), cls._reason_label(event), "；".join(reasons)

    @classmethod
    def _reason_label(cls, event: Dict[str, Any]) -> str:
        if event.get("is_key_node"):
            return "关键剧情节点"
        if "main" in event.get("tags", []):
            return "主线事件"
        if event.get("event_type") == "transition":
            return "过渡事件"
        if event.get("event_type") == "background":
            return "背景推进"
        return "待推进事件"

    @classmethod
    def _importance(cls, event: Dict[str, Any]) -> str:
        if event.get("is_key_node") or "main" in event.get("tags", []):
            return "major"
        if event.get("event_type") == "transition":
            return "transition"
        if event.get("event_type") == "background":
            return "background"
        return "minor"

    @classmethod
    def _source_block_id(cls, story_data: Dict[str, Any], event_id: str) -> Optional[str]:
        for block in story_data.get("narrative_blocks", []) or []:
            if event_id in (block.get("event_ids") or []):
                return block.get("id")
        return None

    @classmethod
    def _sort_queue(cls, queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        status_rank = {
            "active": 0,
            "pending": 1,
            "consumed": 2,
            "skipped": 3,
            "compressed": 4,
        }
        return sorted(
            queue,
            key=lambda item: (
                status_rank.get(item.get("status", "pending"), 9),
                -float(item.get("priority", 0.0)) if item.get("status") == "pending" else 0,
                item.get("order", 0),
            ),
        )

    @classmethod
    def _normalize_status(cls, status: str) -> str:
        cleaned = str(status or "pending").strip().lower()
        return cleaned if cleaned in cls.VALID_STATUSES else "pending"

    @classmethod
    def _join_reason(cls, current: str, extra: str) -> str:
        parts = [item for item in [current, extra] if item]
        return "；".join(dict.fromkeys(parts))


class NarrativePlanner:
    @classmethod
    def get_candidate_events(cls, story_data: Dict[str, Any], world_state: WorldState) -> List[Dict[str, Any]]:
        queue = PlayEventQueue.ensure(story_data, world_state, story_data.setdefault("play_state", {}))
        events_by_id = {item["id"]: item for item in story_data.get("events", [])}
        candidates = []
        for entry in queue:
            if entry.get("status") != "pending":
                continue
            event = events_by_id.get(entry.get("event_id"))
            if not event:
                continue
            candidates.append(
                {
                    "event_id": event["id"],
                    "title": event["title"],
                    "summary": event.get("summary", ""),
                    "priority": round(float(entry.get("priority", 0.0)), 2),
                    "anchor": "mainline" if entry.get("importance") == "major" else "side",
                    "is_key_node": event.get("is_key_node", False),
                    "status": entry.get("status"),
                    "importance": entry.get("importance"),
                    "source_block_id": entry.get("source_block_id"),
                    "reason": entry.get("reason", ""),
                    "debug_reason": entry.get("debug_reason", ""),
                    "turn_generated": bool(entry.get("turn_generated")),
                }
            )
        return candidates[:6]

    @classmethod
    def choose_next_event(cls, story_data: Dict[str, Any], world_state: WorldState) -> Optional[Dict[str, Any]]:
        next_entry = PlayEventQueue.peek_next_entry(story_data, world_state, story_data.setdefault("play_state", {}))
        if not next_entry:
            return None
        chosen_id = next_entry["event_id"]
        return next((event for event in story_data.get("events", []) if event["id"] == chosen_id), None)


class PlayerInteractionService:
    @classmethod
    def parse_input(cls, story_data: Dict[str, Any], text: str) -> Dict[str, Any]:
        lowered = (text or "").strip().lower()
        if not lowered:
            return {"intent": "observe", "targets": []}

        if any(keyword in lowered for keyword in ["关系", "relationship", "trust", "love"]):
            return {"intent": "inspect_relationships", "targets": []}
        if any(keyword in lowered for keyword in ["线索", "clue", "secret", "秘密"]):
            return {"intent": "inspect_clues", "targets": []}
        if any(keyword in lowered for keyword in ["主线", "storyline", "arc", "剧情"]):
            return {"intent": "inspect_storyline", "targets": []}
        if any(keyword in lowered for keyword in ["推进", "advance", "继续", "trigger"]):
            return {"intent": "advance_story", "targets": []}

        registry = CharacterRegistry.ensure(story_data)
        alias_map = registry.get("alias_map") or {}
        for alias, character_id in alias_map.items():
            if alias and alias in lowered:
                target = CharacterRegistry.get_character(story_data, character_id, require_playable=True)
                if target:
                    return {"intent": "intervene_character", "targets": [character_id]}
        return {"intent": "freeform", "targets": [], "raw": text}

    @classmethod
    def execute(cls, story_data: Dict[str, Any], world_state: WorldState, player_input: str) -> Dict[str, Any]:
        parsed = cls.parse_input(story_data, player_input)
        intent = parsed["intent"]

        if intent == "inspect_relationships":
            return {
                "intent": intent,
                "message": "你重新梳理了人物之间的站位。",
                "data": story_data.get("relationships", [])[:20],
            }
        if intent == "inspect_clues":
            return {
                "intent": intent,
                "message": "你把目前掌握的线索重新摆到台面上。",
                "data": {
                    "clues": story_data.get("clues", []),
                    "secrets": story_data.get("secrets", []),
                },
            }
        if intent == "inspect_storyline":
            return {
                "intent": intent,
                "message": "你重新确认了当前的主线脉络。",
                "data": {
                    "main_storyline": story_data.get("main_storyline", ""),
                    "arcs": story_data.get("arcs", []),
                },
            }
        if intent == "intervene_character":
            target = parsed["targets"][0]
            character = next((item for item in story_data.get("characters", []) if item["id"] == target), None)
            target_name = (character or {}).get("canonical_name") or (character or {}).get("name") or "这个人"
            world_state.player_state["targets"] = [target]
            world_state.debug_log.append(f"玩家选择介入角色 {target}")
            NarrativeGraphService.record_player_action(story_data, player_input, world_state.__dict__)
            return {
                "intent": intent,
                "message": f"你决定把注意力转向{target_name}。",
                "data": {**world_state.player_state, "target_name": target_name},
            }
        if intent == "advance_story":
            next_event = NarrativePlanner.choose_next_event(story_data, world_state)
            if not next_event:
                return {"intent": intent, "message": "主线已基本收束，暂无新的候选事件。", "data": None}
            return {
                "intent": intent,
                "message": "你决定继续往前走。",
                "data": {"next_event_id": next_event["id"]},
            }

        NarrativeGraphService.record_player_action(story_data, player_input, world_state.__dict__)
        return {
            "intent": intent,
            "message": "世界记住了你的这句话。",
            "data": {"raw": player_input},
        }


class ContinuationEngine:
    @classmethod
    def generate(cls, story_data: Dict[str, Any], world_state: WorldState) -> Dict[str, Any]:
        unresolved = NarrativeGraphService.get_unresolved_threads(story_data)
        lead_characters = [char.get("canonical_name") or char["name"] for char in story_data.get("characters", [])[:3]]
        protagonist_name = (world_state.player_state or {}).get("protagonist_name") or "你"
        narrative_blocks = story_data.get("narrative_blocks", []) or []
        hidden_secrets = unresolved.get("hidden_secrets", [])
        conflict_edges = unresolved.get("relationship_tension", [])
        pending_events = unresolved.get("pending_events", [])
        latest_blocks = narrative_blocks[-2:] if narrative_blocks else []

        next_arc_title = f"{story_data.get('title', '故事')}：下一篇章"
        new_conflicts = [
            f"未解决秘密《{secret['label']}》继续压迫当前关系网络"
            for secret in hidden_secrets[:2]
        ]
        new_conflicts.extend(
            [edge.get("summary", "旧冲突仍在发酵") for edge in conflict_edges[:2]]
        )
        if not new_conflicts:
            new_conflicts = ["旧秩序崩塌后，各方争夺新的叙事控制权。"]

        next_blocks = cls._next_narrative_blocks(
            protagonist_name=protagonist_name,
            latest_blocks=latest_blocks,
            new_conflicts=new_conflicts,
            pending_events=pending_events,
        )

        return {
            "next_chapter_overview": (
                f"{next_arc_title} 将继承当前世界状态，继续让{protagonist_name}站在局势中心，围绕"
                f"{'、'.join(lead_characters) or '核心角色'}的下一轮选择展开。"
            ),
            "new_conflicts": new_conflicts,
            "new_tasks": [
                "追踪未公开的关键秘密",
                "决定是否公开某条核心线索",
                "处理玩家介入造成的关系偏移",
            ],
            "new_event_chain": [item["label"] for item in pending_events[:3]] or ["余波调查", "阵营重组", "下一轮冲突爆发"],
            "next_narrative_blocks": next_blocks,
            "continuation_world_state": {
                "phase": "continuation_setup",
                "inherits_triggered_events": list(world_state.triggered_event_ids),
                "player_targets": world_state.player_state.get("targets", []),
            },
        }

    @classmethod
    def _next_narrative_blocks(
        cls,
        protagonist_name: str,
        latest_blocks: List[Dict[str, Any]],
        new_conflicts: List[str],
        pending_events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        blocks = []
        seeds = latest_blocks or [{}]
        for idx in range(3):
            seed = seeds[min(idx, len(seeds) - 1)] if seeds else {}
            conflict = new_conflicts[min(idx, len(new_conflicts) - 1)] if new_conflicts else "新的冲突正在形成。"
            pending = pending_events[idx]["label"] if idx < len(pending_events) else "新的力量开始重新布子"
            blocks.append({
                "id": f"continuation_block_{idx + 1}",
                "title": seed.get("title") or f"下一篇章回合 {idx + 1}",
                "situation": seed.get("situation") or f"{protagonist_name}刚从上一轮余波里站稳，新局势已经逼近。",
                "conflict": conflict,
                "objective": f"围绕“{pending}”重新判断谁值得接近，谁值得提防。",
                "risk": "你沿用上一轮的判断，很可能正落入别人准备好的惯性陷阱。",
            })
        return blocks
