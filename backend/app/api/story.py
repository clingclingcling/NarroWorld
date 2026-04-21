"""
NarraWorld 故事世界 API
"""

import json
import os
import time
import traceback
import uuid

from flask import Response, jsonify, request, stream_with_context

from . import story_bp
from ..models.story import StoryProjectManager
from ..services.story_extractor import StoryExtractionService
from ..services.story_graph import NarrativeGraphService
from ..services.story_play_runtime import ChatDrivenPlayRuntimeService
from ..services.world_state import ContinuationEngine, NarrativePlanner, WorldState, WorldStateEngine
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger

logger = get_logger("narraworld.api.story")


def _allowed_story_file(filename: str) -> bool:
    return bool(filename and os.path.splitext(filename)[1].lower() in StoryExtractionService.SUPPORTED_EXTENSIONS)


def _load_world_or_404(story_id: str):
    story = StoryProjectManager.load_story(story_id)
    if not story:
        return None, (jsonify({"success": False, "error": f"世界不存在: {story_id}"}), 404)
    return story, None


def _persist_story(story: dict):
    StoryProjectManager.save_story(story)


def _story_counts(story: dict) -> dict:
    return {
        "characters": len(story.get("characters", [])),
        "relationships": len(story.get("relationships", [])),
        "events": len(story.get("events", [])),
        "scenes": len(story.get("scenes", [])),
        "clues": len(story.get("clues", [])),
        "secrets": len(story.get("secrets", [])),
    }


def _sse_event(data, event: str = "message", event_id: str = "") -> str:
    payload = json.dumps(data, ensure_ascii=False)
    chunks = []
    if event_id:
        chunks.append(f"id: {event_id}")
    chunks.append(f"event: {event}")
    chunks.append(f"data: {payload}")
    return "\n".join(chunks) + "\n\n"


