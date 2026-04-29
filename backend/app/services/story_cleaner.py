"""
故事资产清洗与旧存档迁移
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict
from typing import Any, Dict, List, Tuple

from .story_graph import NarrativeGraphService
from .story_play_runtime import CharacterDialogueDirector, NarrativeEventAdapter
from .world_state import CharacterAgentRuntimeService, CharacterRegistry, ContinuationEngine, WorldState


class StoryDataSanitizer:
    ALLOWED_GROUP_NAMES = {"警方", "启明科技", "调查组", "董事会", "媒体", "实验室", "网友", "调查记者"}
    BLACKLISTED_NAMES = {
        "消息", "一秒", "不要相信", "群像推演", "式剧情游", "这个", "那个", "自己",
        "什么", "这样", "这时", "那里", "这里", "那边", "这边", "于是", "花纹",
        "平静", "周围", "高等级", "蓝光", "陶罐",
        "故事", "世界", "设定", "公司", "文件", "报告", "方案", "主线", "线索", "秘密",
    }
    BAD_NAME_SUFFIXES = ("发", "说", "问", "追", "看", "来", "去", "着", "过", "起", "开始")
    ORGANIZATION_HINTS = {"科技", "公司", "实验室", "集团", "警方", "媒体", "调查组", "董事会"}
    EVENT_NOISE_KEYWORDS = {"设计方案", "剧情游戏", "群像推演", "模块", "改造目标", "技术实现思路", "MVP"}
    GENERIC_NOUN_HINTS = {"家里", "路边", "时间", "时代", "单元", "高贵", "许多", "大厅", "中央", "窗前", "外面", "里面", "花纹", "周围", "蓝光", "陶罐"}
    FRONTSTAGE_NOISE_PATTERNS = (
        "判断当前局势尚未明朗",
        "推动当前剧情",
        "观察局势",
        "这一段发生在",
        "这个行业中",
        "最先撞上的就是",
        "真正的问题是",
        "发生在这个",
        "被同一轮变化绑在了一起",
    )

    @classmethod
    def sanitize_story(cls, story: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        cleaned = copy.deepcopy(story)
        changed = False

        for key in ["summary", "main_storyline"]:
            original = cleaned.get(key, "")
            normalized = cls.clean_text(original)
            if normalized != original:
                cleaned[key] = normalized
                changed = True

        characters, char_changed = cls._sanitize_characters(cleaned.get("characters", []))
        cleaned["characters"] = characters
        changed = changed or char_changed
        registry = CharacterRegistry.build_from_characters(cleaned["characters"])
        if registry != cleaned.get("character_registry", {}):
            cleaned["character_registry"] = registry
            changed = True
        valid_character_ids = {item["id"] for item in registry.get("entries", [])}
        interactive_character_ids = set(registry.get("playable_ids", []))

        events, event_changed = cls._sanitize_events(cleaned.get("events", []), valid_character_ids)
        cleaned["events"] = events
        changed = changed or event_changed
        valid_event_ids = {item["id"] for item in events}

        relationships, relation_changed = cls._sanitize_relationships(cleaned.get("relationships", []), valid_character_ids)
        cleaned["relationships"] = relationships
        changed = changed or relation_changed

        scenes, scene_changed = cls._sanitize_scenes(cleaned.get("scenes", []), valid_character_ids)
        cleaned["scenes"] = scenes
        changed = changed or scene_changed
        valid_scene_ids = {item["id"] for item in scenes}

        cleaned["clues"], clue_changed = cls._sanitize_clues(cleaned.get("clues", []), valid_character_ids, valid_event_ids)
        cleaned["secrets"], secret_changed = cls._sanitize_secrets(cleaned.get("secrets", []), valid_character_ids, {item["id"] for item in cleaned["clues"]})
        cleaned["world_rules"], rule_changed = cls._sanitize_rules(cleaned.get("world_rules", []))
        cleaned["arcs"], arc_changed = cls._sanitize_arcs(cleaned.get("arcs", []), valid_event_ids)
        narrative_blocks, block_changed = cls._sanitize_narrative_blocks(
            cleaned.get("narrative_blocks", []),
            cleaned["events"],
            cleaned["scenes"],
            cleaned["clues"],
        )
        cleaned["narrative_blocks"] = narrative_blocks
        playable_beats, beat_changed = cls._sanitize_playable_beats(
            cleaned.get("playable_beats", []),
            cleaned["events"],
            cleaned["narrative_blocks"],
            cleaned["characters"],
            cleaned["clues"],
            cleaned["scenes"],
        )
        cleaned["playable_beats"] = playable_beats
        changed = changed or clue_changed or secret_changed or rule_changed or arc_changed or block_changed or beat_changed

        runtime_agents = cleaned.get("runtime_agents", {})
        filtered_runtime = {k: v for k, v in runtime_agents.items() if k in valid_character_ids}
        if filtered_runtime != runtime_agents:
            cleaned["runtime_agents"] = filtered_runtime
            changed = True

        cleaned_world_state, ws_changed = cls._sanitize_world_state(cleaned, valid_character_ids, interactive_character_ids, valid_event_ids, valid_scene_ids)
        cleaned["world_state"] = cleaned_world_state
        changed = changed or ws_changed

        cleaned_play_state, play_changed = cls._sanitize_play_state(cleaned, cleaned.get("play_state", {}))
        cleaned["play_state"] = cleaned_play_state
        changed = changed or play_changed

        if changed:
            cleaned["graph"] = asdict(NarrativeGraphService.build_graph(cleaned))
            cleaned["runtime_agents"] = {
                key: vars(value)
                for key, value in CharacterAgentRuntimeService.bootstrap_agents(cleaned).items()
            }
            cleaned["continuation"] = ContinuationEngine.generate(cleaned, WorldState(**cleaned["world_state"]))
            cleaned["play_state"] = cls._sanitize_play_state(cleaned, cleaned.get("play_state", {}))[0]
        return cleaned, changed

    @classmethod
    def clean_text(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        text = re.sub(r"===\s*[^=\n]+\s*===", "", text)
        text = re.sub(r"^=+\s*", "", text)
        text = re.sub(r"\s*=+$", "", text)
        text = re.sub(r"\.tx\b", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = cls._trim_wrapping_quotes(text)
        text = text.strip("：:;；")
        return text

    @classmethod
    def _trim_wrapping_quotes(cls, text: str) -> str:
        pairs = (("“", "”"), ('"', '"'), ("'", "'"), ("`", "`"))
        cleaned = text.strip()
        while len(cleaned) >= 2:
            for left, right in pairs:
                if cleaned.startswith(left) and cleaned.endswith(right):
                    cleaned = cleaned[1:-1].strip()
                    break
            else:
                break
        return cleaned

    @classmethod
    def clean_goal_text(cls, value: Any, entity_type: str = "character") -> str:
        text = cls.clean_text(value)
        if "试图掌握局势并推动个人目标" in text:
            return "维持组织稳定" if entity_type == "organization" else "查清当前局势"
        return text

    @classmethod
    def sanitize_character_goal_text(cls, value: Any, entity_type: str = "character") -> str:
        text = cls.clean_goal_text(value, entity_type)
        if not text:
            return ""
        if CharacterDialogueDirector._looks_like_exposition_fragment(text):
            return "维持组织稳定" if entity_type == "organization" else "查清当前局势"
        if len(text) > 40:
            return "维持组织稳定" if entity_type == "organization" else "查清当前局势"
        return text[:40]

    @classmethod
    def sanitize_character_belief_text(cls, value: Any) -> str:
        text = cls.clean_text(value)
        if not text:
            return ""
        if "判断当前局势尚未明朗" in text:
            return ""
        if CharacterDialogueDirector._looks_like_exposition_fragment(text):
            return ""
        if len(text) > 56 and all(token not in text for token in ("你", "我", "别", "先", "不要", "现在", "必须", "不能", "提醒", "试探")):
            return ""
        return text[:56]

    @classmethod
    def normalize_scene_label(cls, value: Any) -> str:
        text = cls.clean_text(value)
        if not text:
            return "当前场面"
        if text in {"这个行业中", "这一段发生在这个行业中", "这个世界里", "这里"}:
            return "当前场面"
        if text.startswith("这一段") or text.startswith("这个行业"):
            return "当前场面"
        if len(text) > 12 and any(keyword in text for keyword in ("发生在", "这一段", "当前", "局势")):
            return "当前场面"
        return text

    @classmethod
    def is_generic_frontstage_text(cls, value: Any) -> bool:
        text = cls.clean_text(value)
        if not text:
            return True
        return any(pattern in text for pattern in cls.FRONTSTAGE_NOISE_PATTERNS)

    @classmethod
    def sanitize_playable_situation(cls, value: Any, scene_label: str = "", fallback: str = "") -> str:
        text = cls.clean_text(value)
        scene = cls.normalize_scene_label(scene_label)
        if cls.is_generic_frontstage_text(text):
            if fallback and not cls.is_generic_frontstage_text(fallback):
                text = cls.clean_text(fallback)
            else:
                text = f"你站在{scene}里，先感觉到的不是声音，而是所有人都在等你先露判断。"
        text = text.replace("真正的问题是，", "")
        text = text.replace("最先撞上的就是", "最先撞上的，是")
        text = text.replace("。。", "。")
        if not text.startswith("你"):
            text = f"你站在{scene}里，{text}"
        return text[:280]

    @classmethod
    def sanitize_playable_objective(cls, value: Any, present_names: List[str] | None = None) -> str:
        text = cls.clean_text(value)
        target = (present_names or ["对方"])[0] if present_names else "对方"
        if not text or cls.is_generic_frontstage_text(text):
            return f"先弄清楚{target}刚才那句话到底是在提醒你、试探你，还是故意把你往错处引。"
        text = text.replace("先弄清楚你到底是在", f"先弄清楚{target}到底是在")
        text = text.replace("先判断你是否", "先判断对方是否")
        text = text.replace("你到底是在", f"{target}到底是在")
        return text[:180]

    @classmethod
    def sanitize_playable_question(cls, value: Any, present_names: List[str] | None = None) -> str:
        text = cls.clean_text(value)
        target = (present_names or ["对方"])[0] if present_names else "对方"
        if not text or cls.is_generic_frontstage_text(text):
            return f"你要不要现在就把问题当面问到{target}脸上？"
        text = text.replace("逼问你", f"逼问{target}")
        text = text.replace("试探你", f"试探{target}")
        text = text.replace("你要不要当面逼问你", f"你要不要当面逼问{target}")
        return text[:180]

    @classmethod
    def sanitize_playable_risk(cls, value: Any, present_names: List[str] | None = None) -> str:
        text = cls.clean_text(value)
        target = (present_names or ["对方"])[0] if present_names else "对方"
        if not text or cls.is_generic_frontstage_text(text):
            return f"如果你错判{target}的立场，接下来整轮对话都会建立在错误前提上。"
        text = text.replace("如果你错判你的立场", f"如果你错判{target}的立场")
        text = text.replace("如果你误判你的立场", f"如果你误判{target}的立场")
        return text[:180]

    @classmethod
    def sanitize_frontstage_text(cls, value: Any, speaker_name: str = "") -> str:
        text = cls.clean_text(value)
        if not text:
            return ""
        text = re.sub(
            r"如果(?:主角错判滑膛的立场|你错判你的立场|你误判你的立场|你现在先把人看错了)，?接下来几轮对话都会建立在错误前提上。?",
            "你现在只要先把人看错一个，后面几轮话都会建立在错的前提上。",
            text,
        )
        text = text.replace("主角", "你")
        text = text.replace("滑膛的立场", "眼前这些人的立场")
        text = text.replace("判断当前局势尚未明朗。", "")
        text = text.replace("判断当前局势尚未明朗", "")
        text = text.replace("推动当前剧情。", "")
        text = text.replace("推动当前剧情", "")
        text = text.replace("响应事件", "")
        generic_danger = "你现在只要先把人看错一个，后面几轮话都会建立在错的前提上。"
        if speaker_name == "朱汉杨" and generic_danger in text:
            return "朱汉杨看着你：“我话已经放在这儿了。你要怎么接，是你的事。”"
        if speaker_name == "许雪萍" and generic_danger in text:
            return "许雪萍轻声说：“先别急着把话说死。这里有人是在提醒你，也有人是在等你自己露判断。”"
        if speaker_name == "朱汉杨" and any(
            token in text for token in ("但你怎么理解，是你的事", "我只说一次，听不听得进去，看你", "你要是还想往下问，我就在这儿")
        ):
            return "朱汉杨看着你：“我把该说的已经放在桌面上了。你要怎么接，自己决定。”"
        if speaker_name == "许雪萍" and any(
            token in text for token in ("你最好先记住，不要当场把话说透", "你要是真想继续往下走", "先别让人把你的表情读完")
        ):
            return "许雪萍轻声说：“别只听表面那句话。真正该留意的，是谁急着让你表态。”"
        if speaker_name == "滑膛" and any(
            token in text for token in ("先别急着下结论", "先看清楚，再动")
        ) and CharacterDialogueDirector._looks_like_exposition_fragment(text):
            return "滑膛语气很平：“先把人看明白，再往下接。”"
        if speaker_name and CharacterDialogueDirector._looks_like_exposition_fragment(text):
            if speaker_name == "朱汉杨":
                return "朱汉杨看着你：“我把该说的已经放在桌面上了。你要怎么接，自己决定。”"
            if speaker_name == "许雪萍":
                return "许雪萍轻声说：“别只听表面那句话。真正该留意的，是谁急着让你表态。”"
            if speaker_name == "滑膛":
                return "滑膛语气很平：“先把人看明白，再往下接。”"
            return ""
        if speaker_name:
            text = re.sub(rf"^{re.escape(speaker_name)}(?=[\s：:，,])[\s：:，,]*", "", text)
            if re.match(r"^(看着你|轻声说|声音压得很低|把话说得很直|先看了你一眼，才开口|没退|靠近了一点|只说了一句|语气很平)", text):
                text = f"{speaker_name}{text}"
        if "：“" in text and text.count("“") > text.count("”"):
            text = f"{text}”"
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace("。。", "。")
        return text

    @classmethod
    def _sanitize_turn_frontstage(cls, turn: Dict[str, Any]) -> Dict[str, Any]:
        payload = copy.deepcopy(turn or {})
        present_names = [item.get("name") for item in payload.get("present_characters", []) if item.get("name")]
        payload["scene_label"] = cls.normalize_scene_label(payload.get("scene_label", ""))
        payload["situation"] = cls.sanitize_playable_situation(
            payload.get("situation", ""),
            scene_label=payload.get("scene_label", ""),
        )
        payload["objective"] = cls.sanitize_playable_objective(
            payload.get("objective", ""),
            present_names=present_names,
        )
        payload["dramatic_question"] = cls.sanitize_playable_question(
            payload.get("dramatic_question", ""),
            present_names=present_names,
        )
        payload["risk"] = cls.sanitize_playable_risk(
            payload.get("risk", ""),
            present_names=present_names,
        )
        payload["supplemental_hint"] = cls.clean_text(payload.get("supplemental_hint", ""))[:120]
        dialogues = []
        for item in payload.get("dialogues", []):
            sanitized_text = cls.sanitize_frontstage_text(item.get("text", ""), speaker_name=item.get("speaker", ""))
            if not sanitized_text:
                continue
            dialogues.append({**item, "text": sanitized_text})
        payload["dialogues"] = dialogues[:2]
        return payload

    @classmethod
    def is_valid_event_text(cls, text: str) -> bool:
        cleaned = cls.clean_text(text)
        if not cleaned:
            return False
        if len(cleaned) == 1 and re.fullmatch(r"[“”\"'`·,，。.！？!?：:;；\-—_]+", cleaned):
            return False
        if any(keyword in cleaned for keyword in cls.EVENT_NOISE_KEYWORDS):
            return False
        if cleaned.startswith("第") and cleaned.endswith("阶段："):
            return False
        if re.search(r"[=]{2,}", cleaned):
            return False
        return True

    @classmethod
    def is_valid_character_name(cls, name: str, role_type: str = "", importance_score: float = 0.0, aliases: List[str] | None = None) -> bool:
        aliases = aliases or []
        cleaned = cls.clean_text(name)
        if not cleaned or len(cleaned) < 2:
            return False
        if cleaned in cls.BLACKLISTED_NAMES:
            return False
        if cleaned.endswith(cls.BAD_NAME_SUFFIXES):
            return False
        if cleaned.startswith("char_"):
            return False
        if re.search(r"[=《》/\\]", cleaned):
            return False
        if re.fullmatch(r"[“”\"'`·,，。.！？!?：:;；\-—_]+", cleaned):
            return False
        if any(hint in cleaned for hint in cls.GENERIC_NOUN_HINTS) and importance_score < 0.8:
            return False
        if cleaned not in cls.ALLOWED_GROUP_NAMES and role_type != "group":
            if len(cleaned) <= 2 and cleaned.endswith(("秒", "条", "份", "次", "名", "段")):
                return False
            if any(token in cleaned for token in ["不要", "相信", "剧情", "游戏", "方案", "设计", "消息", "时间", "路边", "家里", "高贵", "许多", "于是", "花纹", "平静", "周围", "高等级", "蓝光", "陶罐"]):
                return False
        if importance_score < 0.2 and len(set(aliases)) <= 1 and cleaned not in cls.ALLOWED_GROUP_NAMES:
            return False
        return True

    @classmethod
    def _sanitize_characters(cls, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        seen_names = set()
        for item in items:
            canonical_name = cls.clean_text(item.get("canonical_name") or item.get("name"))
            aliases = [cls.clean_text(alias) for alias in item.get("aliases", []) if cls.clean_text(alias)]
            role_type = item.get("role_type", "")
            importance_score = float(item.get("importance_score", 0.0))
            if not cls.is_valid_character_name(canonical_name, role_type=role_type, importance_score=importance_score, aliases=aliases):
                changed = True
                continue
            if canonical_name in seen_names:
                changed = True
                continue
            seen_names.add(canonical_name)

            payload = copy.deepcopy(item)
            payload["canonical_name"] = canonical_name
            payload["name"] = cls.clean_text(item.get("name") or canonical_name)
            payload["entity_type"] = item.get("entity_type") or cls._infer_entity_type(canonical_name, item.get("role_type", ""))
            payload["aliases"] = list(dict.fromkeys([alias for alias in aliases if alias != canonical_name]))[:8]
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["persona"] = cls.clean_text(item.get("persona", ""))
            payload["motivation"] = cls.sanitize_character_goal_text(item.get("motivation", ""), payload["entity_type"])
            payload["hidden_info"] = [cls.clean_text(text) for text in item.get("hidden_info", []) if cls.clean_text(text)]
            payload["goals"] = [
                cls.sanitize_character_goal_text(text, payload["entity_type"])
                for text in item.get("goals", [])
                if cls.sanitize_character_goal_text(text, payload["entity_type"])
            ]
            payload["traits"] = [cls.clean_text(text) for text in item.get("traits", []) if cls.clean_text(text)]
            payload["beliefs"] = [
                cls.sanitize_character_belief_text(text)
                for text in item.get("beliefs", [])
                if cls.sanitize_character_belief_text(text)
            ]
            payload["knowledge_scope"] = [cls.clean_text(text) for text in item.get("knowledge_scope", []) if cls.clean_text(text)]
            payload["review_source"] = cls.clean_text(item.get("review_source", "")) or "heuristic"
            payload["review_verdict"] = cls.clean_text(item.get("review_verdict", "")) or "keep"
            payload["review_notes"] = [cls.clean_text(text) for text in item.get("review_notes", []) if cls.clean_text(text)]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_events(cls, items: List[Dict[str, Any]], valid_character_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            title = cls.clean_text(item.get("title") or item.get("summary"))
            summary = cls.clean_text(item.get("summary") or title)
            if not cls.is_valid_event_text(title):
                changed = True
                continue
            participants = [item_id for item_id in item.get("participants", []) if item_id in valid_character_ids]
            payload["title"] = title[:48]
            payload["summary"] = summary[:180]
            payload["actor"] = item.get("actor", "") if item.get("actor", "") in valid_character_ids else ""
            payload["action"] = cls.clean_text(item.get("action", ""))
            payload["target"] = item.get("target", "") if item.get("target", "") in valid_character_ids else ""
            payload["participants"] = participants
            payload["trigger_conditions"] = [cls.clean_text(text) for text in item.get("trigger_conditions", []) if cls.clean_text(text)]
            payload["preconditions"] = [cls.clean_text(text) for text in item.get("preconditions", []) if cls.clean_text(text)]
            payload["consequences"] = [cls.clean_text(text) for text in item.get("consequences", []) if cls.clean_text(text)]
            payload["outcomes"] = [cls.clean_text(text) for text in item.get("outcomes", []) if cls.clean_text(text)]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_relationships(cls, items: List[Dict[str, Any]], valid_character_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        seen = set()
        for item in items:
            source = item.get("source")
            target = item.get("target")
            if source not in valid_character_ids or target not in valid_character_ids or source == target:
                changed = True
                continue
            key = (source, target, item.get("relation"))
            if key in seen:
                changed = True
                continue
            seen.add(key)
            payload = copy.deepcopy(item)
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_scenes(cls, items: List[Dict[str, Any]], valid_character_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            payload["name"] = cls.clean_text(item.get("name", "")) or item.get("id", "场景")
            payload["location"] = cls.clean_text(item.get("location", ""))
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["participants"] = [item_id for item_id in item.get("participants", []) if item_id in valid_character_ids]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_clues(cls, items: List[Dict[str, Any]], valid_character_ids: set[str], valid_event_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            payload["title"] = cls.clean_text(item.get("title", "")) or item.get("id", "线索")
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["holders"] = [item_id for item_id in item.get("holders", []) if item_id in valid_character_ids]
            payload["related_events"] = [item_id for item_id in item.get("related_events", []) if item_id in valid_event_ids]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_secrets(cls, items: List[Dict[str, Any]], valid_character_ids: set[str], valid_clue_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            payload["title"] = cls.clean_text(item.get("title", "")) or item.get("id", "秘密")
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["holders"] = [item_id for item_id in item.get("holders", []) if item_id in valid_character_ids]
            payload["related_clues"] = [item_id for item_id in item.get("related_clues", []) if item_id in valid_clue_ids]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_rules(cls, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            payload["rule"] = cls.clean_text(item.get("rule", ""))
            payload["implication"] = cls.clean_text(item.get("implication", ""))
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_arcs(cls, items: List[Dict[str, Any]], valid_event_ids: set[str]) -> Tuple[List[Dict[str, Any]], bool]:
        normalized = []
        changed = False
        for item in items:
            payload = copy.deepcopy(item)
            payload["title"] = cls.clean_text(item.get("title", "")) or item.get("id", "章节")
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["events"] = [item_id for item_id in item.get("events", []) if item_id in valid_event_ids]
            payload["key_node_event_ids"] = [item_id for item_id in item.get("key_node_event_ids", []) if item_id in valid_event_ids]
            normalized.append(payload)
            if payload != item:
                changed = True
        return normalized, changed

    @classmethod
    def _sanitize_narrative_blocks(
        cls,
        items: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        valid_event_ids = {item["id"] for item in events}
        valid_scene_ids = {item["id"] for item in scenes}
        valid_clue_ids = {item["id"] for item in clues}
        normalized = []
        changed = False
        for idx, item in enumerate(items, 1):
            payload = copy.deepcopy(item)
            payload["title"] = cls.clean_text(item.get("title", "")) or f"叙事块 {idx}"
            payload["summary"] = cls.clean_text(item.get("summary", ""))
            payload["situation"] = cls.clean_text(item.get("situation", ""))
            payload["conflict"] = cls.clean_text(item.get("conflict", ""))
            payload["player_implication"] = cls.clean_text(item.get("player_implication", ""))
            payload["risk"] = cls.clean_text(item.get("risk", ""))
            payload["objective"] = cls.clean_text(item.get("objective", ""))
            payload["action_vectors"] = [cls.clean_text(text) for text in item.get("action_vectors", []) if cls.clean_text(text)]
            payload["event_ids"] = [event_id for event_id in item.get("event_ids", []) if event_id in valid_event_ids]
            payload["participant_ids"] = item.get("participant_ids", [])[:6]
            payload["clue_ids"] = [clue_id for clue_id in item.get("clue_ids", []) if clue_id in valid_clue_ids]
            payload["scene_id"] = item.get("scene_id") if item.get("scene_id") in valid_scene_ids else None
            payload["phase"] = item.get("phase", "setup")
            if not payload["event_ids"]:
                changed = True
                continue
            normalized.append(payload)
            if payload != item:
                changed = True
        ideal_count = max(1, min(4, (len(events) + 1) // 2))
        if (not normalized or len(normalized) < ideal_count) and events:
            changed = True
            normalized = cls._generate_narrative_blocks_from_events(events)
        return normalized, changed

    @classmethod
    def _generate_narrative_blocks_from_events(cls, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        blocks = []
        chunk_size = 2 if len(events) <= 8 else 3
        for idx, start in enumerate(range(0, len(events), chunk_size), 1):
            batch = events[start:start + chunk_size]
            if not batch:
                continue
            opening = cls.clean_text(batch[0].get("summary", "") or batch[0].get("title", ""))
            ending = cls.clean_text(batch[-1].get("summary", "") or batch[-1].get("title", ""))
            participant_ids = []
            clue_ids = []
            for event in batch:
                for participant in event.get("participants", []):
                    if participant not in participant_ids:
                        participant_ids.append(participant)
                for clue_id in event.get("clues", []):
                    if clue_id not in clue_ids:
                        clue_ids.append(clue_id)
            blocks.append({
                "id": f"block_{idx}",
                "title": batch[0].get("title", f"叙事块 {idx}"),
                "summary": "；".join(cls.clean_text(event.get("summary", ""))[:48] for event in batch[:3]),
                "situation": f"这一轮局势从“{opening[:36]}”开始，很快又逼近到“{ending[:42]}”。",
                "conflict": "眼前的信息还不完整，但已经足够让站位、信任和风险开始重新洗牌。",
                "player_implication": "你不能再只看表面动作，必须判断谁在推动节奏，谁在利用沉默。",
                "risk": "你现在任何过早的表态，都可能替别人完成他们想要的引导。",
                "objective": "先确认这轮变化到底在把你推向谁，再决定如何开口。",
                "action_vectors": ["先试探在场人物", "先不暴露立场继续观察", "转去核实你最不确定的那条线索"],
                "event_ids": [event["id"] for event in batch],
                "participant_ids": participant_ids[:5],
                "clue_ids": clue_ids[:4],
                "scene_id": (batch[0].get("scenes") or [None])[0],
                "phase": cls._block_phase(idx, len(events)),
                "evidence": batch[0].get("evidence", [])[:2],
            })
        return blocks or [{
            "id": "block_1",
            "title": events[0].get("title", "叙事块 1"),
            "summary": cls.clean_text(events[0].get("summary", "")),
            "situation": cls.clean_text(events[0].get("summary", "")),
            "conflict": "局势已经开始变化，但真正的矛盾还没有完全浮出水面。",
            "player_implication": "你必须尽快判断该向谁逼近，向谁隐瞒。",
            "risk": "你的每一步都会改变别人看待你的方式。",
            "objective": "先把局势看清，再决定如何出手。",
            "action_vectors": ["先试探在场人物", "先不暴露立场继续观察"],
            "event_ids": [events[0]["id"]],
            "participant_ids": events[0].get("participants", [])[:4],
            "clue_ids": events[0].get("clues", [])[:2],
            "scene_id": (events[0].get("scenes") or [None])[0],
            "phase": "setup",
            "evidence": events[0].get("evidence", [])[:2],
        }]

    @classmethod
    def _block_phase(cls, block_index: int, total_events: int) -> str:
        ratio = min(1.0, (block_index * 2) / max(total_events, 1))
        if ratio < 0.25:
            return "setup"
        if ratio < 0.6:
            return "confrontation"
        if ratio < 0.85:
            return "climax"
        return "resolution"

    @classmethod
    def _sanitize_world_state(
        cls,
        story: Dict[str, Any],
        valid_character_ids: set[str],
        interactive_character_ids: set[str],
        valid_event_ids: set[str],
        valid_scene_ids: set[str],
    ) -> Tuple[Dict[str, Any], bool]:
        world_state = copy.deepcopy(story.get("world_state", {}))
        changed = False
        triggered = [item for item in world_state.get("triggered_event_ids", []) if item in valid_event_ids]
        candidate = [item for item in world_state.get("candidate_event_ids", []) if item in valid_event_ids]
        if triggered != world_state.get("triggered_event_ids", []):
            world_state["triggered_event_ids"] = triggered
            changed = True
        if candidate != world_state.get("candidate_event_ids", []):
            world_state["candidate_event_ids"] = candidate
            changed = True

        character_states = {}
        for char_id, state in (world_state.get("character_states") or {}).items():
            if char_id not in interactive_character_ids:
                changed = True
                continue
            payload = copy.deepcopy(state)
            payload["name"] = cls.clean_text(state.get("name", ""))
            entity_type = next((item.get("entity_type", "character") for item in story.get("characters", []) if item["id"] == char_id), "character")
            payload["focus"] = cls.clean_goal_text(state.get("focus", ""), entity_type)
            character_states[char_id] = payload
        for character in story.get("characters", []):
            if character["id"] not in interactive_character_ids:
                continue
            character_states.setdefault(character["id"], {
                "name": character.get("canonical_name") or character.get("name"),
                "status": character.get("status", "active"),
                "focus": cls.clean_goal_text((character.get("goals") or [""])[0], character.get("entity_type", "character")),
            })
        if character_states != world_state.get("character_states", {}):
            world_state["character_states"] = character_states
            changed = True

        player_state = copy.deepcopy(world_state.get("player_state", {}))
        targets = [item for item in player_state.get("targets", []) if item in valid_character_ids]
        if targets != player_state.get("targets", []):
            player_state["targets"] = targets
            world_state["player_state"] = player_state
            changed = True

        public_information = []
        seen = set()
        for item in world_state.get("public_information", []):
            cleaned = cls.clean_text(item)
            if not cleaned or cleaned in seen or len(cleaned) == 1 or not cls.is_valid_event_text(cleaned):
                changed = True
                continue
            seen.add(cleaned)
            public_information.append(cleaned)
        if public_information != world_state.get("public_information", []):
            world_state["public_information"] = public_information
            changed = True

        current_event_id = world_state.get("current_event_id")
        if current_event_id not in valid_event_ids:
            world_state["current_event_id"] = triggered[-1] if triggered else None
            changed = True
        current_scene_id = world_state.get("current_scene_id")
        if current_scene_id not in valid_scene_ids:
            world_state["current_scene_id"] = next(iter(valid_scene_ids), None)
            changed = True

        return world_state, changed

    @classmethod
    def _sanitize_play_state(cls, story: Dict[str, Any], play_state: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        payload = copy.deepcopy(play_state or {})
        changed = False
        registry = CharacterRegistry.ensure(story)
        valid_speakers = set(registry.get("speakable_ids", []))
        valid_targets = set(registry.get("playable_ids", []))
        for key in ["feed", "pending_messages"]:
            cleaned = NarrativeEventAdapter.sanitize_visible_messages(payload.get(key, []), story)
            normalized_messages = []
            for item in cleaned:
                message = copy.deepcopy(item)
                if message.get("type") == "scene":
                    raw_label = cls.clean_text(str(message.get("text", "")).replace("场景：", "", 1))
                    message["text"] = f"场景：{cls.normalize_scene_label(raw_label)}"
                else:
                    message["text"] = cls.sanitize_frontstage_text(message.get("text", ""), speaker_name=message.get("author", ""))
                if cls.clean_text(message.get("text", "")):
                    normalized_messages.append(message)
            cleaned = normalized_messages
            if cleaned != payload.get(key, []):
                payload[key] = cleaned
                changed = True
        current_turn = copy.deepcopy(payload.get("current_turn") or {})
        if current_turn:
            dialogues = []
            present_ids = []
            for item in current_turn.get("dialogues", []):
                character_id = item.get("character_id")
                if character_id not in valid_speakers:
                    changed = True
                    continue
                character = CharacterRegistry.get_character(story, character_id, require_speaking=True)
                if not character:
                    changed = True
                    continue
                item["speaker"] = character.get("canonical_name") or character.get("name") or item.get("speaker", "")
                dialogues.append(item)
                present_ids.append(character_id)
            current_turn["dialogues"] = dialogues[:2]
            present_characters = []
            for char_id in present_ids[:2]:
                character = CharacterRegistry.get_character(story, char_id, require_speaking=True)
                if not character:
                    continue
                present_characters.append({
                    "id": char_id,
                    "name": character.get("canonical_name") or character.get("name"),
                    "role": CharacterDialogueDirector.display_role(character),
                    "summary": NarrativeEventAdapter._truncate(character.get("summary") or character.get("persona") or "", 48),
                })
            if present_characters != current_turn.get("present_characters", []):
                current_turn["present_characters"] = present_characters
                changed = True
            sanitized_turn = cls._sanitize_turn_frontstage(current_turn)
            if sanitized_turn != current_turn:
                current_turn = sanitized_turn
                changed = True
            actions = []
            for item in current_turn.get("actions", []):
                target_character_id = item.get("target_character_id")
                if target_character_id and target_character_id not in valid_targets:
                    changed = True
                    item = {**item, "target_character_id": None}
                actions.append(item)
            current_turn["actions"] = actions[:5]
            payload["current_turn"] = current_turn
        turn_history = []
        for turn in payload.get("turn_history", []):
            sanitized_turn = cls._sanitize_turn_frontstage(turn)
            if sanitized_turn != turn:
                changed = True
            turn_history.append(sanitized_turn)
        if turn_history != payload.get("turn_history", []):
            payload["turn_history"] = turn_history[:10]
            changed = True
        decision = payload.get("current_decision") or None
        if decision:
            options = []
            for item in decision.get("options", []):
                target_character_id = item.get("target_character_id")
                if target_character_id and target_character_id not in valid_targets:
                    item = {**item, "target_character_id": None}
                    changed = True
                options.append(item)
            decision["options"] = options
        decision = payload.get("current_decision") or None
        if decision:
            decision["title"] = cls.clean_text(decision.get("title", "")) or "关键节点"
            decision["summary"] = cls.clean_text(decision.get("summary", ""))
            decision["prompt"] = "你要怎么做？"
            payload["current_decision"] = decision
            changed = True
        return payload, changed

    @classmethod
    def _sanitize_playable_beats(
        cls,
        items: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        narrative_blocks: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        valid_event_ids = {item["id"] for item in events}
        valid_block_ids = {item["id"] for item in narrative_blocks}
        valid_character_ids = {
            item["id"] for item in characters
            if item.get("entity_type", "character") == "character"
        }
        valid_clue_ids = {item["id"] for item in clues}
        valid_scene_ids = {item["id"] for item in scenes}
        char_map = {
            item["id"]: item
            for item in characters
            if item.get("entity_type", "character") == "character"
        }
        scene_map = {item["id"]: item for item in scenes}
        normalized = []
        changed = False
        for idx, item in enumerate(items, 1):
            payload = copy.deepcopy(item)
            payload["beat_id"] = item.get("beat_id") or f"beat_{idx}"
            payload["source_event_ids"] = [event_id for event_id in item.get("source_event_ids", []) if event_id in valid_event_ids]
            payload["source_block_id"] = item.get("source_block_id") if item.get("source_block_id") in valid_block_ids else None
            payload["importance"] = item.get("importance", "minor")
            if payload["importance"] not in {"major", "minor", "transition", "background"}:
                payload["importance"] = "minor"
            payload["present_character_ids"] = [
                char_id for char_id in item.get("present_character_ids", [])
                if char_id in valid_character_ids
            ][:4]
            present_names = [
                char_map[char_id].get("canonical_name") or char_map[char_id].get("name")
                for char_id in payload["present_character_ids"]
                if char_id in char_map
            ]
            payload["scene_id"] = item.get("scene_id") if item.get("scene_id") in valid_scene_ids else None
            scene_label = cls.normalize_scene_label((scene_map.get(payload["scene_id"]) or {}).get("name") or (scene_map.get(payload["scene_id"]) or {}).get("location") or "")
            payload["first_person_situation"] = cls.sanitize_playable_situation(
                item.get("first_person_situation", ""),
                scene_label=scene_label,
            )
            payload["player_objective"] = cls.sanitize_playable_objective(
                item.get("player_objective", ""),
                present_names=present_names,
            )
            payload["dramatic_question"] = cls.sanitize_playable_question(
                item.get("dramatic_question", ""),
                present_names=present_names,
            )
            payload["suggested_action_intents"] = [
                cls.clean_text(intent) for intent in item.get("suggested_action_intents", [])
                if cls.clean_text(intent)
            ][:5]
            payload["revealed_clue_ids"] = [
                clue_id for clue_id in item.get("revealed_clue_ids", [])
                if clue_id in valid_clue_ids
            ][:4]
            payload["risk_summary"] = cls.sanitize_playable_risk(
                item.get("risk_summary", ""),
                present_names=present_names,
            )
            payload["should_render_full_turn"] = bool(item.get("should_render_full_turn", True))
            payload["phase"] = item.get("phase", "setup")
            payload["evidence"] = item.get("evidence", [])[:4]
            if not payload["source_event_ids"]:
                changed = True
                continue
            normalized.append(payload)
            if payload != item:
                changed = True
        ideal_count = max(1, min(6, len(narrative_blocks) or len(events)))
        if (not normalized or len(normalized) < ideal_count) and events:
            changed = True
            normalized = cls._generate_playable_beats_from_blocks(events, narrative_blocks, characters, clues, scenes)
        return normalized, changed

    @classmethod
    def _generate_playable_beats_from_blocks(
        cls,
        events: List[Dict[str, Any]],
        narrative_blocks: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        event_map = {item["id"]: item for item in events}
        char_map = {
            item["id"]: item
            for item in characters
            if item.get("entity_type", "character") == "character"
        }
        clue_map = {item["id"]: item for item in clues}
        scene_map = {item["id"]: item for item in scenes}
        beats = []
        source_blocks = narrative_blocks or cls._generate_narrative_blocks_from_events(events)
        for idx, block in enumerate(source_blocks, 1):
            source_events = [event_map[event_id] for event_id in block.get("event_ids", []) if event_id in event_map]
            if not source_events:
                continue
            lead_event = source_events[0]
            importance = "major" if any(event.get("is_key_node") or "main" in event.get("tags", []) for event in source_events) else (
                "background" if not block.get("participant_ids") and not block.get("clue_ids") else "minor"
            )
            present_character_ids = [item for item in block.get("participant_ids", []) if item in char_map][:4]
            names = [char_map[item].get("canonical_name") or char_map[item].get("name") for item in present_character_ids]
            clue_titles = [clue_map[item]["title"] for item in block.get("clue_ids", []) if item in clue_map]
            scene = scene_map.get(block.get("scene_id"), {})
            scene_name = cls.normalize_scene_label(scene.get("name", "") or scene.get("location", ""))
            beats.append({
                "beat_id": f"beat_{idx}",
                "source_event_ids": [event["id"] for event in source_events],
                "source_block_id": block.get("id"),
                "importance": importance,
                "first_person_situation": cls.sanitize_playable_situation(
                    f"你站在{scene_name}里，先看到的是{cls.clean_text(block.get('situation', '') or lead_event.get('summary', ''))[:96]}。",
                    scene_label=scene_name,
                    fallback=block.get("situation", "") or lead_event.get("summary", ""),
                ),
                "player_objective": cls.sanitize_playable_objective(
                    cls.clean_text(block.get("objective", "")) or "先分清这轮变化究竟在把你推向谁。",
                    present_names=names,
                ),
                "dramatic_question": cls.sanitize_playable_question(
                    f"你要不要当面逼问{names[0]}？" if names else "你要不要现在就把判断亮出来？",
                    present_names=names,
                ),
                "present_character_ids": present_character_ids,
                "suggested_action_intents": ["press_character", "probe_character", "observe"] if names else ["observe", "reposition"],
                "revealed_clue_ids": block.get("clue_ids", [])[:4],
                "risk_summary": cls.sanitize_playable_risk(
                    cls.clean_text(block.get("risk", "")) or "你现在任何过早表态，都可能替别人完成他们想要的引导。",
                    present_names=names,
                ),
                "should_render_full_turn": importance != "background",
                "scene_id": block.get("scene_id"),
                "phase": block.get("phase", "setup"),
                "evidence": block.get("evidence", [])[:4],
            })
        return beats

    @classmethod
    def _infer_entity_type(cls, canonical_name: str, role_type: str) -> str:
        if role_type == "group" or any(hint in canonical_name for hint in cls.ORGANIZATION_HINTS):
            return "organization"
        return "character"
