"""
NarraWorld 叙事图谱服务
提供 Zep 风格的统一图谱 schema、过滤视图和运行时双写能力。
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from ..models.story import EvidenceRef, NarrativeGraph, StoryEdge, StoryNode


class NarrativeGraphService:
    NODE_TYPES = [
        "Character",
        "Event",
        "Scene",
        "Location",
        "Faction",
        "Item",
        "Clue",
        "Secret",
        "Arc",
        "WorldRule",
        "PlayerAction",
    ]
    EDGE_TYPES = [
        "KNOWS",
        "TRUSTS",
        "HATES",
        "LOVES",
        "ALLIES_WITH",
        "HIDES_FROM",
        "CAUSED_BY",
        "LEADS_TO",
        "APPEARS_IN",
        "LOCATED_IN",
        "BELONGS_TO",
        "POSSESSES",
        "PURSUES",
        "REVEALS",
        "CONFLICTS_WITH",
    ]
    EDGE_MAP = {item.lower(): item for item in EDGE_TYPES}
    EDGE_MAP.update(
        {
            "causes": "LEADS_TO",
            "appears_in": "APPEARS_IN",
            "belongs_to": "BELONGS_TO",
            "locates_in": "LOCATED_IN",
            "hides_from": "HIDES_FROM",
            "trusts": "TRUSTS",
            "loves": "LOVES",
            "hates": "HATES",
            "knows": "KNOWS",
            "possesses": "POSSESSES",
            "seeks": "PURSUES",
            "conflicts_with": "CONFLICTS_WITH",
            "caused_by": "CAUSED_BY",
            "leads_to": "LEADS_TO",
            "reveals": "REVEALS",
            "allies_with": "ALLIES_WITH",
        }
    )

    @classmethod
    def build_graph(cls, story_data: Dict[str, Any]) -> NarrativeGraph:
        graph = NarrativeGraph(
            node_types=list(cls.NODE_TYPES),
            edge_types=list(cls.EDGE_TYPES),
        )

        nodes_by_id: Dict[str, StoryNode] = {}
        edges: List[StoryEdge] = []

        def add_node(node: StoryNode):
            if node.id not in nodes_by_id:
                nodes_by_id[node.id] = node

        def add_edge(edge: StoryEdge):
            edges.append(edge)

        for character in story_data.get("characters", []):
            add_node(
                StoryNode(
                    id=character["id"],
                    label=character.get("canonical_name") or character["name"],
                    type="Character",
                    summary=character.get("summary") or character.get("persona", ""),
                    metadata={
                        "aliases": character.get("aliases", []),
                        "role": character.get("role", ""),
                        "role_type": character.get("role_type", ""),
                        "goals": character.get("goals", []),
                        "motivation": character.get("motivation", ""),
                        "hidden_info": character.get("hidden_info", []),
                        "importance_score": character.get("importance_score", 0.5),
                        "evidence": character.get("evidence", []),
                    },
                )
            )

        for event in story_data.get("events", []):
            add_node(
                StoryNode(
                    id=event["id"],
                    label=event["title"],
                    type="Event",
                    summary=event.get("summary", ""),
                    status=event.get("status", "pending"),
                    metadata={
                        "order": event.get("order", 0),
                        "event_type": event.get("event_type", "plot"),
                        "trigger_conditions": event.get("trigger_conditions", []),
                        "preconditions": event.get("preconditions", []),
                        "outcomes": event.get("outcomes", []),
                        "is_key_node": event.get("is_key_node", False),
                        "evidence": event.get("evidence", []),
                    },
                )
            )

        for scene in story_data.get("scenes", []):
            add_node(
                StoryNode(
                    id=scene["id"],
                    label=scene["name"],
                    type="Scene",
                    summary=scene.get("summary", ""),
                    metadata={
                        "mood": scene.get("mood", ""),
                        "participants": scene.get("participants", []),
                        "evidence": scene.get("evidence", []),
                    },
                )
            )
            if scene.get("location"):
                location_id = f"location:{scene['location']}"
                add_node(
                    StoryNode(
                        id=location_id,
                        label=scene["location"],
                        type="Location",
                        summary="故事空间锚点",
                    )
                )
                add_edge(
                    StoryEdge(
                        source=scene["id"],
                        target=location_id,
                        type="LOCATED_IN",
                        summary="场景位于地点",
                        weight=0.7,
                    )
                )

        for clue in story_data.get("clues", []):
            add_node(
                StoryNode(
                    id=clue["id"],
                    label=clue["title"],
                    type="Clue",
                    summary=clue.get("summary", ""),
                    metadata={"evidence": clue.get("evidence", [])},
                )
            )

        for secret in story_data.get("secrets", []):
            add_node(
                StoryNode(
                    id=secret["id"],
                    label=secret["title"],
                    type="Secret",
                    summary=secret.get("summary", ""),
                    status="revealed" if secret.get("exposed") else "hidden",
                    metadata={"evidence": secret.get("evidence", [])},
                )
            )

        for arc in story_data.get("arcs", []):
            add_node(
                StoryNode(
                    id=arc["id"],
                    label=arc["title"],
                    type="Arc",
                    summary=arc.get("summary", ""),
                    metadata={"phase": arc.get("phase", "setup")},
                )
            )

        for rule in story_data.get("world_rules", []):
            add_node(
                StoryNode(
                    id=rule["id"],
                    label=rule["rule"][:30],
                    type="WorldRule",
                    summary=rule.get("implication", ""),
                    metadata={"rule": rule.get("rule", ""), "evidence": rule.get("evidence", [])},
                )
            )

        for rel in story_data.get("relationships", []):
            add_edge(
                StoryEdge(
                    source=rel["source"],
                    target=rel["target"],
                    type=cls._normalize_edge_type(rel.get("relation")),
                    summary=rel.get("summary", ""),
                    weight=float(rel.get("strength", 0.5)),
                    evidence=cls._hydrate_evidence(rel.get("evidence", [])),
                )
            )

        for event in story_data.get("events", []):
            for participant in event.get("participants", []):
                add_edge(
                    StoryEdge(
                        source=participant,
                        target=event["id"],
                        type="APPEARS_IN",
                        summary="角色参与事件",
                        weight=0.7,
                        evidence=cls._hydrate_evidence(event.get("evidence", [])),
                    )
                )
            for scene_id in event.get("scenes", []):
                add_edge(
                    StoryEdge(
                        source=event["id"],
                        target=scene_id,
                        type="APPEARS_IN",
                        summary="事件发生于场景",
                        weight=0.65,
                    )
                )
            for clue_id in event.get("clues", []):
                add_edge(
                    StoryEdge(
                        source=event["id"],
                        target=clue_id,
                        type="REVEALS",
                        summary="事件暴露线索",
                        weight=0.65,
                    )
                )
            for event_id in event.get("caused_by", []):
                add_edge(
                    StoryEdge(
                        source=event["id"],
                        target=event_id,
                        type="CAUSED_BY",
                        summary="事件有明确前因",
                        weight=0.7,
                    )
                )
            for event_id in event.get("leads_to", []):
                add_edge(
                    StoryEdge(
                        source=event["id"],
                        target=event_id,
                        type="LEADS_TO",
                        summary="事件推动后续发展",
                        weight=0.7,
                    )
                )

        for clue in story_data.get("clues", []):
            for holder in clue.get("holders", []):
                add_edge(
                    StoryEdge(
                        source=holder,
                        target=clue["id"],
                        type="POSSESSES",
                        summary="角色持有线索",
                        weight=0.68,
                        evidence=cls._hydrate_evidence(clue.get("evidence", [])),
                    )
                )
            for event_id in clue.get("related_events", []):
                add_edge(
                    StoryEdge(
                        source=clue["id"],
                        target=event_id,
                        type="REVEALS",
                        summary="线索揭示相关事件",
                        weight=0.58,
                    )
                )

        for secret in story_data.get("secrets", []):
            for holder in secret.get("holders", []):
                add_edge(
                    StoryEdge(
                        source=holder,
                        target=secret["id"],
                        type="HIDES_FROM",
                        summary="角色掌握隐秘信息",
                        weight=0.78,
                    )
                )
            for clue_id in secret.get("related_clues", []):
                add_edge(
                    StoryEdge(
                        source=clue_id,
                        target=secret["id"],
                        type="REVEALS",
                        summary="线索可揭开秘密",
                        weight=0.62,
                    )
                )

        for arc in story_data.get("arcs", []):
            for event_id in arc.get("events", []):
                add_edge(
                    StoryEdge(
                        source=event_id,
                        target=arc["id"],
                        type="BELONGS_TO",
                        summary="事件属于章节主线",
                        weight=0.8,
                    )
                )

        for rule in story_data.get("world_rules", []):
            for character in story_data.get("characters", [])[:5]:
                add_edge(
                    StoryEdge(
                        source=character["id"],
                        target=rule["id"],
                        type="BELONGS_TO",
                        summary="角色受到世界规则约束",
                        weight=0.35,
                    )
                )

        graph.nodes = list(nodes_by_id.values())
        graph.edges = edges
        return graph

    @classmethod
    def get_character_context(cls, story_data: Dict[str, Any], character_id: str) -> Dict[str, Any]:
        graph = story_data.get("graph", {})
        nodes = {node["id"]: node for node in graph.get("nodes", [])}
        edges = [
            edge for edge in graph.get("edges", [])
            if edge.get("source") == character_id or edge.get("target") == character_id
        ]
        return {
            "node": nodes.get(character_id),
            "edges": edges,
            "known_characters": [edge.get("target") for edge in edges if edge.get("source") == character_id],
            "secrets": [edge.get("target") for edge in edges if edge.get("type") == "HIDES_FROM"],
            "goals": (nodes.get(character_id) or {}).get("metadata", {}).get("goals", []),
        }

    @classmethod
    def get_unresolved_threads(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        graph = story_data.get("graph", {})
        secret_nodes = [node for node in graph.get("nodes", []) if node.get("type") == "Secret" and node.get("status") != "revealed"]
        conflict_edges = [edge for edge in graph.get("edges", []) if edge.get("type") in {"CONFLICTS_WITH", "HATES", "HIDES_FROM"}]
        pending_events = [
            node for node in graph.get("nodes", [])
            if node.get("type") == "Event" and node.get("status") != "completed"
        ]
        return {
            "hidden_secrets": secret_nodes,
            "relationship_tension": conflict_edges,
            "pending_events": pending_events,
        }

    @classmethod
    def record_player_action(cls, story_data: Dict[str, Any], action_text: str, world_state: Dict[str, Any]) -> Dict[str, Any]:
        graph = cls._ensure_graph(story_data)
        action_id = f"player_action_{world_state.get('time_index', 0)}_{len(graph['nodes']) + 1}"
        graph["nodes"].append({
            "id": action_id,
            "label": action_text[:48],
            "type": "PlayerAction",
            "summary": action_text,
            "status": "recorded",
            "highlighted": True,
            "metadata": {
                "time_index": world_state.get("time_index", 0),
                "player_targets": world_state.get("player_state", {}).get("targets", []),
            },
        })
        for target in world_state.get("player_state", {}).get("targets", []):
            graph["edges"].append({
                "source": action_id,
                "target": target,
                "type": "PURSUES",
                "summary": "玩家将注意力聚焦于该目标",
                "weight": 0.6,
                "evidence": [],
            })
        return graph

    @classmethod
    def _world_state_dict(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        world_state = story_data.get("world_state") or {}
        if isinstance(world_state, dict):
            return world_state
        if is_dataclass(world_state):
            return asdict(world_state)
        if hasattr(world_state, "__dict__"):
            return dict(vars(world_state))
        return {}

    @classmethod
    def apply_runtime_update(
        cls,
        story_data: Dict[str, Any],
        event: Optional[Dict[str, Any]] = None,
        player_action: Optional[str] = None,
    ) -> Dict[str, Any]:
        graph = cls._ensure_graph(story_data)
        world_state = cls._world_state_dict(story_data)
        current_scene = world_state.get("current_scene_id")
        current_event = world_state.get("current_event_id")
        triggered_ids = set(world_state.get("triggered_event_ids", []))
        unlocked_clue_ids = set(world_state.get("unlocked_clue_ids", []))

        for node in graph["nodes"]:
            node["highlighted"] = node["id"] in {current_scene, current_event}
            if node["type"] == "Event":
                node["status"] = "completed" if node["id"] in triggered_ids else node.get("status", "pending")
            if node["type"] == "Clue" and node["id"] in unlocked_clue_ids:
                node["status"] = "unlocked"

        if event:
            participants = event.get("participants", [])
            for idx, source in enumerate(participants):
                for target in participants[idx + 1:]:
                    graph["edges"].append({
                        "source": source,
                        "target": target,
                        "type": "KNOWS",
                        "summary": f"因事件《{event['title']}》产生新的交互张力",
                        "weight": 0.42,
                        "evidence": event.get("evidence", []),
                    })

        if player_action:
            cls.record_player_action(story_data, player_action, world_state)
        return graph

    @classmethod
    def get_filtered_view(
        cls,
        story_data: Dict[str, Any],
        view: str = "all",
        node_types: Optional[Iterable[str]] = None,
        focus_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        graph = cls._ensure_graph(story_data)
        selected_types = set(node_types or [])
        focus_set = set(focus_ids or [])

        def edge_allowed(edge: Dict[str, Any]) -> bool:
            if view == "relationships":
                return edge.get("type") in {"KNOWS", "TRUSTS", "HATES", "LOVES", "ALLIES_WITH", "HIDES_FROM", "CONFLICTS_WITH"}
            if view == "causality":
                return edge.get("type") in {"CAUSED_BY", "LEADS_TO", "REVEALS", "PURSUES"}
            return True

        nodes = graph.get("nodes", [])
        edges = [edge for edge in graph.get("edges", []) if edge_allowed(edge)]

        if selected_types:
            nodes = [node for node in nodes if node.get("type") in selected_types]
            node_ids = {node["id"] for node in nodes}
            edges = [edge for edge in edges if edge.get("source") in node_ids and edge.get("target") in node_ids]

        if focus_set:
            related_ids: Set[str] = set(focus_set)
            for edge in edges:
                if edge.get("source") in focus_set or edge.get("target") in focus_set:
                    related_ids.add(edge.get("source"))
                    related_ids.add(edge.get("target"))
            nodes = [node for node in nodes if node.get("id") in related_ids]
            edges = [edge for edge in edges if edge.get("source") in related_ids and edge.get("target") in related_ids]

        return {
            "schema_version": graph.get("schema_version", "narraworld-zep-v1"),
            "node_types": graph.get("node_types", cls.NODE_TYPES),
            "edge_types": graph.get("edge_types", cls.EDGE_TYPES),
            "view": view,
            "nodes": nodes,
            "edges": edges,
        }

    @classmethod
    def _normalize_edge_type(cls, relation: Optional[str]) -> str:
        if not relation:
            return "KNOWS"
        return cls.EDGE_MAP.get(relation.strip().lower(), relation.strip().upper())

    @classmethod
    def _hydrate_evidence(cls, items: List[Dict[str, Any]]) -> List[EvidenceRef]:
        hydrated = []
        for item in items:
            if isinstance(item, dict):
                hydrated.append(EvidenceRef(**{
                    "quote": item.get("quote", ""),
                    "source": item.get("source", ""),
                    "chunk_index": item.get("chunk_index"),
                    "note": item.get("note", ""),
                }))
        return hydrated

    @classmethod
    def _ensure_graph(cls, story_data: Dict[str, Any]) -> Dict[str, Any]:
        graph = story_data.get("graph")
        if not graph:
            story_data["graph"] = asdict(cls.build_graph(story_data))
            graph = story_data["graph"]
        return graph