@story_bp.route("/ingest", methods=["POST"])
def ingest_story():
    try:
        title = request.form.get("title", "").strip() or "未命名世界"
        genre = request.form.get("genre", "").strip()
        source_type = request.form.get("source_type", "story").strip() or "story"
        uploaded_files = request.files.getlist("files")

        if not uploaded_files or all(not item.filename for item in uploaded_files):
            return jsonify({"success": False, "error": "请至少上传一个故事文件"}), 400

        story_id = StoryProjectManager.create_story_id()
        files_dir = StoryProjectManager.get_story_files_dir(story_id)

        source_files = []
        all_text_parts = []
        for file in uploaded_files:
            if not file or not file.filename:
                continue
            if not _allowed_story_file(file.filename):
                return jsonify({"success": False, "error": f"不支持的文件类型: {file.filename}"}), 400

            ext = os.path.splitext(file.filename)[1].lower()
            stored_name = f"{uuid.uuid4().hex[:8]}{ext}"
            stored_path = os.path.join(files_dir, stored_name)
            file.save(stored_path)
            text = FileParser.extract_text(stored_path)
            source_files.append({
                "original_filename": file.filename,
                "stored_filename": stored_name,
                "path": stored_path,
                "size": os.path.getsize(stored_path),
            })
            all_text_parts.append(f"=== {file.filename} ===\n{text}")

        source_text = "\n\n".join(all_text_parts).strip()
        service = StoryExtractionService()
        world = service.ingest(
            story_id=story_id,
            title=title,
            genre=genre,
            source_text=source_text,
            source_files=source_files,
            source_type=source_type,
        )
        _persist_story(world.to_dict())
        return jsonify({"success": True, "data": world.to_dict()})
    except Exception as e:
        logger.error(f"世界导入失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@story_bp.route("/list", methods=["GET"])
def list_stories():
    limit = request.args.get("limit", 20, type=int)
    return jsonify({"success": True, "data": StoryProjectManager.list_stories(limit=limit)})


@story_bp.route("/<story_id>", methods=["GET"])
def get_story(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    return jsonify({"success": True, "data": story})


@story_bp.route("/<story_id>/overview", methods=["GET"])
def get_world_overview(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    overview = {
        "story_id": story["story_id"],
        "title": story["title"],
        "genre": story.get("genre", ""),
        "summary": story.get("summary", ""),
        "main_storyline": story.get("main_storyline", ""),
        "counts": _story_counts(story),
        "characters": story.get("characters", [])[:6],
        "arcs": story.get("arcs", []),
        "graph_preview": NarrativeGraphService.get_filtered_view(story, view="all"),
        "world_state": story.get("world_state", {}),
        "continuation": story.get("continuation", {}),
    }
    return jsonify({"success": True, "data": overview})


@story_bp.route("/<story_id>/preview", methods=["GET"])
def get_story_preview(story_id: str):
    return get_world_overview(story_id)


@story_bp.route("/<story_id>/graph", methods=["GET"])
def get_story_graph(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    view = request.args.get("view", "all")
    node_types = [item.strip() for item in request.args.get("node_types", "").split(",") if item.strip()]
    focus_ids = [item.strip() for item in request.args.get("focus_ids", "").split(",") if item.strip()]
    return jsonify({
        "success": True,
        "data": NarrativeGraphService.get_filtered_view(story, view=view, node_types=node_types, focus_ids=focus_ids),
    })


@story_bp.route("/<story_id>/characters", methods=["GET"])
def get_story_characters(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    runtime_agents = story.get("runtime_agents", {})
    data = []
    for character in story.get("characters", []):
        data.append({
            **character,
            "runtime": runtime_agents.get(character["id"], {}),
            "graph_context": NarrativeGraphService.get_character_context(story, character["id"]),
        })
    return jsonify({"success": True, "data": data})


@story_bp.route("/<story_id>/planner", methods=["GET"])
def get_story_planner(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    world_state = WorldState(**story.get("world_state", {}))
    return jsonify({
        "success": True,
        "data": {
            "phase": world_state.phase,
            "candidate_events": NarrativePlanner.get_candidate_events(story, world_state),
        },
    })


@story_bp.route("/<story_id>/debug", methods=["GET"])
def get_story_debug(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    return jsonify({
        "success": True,
        "data": {
            "world_state": story.get("world_state", {}),
            "play_state": story.get("play_state", {}),
            "planner": {
                "candidate_events": NarrativePlanner.get_candidate_events(story, WorldState(**story.get("world_state", {}))),
            },
            "graph_threads": NarrativeGraphService.get_unresolved_threads(story),
            "extraction_meta": story.get("extraction_meta", {}),
        },
    })


@story_bp.route("/<story_id>/advance", methods=["POST"])
def advance_story(story_id: str):
    try:
        story, error = _load_world_or_404(story_id)
        if error:
            return error
        world_state = WorldState(**story.get("world_state", {}))
        payload = request.get_json(silent=True) or {}
        event_id = payload.get("event_id")
        if not event_id:
            next_event = NarrativePlanner.choose_next_event(story, world_state)
            if not next_event:
                return jsonify({"success": True, "data": {"message": "暂无可推进事件"}})
            event_id = next_event["id"]

        result = WorldStateEngine.apply_event(story, world_state, event_id)
        story["world_state"] = result["world_state"].__dict__
        story["continuation"] = ContinuationEngine.generate(story, result["world_state"])
        ChatDrivenPlayRuntimeService.ensure_play_state(story)
        _persist_story(story)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"推进剧情失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@story_bp.route("/<story_id>/player-action", methods=["POST"])
def player_action(story_id: str):
    try:
        story, error = _load_world_or_404(story_id)
        if error:
            return error
        payload = request.get_json() or {}
        player_input = payload.get("input", "")
        result = ChatDrivenPlayRuntimeService.submit_player_input(story, player_input)
        story["continuation"] = ContinuationEngine.generate(story, WorldState(**story.get("world_state", {})))
        _persist_story(story)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"玩家动作失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@story_bp.route("/<story_id>/play", methods=["GET"])
def get_play_state(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    play_state = ChatDrivenPlayRuntimeService.ensure_play_state(story)
    return jsonify({"success": True, "data": play_state})


@story_bp.route("/<story_id>/play/start", methods=["POST"])
def start_play(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    play_state = ChatDrivenPlayRuntimeService.start_session(story)
    _persist_story(story)
    return jsonify({"success": True, "data": play_state})


@story_bp.route("/<story_id>/play/stream", methods=["GET"])
def stream_play(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error

    raw_cursor = request.args.get("cursor", "0")
    last_event_id = request.headers.get("Last-Event-ID", "").strip()
    try:
        start_cursor = int(last_event_id or raw_cursor or 0)
    except ValueError:
        start_cursor = 0

    @stream_with_context
    def generate():
        cursor = max(start_cursor, 0)
        state_signature = ""
        deadline = time.time() + 25
        initial_snapshot = ChatDrivenPlayRuntimeService.snapshot(story)
        cursor = max(cursor, initial_snapshot.get("cursor", 0))
        yield _sse_event(initial_snapshot, event="init")

        while time.time() < deadline:
            latest_story = StoryProjectManager.load_story(story_id)
            if not latest_story:
                yield _sse_event({"error": "世界已不可用"}, event="error")
                break

            play_state = ChatDrivenPlayRuntimeService.tick(latest_story)
            latest_story["continuation"] = ContinuationEngine.generate(
                latest_story,
                WorldState(**latest_story.get("world_state", {})),
            )
            _persist_story(latest_story)

            feed = play_state.get("feed", [])
            if cursor > len(feed):
                cursor = 0

            if len(feed) > cursor:
                for index, message in enumerate(feed[cursor:], start=cursor + 1):
                    yield _sse_event(message, event="message", event_id=str(index))
                cursor = len(feed)

            snapshot = ChatDrivenPlayRuntimeService.snapshot(latest_story)
            state_payload = {
                **snapshot,
                "continuation": latest_story.get("continuation", {}),
            }
            new_signature = json.dumps({
                "cursor": state_payload["cursor"],
                "world_state": state_payload["world_state"],
                "decision": (state_payload["play_state"] or {}).get("current_decision"),
                "director": state_payload.get("director", {}),
            }, ensure_ascii=False, sort_keys=True)
            if new_signature != state_signature:
                state_signature = new_signature
                yield _sse_event(state_payload, event="state")
            else:
                yield ": keep-alive\n\n"

            time.sleep(1.0)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@story_bp.route("/<story_id>/play/tick", methods=["POST"])
def tick_play(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    play_state = ChatDrivenPlayRuntimeService.tick(story)
    story["continuation"] = ContinuationEngine.generate(story, WorldState(**story.get("world_state", {})))
    _persist_story(story)
    return jsonify({"success": True, "data": play_state})


@story_bp.route("/<story_id>/play/input", methods=["POST"])
def play_input(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    payload = request.get_json() or {}
    result = ChatDrivenPlayRuntimeService.submit_player_input(story, payload.get("input", ""))
    _persist_story(story)
    return jsonify({"success": True, "data": result, "play_state": story.get("play_state", {})})


@story_bp.route("/<story_id>/play/choice", methods=["POST"])
def play_choice(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    payload = request.get_json() or {}
    result = ChatDrivenPlayRuntimeService.submit_choice(story, payload.get("option_id", ""))
    _persist_story(story)
    return jsonify({"success": True, "data": result, "play_state": story.get("play_state", {})})


@story_bp.route("/<story_id>/continuation", methods=["GET", "POST"])
def get_continuation(story_id: str):
    try:
        story, error = _load_world_or_404(story_id)
        if error:
            return error
        world_state = WorldState(**story.get("world_state", {}))
        continuation = ContinuationEngine.generate(story, world_state)
        story["continuation"] = continuation
        _persist_story(story)
        return jsonify({"success": True, "data": continuation})
    except Exception as e:
        logger.error(f"生成续写失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500
