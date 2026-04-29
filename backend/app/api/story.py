"""
NarraWorld 故事世界 API
"""

import json
import os
import time
import traceback
import uuid
from threading import Thread

from flask import Response, jsonify, request, stream_with_context

from . import story_bp
from ..models.story import StoryGenerationJobManager, StoryProjectManager
from ..services.story_cleaner import StoryDataSanitizer
from ..services.story_extractor import StoryExtractionService
from ..services.story_graph import NarrativeGraphService
from ..services.story_play_runtime import ChatDrivenPlayRuntimeService, ProtagonistResolver
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
    cleaned_story, changed = StoryDataSanitizer.sanitize_story(story)
    if changed:
        StoryProjectManager.save_story(cleaned_story)
        story = cleaned_story
    return story, None


def _persist_story(story: dict):
    StoryProjectManager.save_story(story)


def _store_uploaded_story_files(uploaded_files, story_id: str):
    files_dir = StoryProjectManager.get_story_files_dir(story_id)
    source_files = []
    for file in uploaded_files:
        if not file or not file.filename:
            continue
        if not _allowed_story_file(file.filename):
            raise ValueError(f"不支持的文件类型: {file.filename}")
        ext = os.path.splitext(file.filename)[1].lower()
        stored_name = f"{uuid.uuid4().hex[:8]}{ext}"
        stored_path = os.path.join(files_dir, stored_name)
        file.save(stored_path)
        source_files.append({
            "original_filename": file.filename,
            "stored_filename": stored_name,
            "path": stored_path,
            "size": os.path.getsize(stored_path),
        })
    return source_files


def _build_source_text_from_files(source_files):
    parts = []
    for item in source_files:
        path = item.get("path")
        if not path or not os.path.exists(path):
            continue
        filename = item.get("original_filename") or item.get("stored_filename") or os.path.basename(path)
        text = FileParser.extract_text(path)
        if text.strip():
            parts.append(f"=== {filename} ===\n{text}")
    return "\n\n".join(parts).strip()


def _read_story_source_text(story: dict) -> str:
    return _build_source_text_from_files(story.get("source_files", []))


def _rebuild_story_from_sources(story_id: str, story: dict) -> dict:
    source_text = _read_story_source_text(story)
    if not source_text:
        raise ValueError("未找到可用于重抽的源文件内容")
    service = StoryExtractionService()
    world = service.ingest(
        story_id=story_id,
        title=story.get("title", "未命名世界"),
        genre=story.get("genre", ""),
        source_text=source_text,
        source_files=story.get("source_files", []),
        source_type=story.get("source_type", "story"),
    )
    cleaned_story, _ = StoryDataSanitizer.sanitize_story(world.to_dict())
    _persist_story(cleaned_story)
    return cleaned_story


