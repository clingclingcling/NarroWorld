"""
故事世界数据模型与持久化管理
"""

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from json import JSONDecodeError
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
    entity_type: str = "character"
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
    review_source: str = "heuristic"
    review_verdict: str = "keep"
    review_notes: List[str] = field(default_factory=list)
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
    actor: str = ""
    action: str = ""
    target: str = ""
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
class NarrativeBlock:
    id: str
    title: str
    summary: str
    situation: str = ""
    conflict: str = ""
    player_implication: str = ""
    risk: str = ""
    objective: str = ""
    action_vectors: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)
    participant_ids: List[str] = field(default_factory=list)
    clue_ids: List[str] = field(default_factory=list)
    scene_id: Optional[str] = None
    phase: str = "setup"
    evidence: List[EvidenceRef] = field(default_factory=list)


@dataclass
class PlayableBeat:
    beat_id: str
    source_event_ids: List[str] = field(default_factory=list)
    source_block_id: Optional[str] = None
    importance: str = "minor"
    first_person_situation: str = ""
    player_objective: str = ""
    dramatic_question: str = ""
    present_character_ids: List[str] = field(default_factory=list)
    suggested_action_intents: List[str] = field(default_factory=list)
    revealed_clue_ids: List[str] = field(default_factory=list)
    risk_summary: str = ""
    should_render_full_turn: bool = True
    scene_id: Optional[str] = None
    phase: str = "setup"
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
    speech_style: str = ""
    risk_profile: str = ""
    value_guardrails: List[str] = field(default_factory=list)
    knowledge_scope: List[str] = field(default_factory=list)
    current_intent: str = ""
    last_action: str = ""
    secret_pressure: float = 0.0


@dataclass
class DecisionOption:
    id: str
    label: str
    impact: str = ""
    risk: str = ""
    action_type: str = ""
    target_character_id: Optional[str] = None
    target_clue_id: Optional[str] = None
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
class PlayEventQueueEntry:
    event_id: str
    status: str = "pending"
    importance: str = "minor"
    source_block_id: Optional[str] = None
    reason: str = ""
    debug_reason: str = ""
    turn_generated: bool = False
    priority: float = 0.0


