"""
故事世界数据模型与持久化管理
"""

import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config


def _deep_convert(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: _deep_convert(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _deep_convert(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_convert(v) for v in value]
    return value


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class EvidenceRef:
    quote: str
    source: str = ""
    chunk_index: Optional[int] = None
    note: str = ""


@dataclass
class StoryCharacter:
    id: str
    name: str
    canonical_name: str = ""
    aliases: List[str] = field(default_factory=list)
    role: str = ""
    role_type: str = ""
    summary: str = ""
    persona: str = ""
    motivation: str = ""
    hidden_info: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    traits: List[str] = field(default_factory=list)
    secrets: List[str] = field(default_factory=list)
    beliefs: List[str] = field(default_factory=list)
    knowledge_scope: List[str] = field(default_factory=list)
    importance_score: float = 0.5
    status: str = "active"
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StoryRelationship:
    source: str
    target: str
    relation: str
    strength: float = 0.5
    summary: str = ""
    evidence: List[EvidenceRef] = field(default_factory=list)
    supporting_event_ids: List[str] = field(default_factory=list)


@dataclass
class StoryEvent:
    id: str
    title: str
    summary: str
    order: int
    event_type: str = "plot"
    participants: List[str] = field(default_factory=list)
    scenes: List[str] = field(default_factory=list)
    clues: List[str] = field(default_factory=list)
    status: str = "pending"
    trigger_conditions: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    consequences: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)
    caused_by: List[str] = field(default_factory=list)
    leads_to: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    is_key_node: bool = False
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StoryScene:
    id: str
    name: str
    location: str = ""
    summary: str = ""
    mood: str = ""
    participants: List[str] = field(default_factory=list)
    items: List[str] = field(default_factory=list)
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StoryClue:
    id: str
    title: str
    summary: str
    holders: List[str] = field(default_factory=list)
    related_events: List[str] = field(default_factory=list)
    visibility: str = "private"
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StorySecret:
    id: str
    title: str
    summary: str
    holders: List[str] = field(default_factory=list)
    exposed: bool = False
    related_clues: List[str] = field(default_factory=list)
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StoryArc:
    id: str
    title: str
    summary: str
    events: List[str] = field(default_factory=list)
    phase: str = "setup"
    key_node_event_ids: List[str] = field(default_factory=list)


@dataclass
class WorldRule:
    id: str
    rule: str
    implication: str = ""
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class StoryNode:
    id: str
    label: str
    type: str
    summary: str = ""
    status: str = "active"
    highlighted: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryEdge:
    source: str
    target: str
    type: str
    summary: str = ""
    weight: float = 0.5
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class NarrativeGraph:
    schema_version: str = "narraworld-zep-v1"
    node_types: List[str] = field(default_factory=list)
    edge_types: List[str] = field(default_factory=list)
    nodes: List[StoryNode] = field(default_factory=list)
    edges: List[StoryEdge] = field(default_factory=list)


@dataclass
class CharacterRuntimeState:
    character_id: str
    persona: str = ""
    goals: List[str] = field(default_factory=list)
    memory: List[str] = field(default_factory=list)
    belief_state: List[str] = field(default_factory=list)
    relationship_state: Dict[str, str] = field(default_factory=dict)
    action_policy: str = ""
    knowledge_scope: List[str] = field(default_factory=list)
    current_intent: str = ""
    last_action: str = ""
    secret_pressure: float = 0.0


@dataclass
class DecisionOption:
    id: str
    label: str
    impact: str = ""
    target_event_id: Optional[str] = None


@dataclass
class PlotNode:
    id: str
    title: str
    summary: str
    event_id: Optional[str] = None
    required: bool = True
    prompt: str = ""
    options: List[DecisionOption] = field(default_factory=list)


@dataclass
class PlayMessage:
    id: str
    type: str
    text: str
    timestamp: str = field(default_factory=_now_iso)
    author: str = ""
    character_id: Optional[str] = None
    delay_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayState:
    session_started: bool = False
    auto_mode: bool = True
    active_plot_node_id: Optional[str] = None
    current_scene_id: Optional[str] = None
    pending_messages: List[PlayMessage] = field(default_factory=list)
    feed: List[PlayMessage] = field(default_factory=list)
    current_decision: Optional[PlotNode] = None
    unlocked_tasks: List[str] = field(default_factory=list)
    last_tick_at: str = field(default_factory=_now_iso)


@dataclass
class WorldState:
    phase: str = "setup"
    time_index: int = 0
    current_scene_id: Optional[str] = None
    current_event_id: Optional[str] = None
    current_plot_node_id: Optional[str] = None
    triggered_event_ids: List[str] = field(default_factory=list)
    candidate_event_ids: List[str] = field(default_factory=list)
    unlocked_clue_ids: List[str] = field(default_factory=list)
    hidden_secret_ids: List[str] = field(default_factory=list)
    public_information: List[str] = field(default_factory=list)
    private_information: Dict[str, List[str]] = field(default_factory=dict)
    player_state: Dict[str, Any] = field(default_factory=dict)
    character_states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    scene_states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    relationship_tension: Dict[str, float] = field(default_factory=dict)
    pending_tasks: List[str] = field(default_factory=list)
    pending_decision: Optional[Dict[str, Any]] = None
    debug_log: List[str] = field(default_factory=list)


@dataclass
class StoryWorld:
    story_id: str
    title: str
    genre: str = ""
    source_type: str = "story"
    source_files: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    main_storyline: str = ""
    characters: List[StoryCharacter] = field(default_factory=list)
    relationships: List[StoryRelationship] = field(default_factory=list)
    events: List[StoryEvent] = field(default_factory=list)
    scenes: List[StoryScene] = field(default_factory=list)
    world_rules: List[WorldRule] = field(default_factory=list)
    clues: List[StoryClue] = field(default_factory=list)
    secrets: List[StorySecret] = field(default_factory=list)
    arcs: List[StoryArc] = field(default_factory=list)
    graph: NarrativeGraph = field(default_factory=NarrativeGraph)
    runtime_agents: Dict[str, CharacterRuntimeState] = field(default_factory=dict)
    world_state: WorldState = field(default_factory=WorldState)
    play_state: PlayState = field(default_factory=PlayState)
    continuation: Dict[str, Any] = field(default_factory=dict)
    extraction_meta: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return _deep_convert(self)


class StoryProjectManager:
    ROOT_DIR = os.path.join(Config.UPLOAD_FOLDER, "story_worlds")

    @classmethod
    def ensure_root_dir(cls):
        os.makedirs(cls.ROOT_DIR, exist_ok=True)

    @classmethod
    def create_story_id(cls) -> str:
        return f"world_{uuid.uuid4().hex[:12]}"

    @classmethod
    def slugify_title(cls, title: str) -> str:
        cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title.strip().lower())
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
        return cleaned or "world"

    @classmethod
    def get_story_dir(cls, story_id: str) -> str:
        cls.ensure_root_dir()
        path = os.path.join(cls.ROOT_DIR, story_id)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_existing_story_dir(cls, story_id: str) -> str:
        cls.ensure_root_dir()
        return os.path.join(cls.ROOT_DIR, story_id)

    @classmethod
    def get_story_meta_path(cls, story_id: str) -> str:
        return os.path.join(cls.get_story_dir(story_id), "world.json")

    @classmethod
    def get_story_files_dir(cls, story_id: str) -> str:
        path = os.path.join(cls.get_story_dir(story_id), "files")
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def save_story(cls, story: Any):
        if isinstance(story, dict):
            story_id = story["story_id"]
            story["updated_at"] = _now_iso()
            payload = story
        else:
            story.updated_at = _now_iso()
            story_id = story.story_id
            payload = story.to_dict()
        with open(cls.get_story_meta_path(story_id), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_story(cls, story_id: str) -> Optional[Dict[str, Any]]:
        path = cls.get_story_meta_path(story_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def list_stories(cls, limit: int = 50) -> List[Dict[str, Any]]:
        cls.ensure_root_dir()
        stories = []
        for story_id in os.listdir(cls.ROOT_DIR):
            data = cls.load_story(story_id)
            if data:
                stories.append(data)
        stories.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return stories[:limit]

    @classmethod
    def delete_story(cls, story_id: str) -> bool:
        story_dir = cls.get_existing_story_dir(story_id)
        if not os.path.exists(story_dir):
            return False
        shutil.rmtree(story_dir)
        return True