def _story_counts(story: dict) -> dict:
    return {
        "characters": len(story.get("characters", [])),
        "relationships": len(story.get("relationships", [])),
        "events": len(story.get("events", [])),
        "narrative_blocks": len(story.get("narrative_blocks", [])),
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


def _update_generation_job(job_id: str, **updates):
    return StoryGenerationJobManager.update_job(job_id, **updates)


def _run_generation_job(job_id: str, story_id: str, title: str, genre: str, source_type: str, source_files):
    try:
        _update_generation_job(
            job_id,
            status="running",
            stage="parsing_file",
            message="正在读取并解析故事文件。",
            progress=8,
            world_id=story_id,
        )
        source_text = _build_source_text_from_files(source_files)
        if not source_text.strip():
            raise ValueError("未能从上传文件中提取到可用文本")

        service = StoryExtractionService()

        def progress_callback(stage: str, message: str, progress: int):
            _update_generation_job(
                job_id,
                status="running",
                stage=stage,
                message=message,
                progress=progress,
                world_id=story_id,
            )

        world = service.ingest(
            story_id=story_id,
            title=title,
            genre=genre,
            source_text=source_text,
            source_files=source_files,
            source_type=source_type,
            progress_callback=progress_callback,
        )
        _update_generation_job(
            job_id,
            status="running",
            stage="saving_world",
            message="正在保存世界与初始状态。",
            progress=96,
            world_id=story_id,
        )
        world_payload = world.to_dict()
        cleaned_payload, _ = StoryDataSanitizer.sanitize_story(world_payload)
        _persist_story(cleaned_payload)
        _update_generation_job(
            job_id,
            status="succeeded",
            stage="completed",
            message="世界已经生成完成，可以进入总览继续游玩。",
            progress=100,
            world_id=cleaned_payload.get("story_id", story_id),
            error="",
        )
    except Exception as e:
        logger.error(f"世界生成任务失败 {job_id}: {e}")
        logger.debug(traceback.format_exc())
        _update_generation_job(
            job_id,
            status="failed",
            message="世界生成失败。",
            error=str(e),
        )


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
        source_files = _store_uploaded_story_files(uploaded_files, story_id)
        source_text = _build_source_text_from_files(source_files)
        service = StoryExtractionService()
        world = service.ingest(
            story_id=story_id,
            title=title,
            genre=genre,
            source_text=source_text,
            source_files=source_files,
            source_type=source_type,
        )
        world_payload = world.to_dict()
        cleaned_payload, _ = StoryDataSanitizer.sanitize_story(world_payload)
        _persist_story(cleaned_payload)
        return jsonify({"success": True, "data": cleaned_payload})
    except Exception as e:
        logger.error(f"世界导入失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@story_bp.route("/generate/start", methods=["POST"])
def start_story_generation():
    try:
        title = request.form.get("title", "").strip() or "未命名世界"
        genre = request.form.get("genre", "").strip()
        source_type = request.form.get("source_type", "story").strip() or "story"
        uploaded_files = request.files.getlist("files")

        if not uploaded_files or all(not item.filename for item in uploaded_files):
            return jsonify({"success": False, "error": "请至少上传一个故事文件"}), 400

        job_id = StoryGenerationJobManager.create_job_id()
        story_id = StoryProjectManager.create_story_id()
        source_files = _store_uploaded_story_files(uploaded_files, story_id)
        StoryGenerationJobManager.create_job(
            job_id=job_id,
            title=title,
            genre=genre,
            source_type=source_type,
            world_id=story_id,
        )
        worker = Thread(
            target=_run_generation_job,
            args=(job_id, story_id, title, genre, source_type, source_files),
            daemon=True,
        )
        worker.start()
        return jsonify({
            "success": True,
            "data": {
                "job_id": job_id,
                "world_id": story_id,
                "status": "pending",
            },
        })
    except Exception as e:
        logger.error(f"启动世界生成失败: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@story_bp.route("/generate/status/<job_id>", methods=["GET"])
def get_story_generation_status(job_id: str):
    payload = StoryGenerationJobManager.load_job(job_id)
    if not payload:
        return jsonify({"success": False, "error": f"生成任务不存在: {job_id}"}), 404
    return jsonify({"success": True, "data": payload})


@story_bp.route("/generate/stream/<job_id>", methods=["GET"])
def stream_story_generation_status(job_id: str):
    payload = StoryGenerationJobManager.load_job(job_id)
    if not payload:
        return jsonify({"success": False, "error": f"生成任务不存在: {job_id}"}), 404

    def generate():
        last_updated_at = ""
        start_time = time.time()
        while True:
            current = StoryGenerationJobManager.load_job(job_id)
            if not current:
                yield _sse_event(
                    {"job_id": job_id, "status": "failed", "error": "生成任务不存在"},
                    event="status",
                )
                break
            if current.get("updated_at") != last_updated_at:
                last_updated_at = current.get("updated_at", "")
                yield _sse_event(current, event="status", event_id=last_updated_at)
            if current.get("status") in {"succeeded", "failed"}:
                break
            if time.time() - start_time > 3600:
                break
            yield ": keep-alive\n\n"
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


@story_bp.route("/<story_id>", methods=["DELETE"])
def delete_story(story_id: str):
    story = StoryProjectManager.load_story(story_id)
    if not story:
        return jsonify({"success": False, "error": f"世界不存在: {story_id}"}), 404
    deleted = StoryProjectManager.delete_story(story_id)
    if not deleted:
        return jsonify({"success": False, "error": f"删除失败: {story_id}"}), 500
    return jsonify({"success": True, "data": {"story_id": story_id, "title": story.get("title", "")}})


@story_bp.route("/<story_id>/rebuild", methods=["POST"])
def rebuild_story(story_id: str):
    story = StoryProjectManager.load_story(story_id)
    if not story:
        return jsonify({"success": False, "error": f"世界不存在: {story_id}"}), 404
    rebuilt = _rebuild_story_from_sources(story_id, story)
    return jsonify({"success": True, "data": rebuilt})


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
        "protagonist": ProtagonistResolver.resolve(story),
        "counts": _story_counts(story),
        "characters": story.get("characters", [])[:6],
        "arcs": story.get("arcs", []),
        "narrative_blocks": story.get("narrative_blocks", [])[:4],
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
    play_state = ChatDrivenPlayRuntimeService.ensure_play_state(story)
    protagonist = ProtagonistResolver.resolve(story)
    return jsonify({
        "success": True,
        "data": {
            "story_meta": {
                "story_id": story.get("story_id", story_id),
                "title": story.get("title", "未命名世界"),
                "genre": story.get("genre", ""),
                "summary": story.get("summary", ""),
                "protagonist": protagonist,
                "counts": _story_counts(story),
                "created_at": story.get("created_at", ""),
                "updated_at": story.get("updated_at", ""),
                "source_files": [
                    {
                        "name": item.get("original_filename") or item.get("stored_filename") or "",
                        "size": item.get("size", 0),
                    }
                    for item in story.get("source_files", [])
                ],
            },
            "world_state": story.get("world_state", {}),
            "play_state": play_state,
            "event_queue": play_state.get("event_queue", []),
            "character_registry": story.get("character_registry", {}),
            "narrative_blocks": story.get("narrative_blocks", []),
            "playable_beats": story.get("playable_beats", []),
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
                "current_turn": (state_payload["play_state"] or {}).get("current_turn"),
                "latest_feedback": (state_payload["play_state"] or {}).get("latest_feedback"),
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
    story["continuation"] = ContinuationEngine.generate(story, WorldState(**story.get("world_state", {})))
    _persist_story(story)
    return jsonify({
        "success": True,
        "data": result,
        "play_state": story.get("play_state", {}),
        "world_state": story.get("world_state", {}),
        "continuation": story.get("continuation", {}),
    })


@story_bp.route("/<story_id>/play/choice", methods=["POST"])
def play_choice(story_id: str):
    story, error = _load_world_or_404(story_id)
    if error:
        return error
    payload = request.get_json() or {}
    result = ChatDrivenPlayRuntimeService.submit_choice(story, payload.get("option_id", ""))
    story["continuation"] = ContinuationEngine.generate(story, WorldState(**story.get("world_state", {})))
    _persist_story(story)
    return jsonify({
        "success": True,
        "data": result,
        "play_state": story.get("play_state", {}),
        "world_state": story.get("world_state", {}),
        "continuation": story.get("continuation", {}),
    })


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
