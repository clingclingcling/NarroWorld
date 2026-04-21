"""
世界状态、角色运行时、玩家互动与续写服务
"""

from typing import Any, Dict, List, Optional

from ..models.story import CharacterRuntimeState, WorldState
from .story_graph import NarrativeGraphService


class CharacterAgentRuntimeService:
    @classmethod
    def bootstrap_agents(cls, story_data: Dict[str, Any]) -> Dict[str, CharacterRuntimeState]:
        runtime = {}
        for character in story_data.get("characters", []):
            context = NarrativeGraphService.get_character_context(story_data, character["id"])
            runtime[character["id"]] = CharacterRuntimeState(
                character_id=character["id"],
                persona=character.get("persona", "") or character.get("summary", ""),
                goals=character.get("goals", []),
                memory=cls._build_initial_memory(character, context),
                belief_state=character.get("beliefs", []),
                relationship_state={
                    edge.get("target"): edge.get("type")
                    for edge in context.get("edges", [])
                    if edge.get("source") == character["id"] and edge.get("target")
                },
                action_policy=cls._build_action_policy(character, context),
                knowledge_scope=character.get("knowledge_scope", []),
                current_intent=(character.get("goals") or ["观察局势"])[0],
                secret_pressure=min(len(context.get("secrets", [])) * 0.15, 1.0),
            )
        return runtime

    @classmethod
    def _build_initial_memory(cls, character: Dict[str, Any], context: Dict[str, Any]) -> List[str]:
        memory = [f"我是{character.get('canonical_name') or character.get('name')}。"]
        memory.extend(character.get("beliefs", [])[:2])
        for edge in context.get("edges", [])[:3]:
            target = edge.get("target")
            relation = edge.get("type")
            memory.append(f"我与 {target} 的关系是 {relation}。")
        return memory[:8]

    @classmethod
    def _build_action_policy(cls, character: Dict[str, Any], context: Dict[str, Any]) -> str:
        role = character.get("role") or character.get("role_type") or "角色"
        goals = "、".join(character.get("goals", [])[:3]) or "保护自身利益"
        known = len(context.get("known_characters", []))
        return f"以{role}身份行动，围绕{goals}做选择，并在与{known}个已知关系对象互动时保持人格一致。"

    @classmethod
    def step_agents(
        cls,
        story_data: Dict[str, Any],
        world_state: WorldState,
        trigger_event: Optional[Dict[str, Any]],
    ) -> Dict[str, CharacterRuntimeState]:
        updated = {}
        for char_id, state in story_data.get("runtime_agents", {}).items():
            if isinstance(state, dict):
                state = CharacterRuntimeState(**state)
            memory = list(state.memory)
            if trigger_event:
                memory.insert(0, f"见证事件：{trigger_event.get('title')}")
                state.last_action = f"响应事件《{trigger_event.get('title')}》"
                state.current_intent = trigger_event.get("summary", state.current_intent)[:80]
            state.memory = memory[:8]
            updated[char_id] = state
        return updated


class WorldStateEngine:
    @classmethod
    def initialize_world_state(cls, story_data: Dict[str, Any]) -> WorldState:
        scenes = story_data.get("scenes", [])
        events = story_data.get("events", [])
        characters = story_data.get("characters", [])
        secrets = story_data.get("secrets", [])

        scene_states = {
            scene["id"]: {
                "name": scene["name"],
                "status": "idle",
                "participants": scene.get("participants", []),
            }
            for scene in scenes
        }
        character_states = {
            character["id"]: {
                "name": character.get("canonical_name") or character["name"],
                "status": character.get("status", "active"),
                "focus": (character.get("goals") or ["观察局势"])[0],
            }
            for character in characters
        }

        return WorldState(
            phase=(story_data.get("arcs") or [{}])[0].get("phase", "setup"),
            time_index=0,
            current_scene_id=scenes[0]["id"] if scenes else None,
            current_plot_node_id=(story_data.get("arcs") or [{}])[0].get("id"),
            triggered_event_ids=[],
            candidate_event_ids=[event["id"] for event in events[:5]],
            unlocked_clue_ids=[],
            hidden_secret_ids=[item["id"] for item in secrets if not item.get("exposed")],
            public_information=[story_data.get("main_storyline", "")] if story_data.get("main_storyline") else [],
            private_information={},
            player_state={
                "id": "player",
                "role": "investigator",
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
        if event_id in world_state.candidate_event_ids:
            world_state.candidate_event_ids.remove(event_id)

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
        story_data["graph"] = NarrativeGraphService.apply_runtime_update(story_data, event=event)
        return {"success": True, "event": event, "world_state": world_state}

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


class NarrativePlanner:
    @classmethod
    def get_candidate_events(cls, story_data: Dict[str, Any], world_state: WorldState) -> List[Dict[str, Any]]:
        graph_threads = NarrativeGraphService.get_unresolved_threads(story_data)
        hidden_secrets = len(graph_threads.get("hidden_secrets", []))
        candidates = []
        for event in story_data.get("events", []):
            if event["id"] in world_state.triggered_event_ids:
                continue
            priority = 0.45
            if event.get("order", 0) <= len(world_state.triggered_event_ids) + 2:
                priority += 0.25
            if "main" in event.get("tags", []):
                priority += 0.2
            if event.get("is_key_node"):
                priority += 0.15
            if hidden_secrets and event.get("clues"):
                priority += 0.08
            candidates.append(
                {
                    "event_id": event["id"],
                    "title": event["title"],
                    "summary": event.get("summary", ""),
                    "priority": round(priority, 2),
                    "anchor": "mainline" if "main" in event.get("tags", []) else "side",
                    "is_key_node": event.get("is_key_node", False),
                }
            )
        candidates.sort(key=lambda item: item["priority"], reverse=True)
        return candidates[:6]

    @classmethod
    def choose_next_event(cls, story_data: Dict[str, Any], world_state: WorldState) -> Optional[Dict[str, Any]]:
        candidates = cls.get_candidate_events(story_data, world_state)
        if not candidates:
            return None
        chosen_id = candidates[0]["event_id"]
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

        for character in story_data.get("characters", []):
            names = {character.get("name", "").lower(), character.get("canonical_name", "").lower(), *[item.lower() for item in character.get("aliases", [])]}
            if any(name and name in lowered for name in names):
                return {"intent": "intervene_character", "targets": [character["id"]]}
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
            result = WorldStateEngine.apply_event(story_data, world_state, next_event["id"])
            return {
                "intent": intent,
                "message": "你决定继续往前走。",
                "data": result,
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
        hidden_secrets = unresolved.get("hidden_secrets", [])
        conflict_edges = unresolved.get("relationship_tension", [])
        pending_events = unresolved.get("pending_events", [])

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

        return {
            "next_chapter_overview": (
                f"{next_arc_title} 将继承当前世界状态，围绕"
                f"{'、'.join(lead_characters) or '核心角色'}的下一轮选择展开。"
            ),
            "new_conflicts": new_conflicts,
            "new_tasks": [
                "追踪未公开的关键秘密",
                "决定是否公开某条核心线索",
                "处理玩家介入造成的关系偏移",
            ],
            "new_event_chain": [item["label"] for item in pending_events[:3]] or ["余波调查", "阵营重组", "下一轮冲突爆发"],
            "continuation_world_state": {
                "phase": "continuation_setup",
                "inherits_triggered_events": list(world_state.triggered_event_ids),
                "player_targets": world_state.player_state.get("targets", []),
            },
        }