@dataclass
class PlayState:
    session_started: bool = False
    auto_mode: bool = True
    protagonist_id: Optional[str] = None
    protagonist_name: str = ""
    active_plot_node_id: Optional[str] = None
    current_scene_id: Optional[str] = None
    pending_messages: List[PlayMessage] = field(default_factory=list)
    feed: List[PlayMessage] = field(default_factory=list)
    event_queue: List[PlayEventQueueEntry] = field(default_factory=list)
    current_turn: Optional[Dict[str, Any]] = None
    turn_history: List[Dict[str, Any]] = field(default_factory=list)
    current_decision: Optional[PlotNode] = None
    latest_feedback: Optional[Dict[str, Any]] = None
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
    character_registry: Dict[str, Any] = field(default_factory=dict)
    relationships: List[StoryRelationship] = field(default_factory=list)
    events: List[StoryEvent] = field(default_factory=list)
    scenes: List[StoryScene] = field(default_factory=list)
    world_rules: List[WorldRule] = field(default_factory=list)
    clues: List[StoryClue] = field(default_factory=list)
    secrets: List[StorySecret] = field(default_factory=list)
    arcs: List[StoryArc] = field(default_factory=list)
    narrative_blocks: List[NarrativeBlock] = field(default_factory=list)
    playable_beats: List[PlayableBeat] = field(default_factory=list)
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
    SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

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
        path = cls._story_dir_path(story_id)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_existing_story_dir(cls, story_id: str) -> str:
        cls.ensure_root_dir()
        return cls._story_dir_path(story_id)

    @classmethod
    def get_story_meta_path(cls, story_id: str, create: bool = True) -> str:
        story_dir = cls.get_story_dir(story_id) if create else cls.get_existing_story_dir(story_id)
        return os.path.join(story_dir, "world.json")

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
        path = cls.get_story_meta_path(story_id)
        story_dir = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(prefix="world_", suffix=".json.tmp", dir=story_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @classmethod
    def load_story(cls, story_id: str) -> Optional[Dict[str, Any]]:
        try:
            path = cls.get_story_meta_path(story_id, create=False)
        except ValueError:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            return json.loads(raw)
        except JSONDecodeError:
            recovered = cls._recover_story_json(raw)
            if not recovered:
                return None
            backup_path = f"{path}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(path, backup_path)
            cls.save_story(recovered)
            return recovered

    @classmethod
    def list_stories(cls, limit: int = 50) -> List[Dict[str, Any]]:
        cls.ensure_root_dir()
        stories = []
        for story_id in os.listdir(cls.ROOT_DIR):
            if not cls.SAFE_ID_PATTERN.match(story_id):
                continue
            data = cls.load_story(story_id)
            if data:
                stories.append(data)
        stories.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return stories[:limit]

    @classmethod
    def delete_story(cls, story_id: str) -> bool:
        try:
            story_dir = cls.get_existing_story_dir(story_id)
        except ValueError:
            return False
        if not os.path.exists(story_dir):
            return False
        shutil.rmtree(story_dir)
        return True

    @classmethod
    def _story_dir_path(cls, story_id: str) -> str:
        story_id = str(story_id or "").strip()
        if not cls.SAFE_ID_PATTERN.match(story_id):
            raise ValueError(f"非法世界 ID: {story_id}")
        root = os.path.abspath(cls.ROOT_DIR)
        path = os.path.abspath(os.path.join(root, story_id))
        if not path.startswith(root + os.sep):
            raise ValueError(f"非法世界路径: {story_id}")
        return path

    @classmethod
    def _recover_story_json(cls, raw: str) -> Optional[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        try:
            data, end = decoder.raw_decode(raw)
        except JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        trailing = raw[end:].strip()
        if not trailing:
            return data
        return data


class StoryGenerationJobManager:
    ROOT_DIR = os.path.join(Config.UPLOAD_FOLDER, "story_generation_jobs")
    SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
    STALE_RUNNING_SECONDS = 2 * 60 * 60

    @classmethod
    def ensure_root_dir(cls):
        os.makedirs(cls.ROOT_DIR, exist_ok=True)

    @classmethod
    def create_job_id(cls) -> str:
        return f"job_{uuid.uuid4().hex[:12]}"

    @classmethod
    def get_job_dir(cls, job_id: str) -> str:
        cls.ensure_root_dir()
        path = cls._job_dir_path(job_id)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_existing_job_dir(cls, job_id: str) -> str:
        cls.ensure_root_dir()
        return cls._job_dir_path(job_id)

    @classmethod
    def get_job_meta_path(cls, job_id: str, create: bool = True) -> str:
        job_dir = cls.get_job_dir(job_id) if create else cls.get_existing_job_dir(job_id)
        return os.path.join(job_dir, "job.json")

    @classmethod
    def create_job(
        cls,
        *,
        job_id: str,
        title: str = "",
        genre: str = "",
        source_type: str = "story",
        world_id: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "job_id": job_id,
            "status": "pending",
            "stage": "pending",
            "message": "任务已创建，等待开始。",
            "progress": 0,
            "world_id": world_id,
            "error": "",
            "title": title,
            "genre": genre,
            "source_type": source_type,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        cls.save_job(payload)
        return payload

    @classmethod
    def save_job(cls, job: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(job)
        payload["updated_at"] = _now_iso()
        path = cls.get_job_meta_path(payload["job_id"])
        job_dir = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(prefix="job_", suffix=".json.tmp", dir=job_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        return payload

    @classmethod
    def load_job(cls, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            path = cls.get_job_meta_path(job_id, create=False)
        except ValueError:
            return None
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            return cls._mark_stale_if_needed(json.loads(raw))
        except JSONDecodeError:
            recovered = cls._recover_job_json(raw)
            if not recovered:
                return {
                    "job_id": job_id,
                    "status": "failed",
                    "stage": "load_job",
                    "message": "生成任务状态文件损坏。",
                    "progress": 0,
                    "world_id": "",
                    "error": "job.json is corrupted",
                    "updated_at": _now_iso(),
                }
            backup_path = f"{path}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(path, backup_path)
            return cls.save_job(recovered)

    @classmethod
    def update_job(cls, job_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        payload = cls.load_job(job_id)
        if not payload:
            return None
        for key, value in updates.items():
            if value is not None:
                payload[key] = value
        return cls.save_job(payload)

    @classmethod
    def _recover_job_json(cls, raw: str) -> Optional[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(raw)
        except JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _mark_stale_if_needed(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("status") not in {"pending", "running"}:
            return payload
        try:
            updated_at = datetime.fromisoformat(payload.get("updated_at", ""))
        except (TypeError, ValueError):
            return payload
        if (datetime.now() - updated_at).total_seconds() <= cls.STALE_RUNNING_SECONDS:
            return payload
        payload = dict(payload)
        payload.update({
            "status": "failed",
            "stage": payload.get("stage") or "stale_job",
            "message": "生成任务长时间没有更新，可能因服务重启而中断。",
            "error": "generation job stale",
        })
        return cls.save_job(payload)

    @classmethod
    def _job_dir_path(cls, job_id: str) -> str:
        job_id = str(job_id or "").strip()
        if not cls.SAFE_ID_PATTERN.match(job_id):
            raise ValueError(f"非法任务 ID: {job_id}")
        root = os.path.abspath(cls.ROOT_DIR)
        path = os.path.abspath(os.path.join(root, job_id))
        if not path.startswith(root + os.sep):
            raise ValueError(f"非法任务路径: {job_id}")
        return path
