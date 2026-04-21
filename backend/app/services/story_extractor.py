"""
故事导入与结构化抽取服务
"""

import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..models.story import (
    EvidenceRef,
    StoryArc,
    StoryCharacter,
    StoryClue,
    StoryEvent,
    StoryRelationship,
    StoryScene,
    StorySecret,
    StoryWorld,
    WorldRule,
)
from ..utils.file_parser import split_text_into_chunks
from ..utils.llm_client import LLMClient
from .story_graph import NarrativeGraphService
from .story_play_runtime import ChatDrivenPlayRuntimeService
from .world_state import CharacterAgentRuntimeService, ContinuationEngine, NarrativePlanner, WorldStateEngine


class StoryExtractionService:
    SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}
    TITLE_SUFFIXES = ["先生", "女士", "老师", "同学", "工程师", "医生", "警官", "组长", "经理"]
    GROUP_LABELS = ["警方", "公司", "网友", "启明科技", "调查组", "董事会", "媒体", "实验室"]
    RELATION_KEYWORDS = {
        "TRUSTS": ["信任", "依赖", "求助", "托付", "协助"],
        "HATES": ["厌恶", "憎恨", "敌视", "怀疑", "冲突"],
        "LOVES": ["爱", "喜欢", "在意", "守护"],
        "HIDES_FROM": ["隐瞒", "瞒着", "躲避", "不愿告诉"],
        "ALLIES_WITH": ["合作", "联手", "站在一起", "同盟"],
        "CONFLICTS_WITH": ["争执", "对立", "背叛", "阻止"],
    }

    def __init__(self):
        self.llm: Optional[LLMClient] = None
        try:
            self.llm = LLMClient()
        except Exception:
            self.llm = None

    def ingest(
        self,
        story_id: str,
        title: str,
        genre: str,
        source_text: str,
        source_files: List[Dict[str, Any]],
        source_type: str = "story",
    ) -> StoryWorld:
        chunks = split_text_into_chunks(source_text, chunk_size=1500, overlap=140)
        extraction = self._extract_assets(title=title, genre=genre, source_text=source_text, chunks=chunks)

        world = StoryWorld(
            story_id=story_id,
            title=title,
            genre=genre,
            source_type=source_type,
            source_files=source_files,
            summary=extraction["summary"],
            main_storyline=extraction["main_storyline"],
            characters=[StoryCharacter(**item) for item in extraction["characters"]],
            relationships=[StoryRelationship(**item) for item in extraction["relationships"]],
            events=[StoryEvent(**item) for item in extraction["events"]],
            scenes=[StoryScene(**item) for item in extraction["scenes"]],
            world_rules=[WorldRule(**item) for item in extraction["world_rules"]],
            clues=[StoryClue(**item) for item in extraction["clues"]],
            secrets=[StorySecret(**item) for item in extraction["secrets"]],
            arcs=[StoryArc(**item) for item in extraction["arcs"]],
            extraction_meta=extraction["extraction_meta"],
        )

        story_dict = world.to_dict()
        world.graph = NarrativeGraphService.build_graph(story_dict)
        story_dict = world.to_dict()
        world.runtime_agents = CharacterAgentRuntimeService.bootstrap_agents(story_dict)
        story_dict = world.to_dict()
        world.world_state = WorldStateEngine.initialize_world_state(story_dict)
        story_dict = world.to_dict()
        world.world_state.candidate_event_ids = [
            item["event_id"] for item in NarrativePlanner.get_candidate_events(story_dict, world.world_state)
        ]
        world.continuation = ContinuationEngine.generate(story_dict, world.world_state)
        world.play_state = ChatDrivenPlayRuntimeService.start_session(world.to_dict())
        return world

    def _extract_assets(self, title: str, genre: str, source_text: str, chunks: List[str]) -> Dict[str, Any]:
        heuristic = self._extract_with_pipeline(title=title, genre=genre, source_text=source_text, chunks=chunks)
        if not self.llm:
            return heuristic
        try:
            refined = self._refine_with_llm(heuristic, title=title, genre=genre, source_text=source_text)
            refined["extraction_meta"]["used_llm"] = True
            return refined
        except Exception:
            heuristic["extraction_meta"]["used_llm"] = False
            return heuristic

    def _extract_with_pipeline(self, title: str, genre: str, source_text: str, chunks: List[str]) -> Dict[str, Any]:
        sentences = self._split_sentences(source_text)
        mentions = self._extract_candidate_mentions(sentences)
        clusters = self._resolve_alias_clusters(mentions)
        characters = self._build_character_cards(clusters, sentences)
        events = self._build_events(sentences, characters)
        relationships = self._build_relationships(sentences, characters, events)
        scenes = self._build_scenes(chunks, characters)
        clues = self._build_clues(sentences, characters, events)
        secrets = self._build_secrets(sentences, characters, clues)
        arcs = self._build_arcs(events)
        rules = self._build_world_rules(sentences)
        validation = self._validate_character_results(characters)

        return {
            "summary": " ".join(sentences[:3])[:260] or title,
            "main_storyline": " -> ".join(event["title"] for event in events[:5]) or title,
            "characters": characters,
            "relationships": relationships,
            "events": events,
            "scenes": scenes,
            "world_rules": rules,
            "clues": clues,
            "secrets": secrets,
            "arcs": arcs,
            "extraction_meta": {
                "used_llm": False,
                "pipeline": [
                    "entity_seed_extraction",
                    "coreference_resolution",
                    "role_clustering",
                    "importance_scoring",
                    "relationship_inference",
                    "schema_validation",
                ],
                "raw_mentions_count": len(mentions),
                "validation": validation,
            },
        }

    def _refine_with_llm(self, heuristic: Dict[str, Any], title: str, genre: str, source_text: str) -> Dict[str, Any]:
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 NarraWorld 的故事抽取修正器。"
                    "请在保持现有结构化数据的基础上，重点修正角色 alias 合并、role_type、motivation、hidden_info、relationship evidence。"
                    "不要无根据增加角色。输出 JSON 字段必须与输入结构一致。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": title,
                        "genre": genre,
                        "text_excerpt": source_text[:12000],
                        "heuristic_output": heuristic,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        refined = self.llm.chat_json(prompt, temperature=0.15, max_tokens=4000)
        normalized = heuristic.copy()
        for key in ["summary", "main_storyline", "characters", "relationships", "events", "scenes", "world_rules", "clues", "secrets", "arcs"]:
            if refined.get(key):
                normalized[key] = refined[key]
        normalized["characters"] = self._normalize_characters(normalized["characters"])
        normalized["relationships"] = self._normalize_relationships(normalized["relationships"], normalized["characters"])
        normalized["events"] = self._normalize_events(normalized["events"])
        normalized["scenes"] = self._normalize_scenes(normalized["scenes"])
        normalized["world_rules"] = self._normalize_rules(normalized["world_rules"])
        normalized["clues"] = self._normalize_clues(normalized["clues"])
        normalized["secrets"] = self._normalize_secrets(normalized["secrets"])
        normalized["arcs"] = self._normalize_arcs(normalized["arcs"], normalized["events"])
        normalized["extraction_meta"] = heuristic["extraction_meta"]
        return normalized

    def _split_sentences(self, source_text: str) -> List[str]:
        sentences = re.split(r"(?<=[。！？!?])|\n+", source_text)
        return [item.strip() for item in sentences if item and item.strip()]

    def _extract_candidate_mentions(self, sentences: List[str]) -> List[Dict[str, Any]]:
        mentions = []
        for idx, sentence in enumerate(sentences):
            names = re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?|[\u4e00-\u9fff]{2,4}", sentence)
            for token in names:
                token = token.strip()
                if self._is_noise_token(token):
                    continue
                mentions.append({
                    "mention": token,
                    "sentence": sentence[:180],
                    "sentence_index": idx,
                    "normalized": self._normalize_alias(token),
                })
        for label in self.GROUP_LABELS:
            for idx, sentence in enumerate(sentences):
                if label in sentence:
                    mentions.append({
                        "mention": label,
                        "sentence": sentence[:180],
                        "sentence_index": idx,
                        "normalized": label,
                    })
        return mentions

    def _resolve_alias_clusters(self, mentions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clusters: Dict[str, Dict[str, Any]] = {}
        for mention in mentions:
            key = mention["normalized"]
            bucket = clusters.setdefault(key, {
                "canonical_name": mention["normalized"],
                "aliases": set(),
                "evidence": [],
                "mentions": [],
                "sentence_indices": set(),
            })
            bucket["aliases"].add(mention["mention"])
            bucket["mentions"].append(mention["mention"])
            bucket["sentence_indices"].add(mention["sentence_index"])
            if len(bucket["evidence"]) < 4:
                bucket["evidence"].append({
                    "quote": mention["sentence"],
                    "source": "text",
                    "chunk_index": mention["sentence_index"],
                    "note": f"提及 {mention['mention']}",
                })
        return list(clusters.values())

    def _build_character_cards(self, clusters: List[Dict[str, Any]], sentences: List[str]) -> List[Dict[str, Any]]:
        cards = []
        for idx, cluster in enumerate(clusters, 1):
            aliases = sorted(cluster["aliases"])
            mention_count = len(cluster["mentions"])
            importance = round(min(1.0, mention_count * 0.12 + len(cluster["sentence_indices"]) * 0.08), 2)
            role_type = self._infer_role_type(cluster["canonical_name"], mention_count, aliases)
            if importance < 0.16 and role_type == "functional":
                continue
            motivation = self._infer_motivation(cluster["canonical_name"], sentences)
            hidden_info = self._infer_hidden_info(cluster["canonical_name"], sentences)
            cards.append({
                "id": f"char_{idx}",
                "name": cluster["canonical_name"],
                "canonical_name": cluster["canonical_name"],
                "aliases": aliases,
                "role": self._infer_role_label(role_type),
                "role_type": role_type,
                "summary": f"{cluster['canonical_name']} 是故事中的 {self._infer_role_label(role_type)}。",
                "persona": f"{cluster['canonical_name']} 的行为需要围绕既定动机与立场保持一致。",
                "motivation": motivation,
                "hidden_info": hidden_info,
                "goals": [motivation] if motivation else ["推动当前剧情"],
                "traits": self._infer_traits(cluster["canonical_name"], sentences),
                "secrets": hidden_info[:2],
                "beliefs": [f"{cluster['canonical_name']} 判断当前局势尚未明朗。"],
                "knowledge_scope": ["亲历事件", "已知关系", "可见线索"],
                "importance_score": importance,
                "status": "active",
                "evidence": cluster["evidence"],
            })
        cards.sort(key=lambda item: item["importance_score"], reverse=True)
        return self._normalize_characters(cards[:10])

    def _build_events(self, sentences: List[str], characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = []
        character_map = self._character_lookup(characters)
        for idx, sentence in enumerate(sentences[:10], 1):
            participants = self._match_characters_in_sentence(sentence, character_map)
            if not participants and idx > 6:
                continue
            event_type = "decision" if any(word in sentence for word in ["决定", "选择", "是否", "必须"]) else "plot"
            is_key_node = idx <= 4 or event_type == "decision"
            events.append({
                "id": f"event_{idx}",
                "title": sentence[:20] or f"事件 {idx}",
                "summary": sentence[:180],
                "order": idx,
                "event_type": event_type,
                "participants": participants,
                "scenes": [f"scene_{min(idx, 4)}"],
                "clues": [f"clue_{idx}"] if self._sentence_has_clue(sentence) else [],
                "status": "pending",
                "trigger_conditions": ["上一个关键节点已结束"] if idx > 1 else ["故事进入起始状态"],
                "preconditions": [sentences[idx - 2][:80]] if idx > 1 else [],
                "consequences": [sentence[:60]],
                "outcomes": [sentence[:80]],
                "caused_by": [f"event_{idx - 1}"] if idx > 1 else [],
                "leads_to": [f"event_{idx + 1}"] if idx < min(len(sentences), 10) else [],
                "tags": ["main"] if idx <= 5 else ["side"],
                "is_key_node": is_key_node,
                "evidence": [{
                    "quote": sentence[:180],
                    "source": "text",
                    "chunk_index": idx - 1,
                    "note": "事件原文证据",
                }],
            })
        return self._normalize_events(events)

    def _build_relationships(
        self,
        sentences: List[str],
        characters: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        relations = {}
        character_map = {item["id"]: item for item in characters}
        alias_lookup = self._character_lookup(characters)
        for sentence in sentences:
            participants = self._match_characters_in_sentence(sentence, alias_lookup)
            if len(participants) < 2:
                continue
            relation_type = self._infer_relation_type(sentence)
            for idx, source in enumerate(participants):
                for target in participants[idx + 1:]:
                    key = tuple(sorted((source, target)))
                    item = relations.setdefault(key, {
                        "source": key[0],
                        "target": key[1],
                        "relation": relation_type,
                        "strength": 0.48,
                        "summary": f"{character_map[key[0]]['canonical_name']} 与 {character_map[key[1]]['canonical_name']} 在文本中存在互动。",
                        "evidence": [],
                        "supporting_event_ids": [],
                    })
                    if len(item["evidence"]) < 3:
                        item["evidence"].append({
                            "quote": sentence[:180],
                            "source": "text",
                            "note": f"关系类型候选：{relation_type}",
                        })
                    item["strength"] = min(1.0, round(item["strength"] + 0.08, 2))

        for event in events:
            participants = event.get("participants", [])
            for idx, source in enumerate(participants):
                for target in participants[idx + 1:]:
                    key = tuple(sorted((source, target)))
                    if key in relations:
                        relations[key]["supporting_event_ids"].append(event["id"])
        return self._normalize_relationships(list(relations.values()), characters)

    def _build_scenes(self, chunks: List[str], characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        scenes = []
        for idx, chunk in enumerate(chunks[:4], 1):
            scenes.append({
                "id": f"scene_{idx}",
                "name": f"场景 {idx}",
                "location": self._infer_location(chunk, idx),
                "summary": chunk[:180],
                "mood": "紧绷" if idx == 1 else "推进中",
                "participants": [item["id"] for item in characters[: min(4, len(characters))]],
                "items": [],
                "evidence": [{
                    "quote": chunk[:180],
                    "source": "chunk",
                    "chunk_index": idx - 1,
                    "note": "场景摘要来源",
                }],
            })
        return self._normalize_scenes(scenes)

    def _build_clues(self, sentences: List[str], characters: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clues = []
        for idx, sentence in enumerate([item for item in sentences if self._sentence_has_clue(item)][:5], 1):
            clues.append({
                "id": f"clue_{idx}",
                "title": f"线索 {idx}",
                "summary": sentence[:100],
                "holders": [characters[0]["id"]] if characters else [],
                "related_events": [event["id"] for event in events if sentence[:20] in event["summary"]][:1] or [f"event_{idx}"],
                "visibility": "private" if idx == 1 else "public",
                "evidence": [{
                    "quote": sentence[:180],
                    "source": "text",
                    "chunk_index": idx - 1,
                    "note": "线索原文证据",
                }],
            })
        if not clues and events:
            clues.append({
                "id": "clue_1",
                "title": "关键异常",
                "summary": events[0]["summary"][:100],
                "holders": [characters[0]["id"]] if characters else [],
                "related_events": [events[0]["id"]],
                "visibility": "private",
                "evidence": events[0].get("evidence", [])[:1],
            })
        return self._normalize_clues(clues)

    def _build_secrets(self, sentences: List[str], characters: List[Dict[str, Any]], clues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        secret_sentences = [item for item in sentences if any(key in item for key in ["秘密", "匿名", "失踪", "隐瞒", "未知"])]
        secrets = []
        for idx, sentence in enumerate(secret_sentences[:3], 1):
            secrets.append({
                "id": f"secret_{idx}",
                "title": f"秘密 {idx}",
                "summary": sentence[:110],
                "holders": [characters[-1]["id"]] if characters else [],
                "exposed": False,
                "related_clues": [clues[min(idx - 1, len(clues) - 1)]["id"]] if clues else [],
                "evidence": [{
                    "quote": sentence[:180],
                    "source": "text",
                    "chunk_index": idx - 1,
                    "note": "秘密原文证据",
                }],
            })
        return self._normalize_secrets(secrets)

    def _build_arcs(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._normalize_arcs([
            {
                "id": "arc_main",
                "title": "主线章节",
                "summary": "围绕核心冲突推进的主叙事。",
                "events": [event["id"] for event in events[:6]],
                "phase": "setup",
                "key_node_event_ids": [event["id"] for event in events if event.get("is_key_node")][:4],
            }
        ], events)

    def _build_world_rules(self, sentences: List[str]) -> List[Dict[str, Any]]:
        base = [
            {
                "id": "rule_1",
                "rule": "角色只能基于其知识边界行动。",
                "implication": "没有证据支撑的秘密不会被凭空知晓。",
                "evidence": [],
            },
            {
                "id": "rule_2",
                "rule": "主线节点优先于支线噪声推进。",
                "implication": "剧情不会因自由输入完全失控。",
                "evidence": [],
            },
        ]
        if sentences:
            base[0]["evidence"] = [{
                "quote": sentences[0][:160],
                "source": "text",
                "chunk_index": 0,
                "note": "世界起始规则参考",
            }]
        return self._normalize_rules(base)

    def _validate_character_results(self, characters: List[Dict[str, Any]]) -> Dict[str, Any]:
        aliases = defaultdict(list)
        for character in characters:
            for alias in character.get("aliases", []):
                aliases[alias].append(character["canonical_name"])
        duplicate_aliases = {k: v for k, v in aliases.items() if len(set(v)) > 1}
        dirty_characters = [
            item["canonical_name"]
            for item in characters
            if item.get("importance_score", 0) < 0.2 and not item.get("hidden_info") and len(item.get("aliases", [])) == 1
        ]
        return {
            "character_count": len(characters),
            "duplicate_aliases": duplicate_aliases,
            "dirty_characters": dirty_characters,
            "required_fields_ok": all(item.get("canonical_name") and item.get("role_type") for item in characters),
        }

    def _infer_role_type(self, canonical_name: str, mention_count: int, aliases: List[str]) -> str:
        if canonical_name in self.GROUP_LABELS:
            return "group"
        if "匿名" in canonical_name or "未知" in canonical_name:
            return "hidden"
        if mention_count >= 4:
            return "core"
        if mention_count >= 2:
            return "supporting"
        return "functional"

    def _infer_role_label(self, role_type: str) -> str:
        return {
            "core": "核心主角",
            "supporting": "重要配角",
            "group": "群体性角色",
            "hidden": "匿名/隐藏角色",
            "functional": "功能性角色",
        }.get(role_type, "角色")

    def _infer_motivation(self, name: str, sentences: List[str]) -> str:
        for sentence in sentences:
            if name in sentence and any(word in sentence for word in ["想", "希望", "试图", "决定", "必须", "为了"]):
                return sentence[:48]
        return f"{name} 试图掌握局势并推动个人目标。"

    def _infer_hidden_info(self, name: str, sentences: List[str]) -> List[str]:
        hidden = []
        for sentence in sentences:
            if name in sentence and any(word in sentence for word in ["隐瞒", "秘密", "匿名", "未说", "失踪", "真相"]):
                hidden.append(sentence[:64])
        return hidden[:3]

    def _infer_traits(self, name: str, sentences: List[str]) -> List[str]:
        joined = " ".join([item for item in sentences if name in item][:4])
        traits = []
        if any(word in joined for word in ["冷静", "谨慎", "观察"]):
            traits.append("冷静")
        if any(word in joined for word in ["冲动", "急切", "立刻"]):
            traits.append("急迫")
        if any(word in joined for word in ["怀疑", "调查", "追踪"]):
            traits.append("调查者")
        return traits or ["复杂", "有行动意图"]

    def _infer_relation_type(self, sentence: str) -> str:
        for relation, keywords in self.RELATION_KEYWORDS.items():
            if any(keyword in sentence for keyword in keywords):
                return relation
        return "KNOWS"

    def _infer_location(self, chunk: str, idx: int) -> str:
        match = re.search(r"在([\u4e00-\u9fff]{2,8})", chunk)
        if match:
            return match.group(1)
        return f"地点 {idx}"

    def _character_lookup(self, characters: List[Dict[str, Any]]) -> Dict[str, str]:
        lookup = {}
        for character in characters:
            lookup[character["canonical_name"]] = character["id"]
            lookup[character["name"]] = character["id"]
            for alias in character.get("aliases", []):
                lookup[alias] = character["id"]
        return lookup

    def _match_characters_in_sentence(self, sentence: str, lookup: Dict[str, str]) -> List[str]:
        participants = []
        for alias, character_id in lookup.items():
            if alias and alias in sentence and character_id not in participants:
                participants.append(character_id)
        return participants[:4]

    def _sentence_has_clue(self, sentence: str) -> bool:
        return any(keyword in sentence for keyword in ["线索", "秘密", "短信", "车票", "证据", "档案", "编号", "匿名", "节点"])

    def _normalize_alias(self, token: str) -> str:
        result = token
        for suffix in self.TITLE_SUFFIXES:
            result = result.replace(suffix, "")
        return result.strip() or token

    def _is_noise_token(self, token: str) -> bool:
        blacklist = {
            "他们", "我们", "自己", "如果", "因为", "然后", "但是", "故事", "世界",
            "设定", "没有", "一个", "不是", "可以", "时候", "这个", "那个", "你们",
        }
        return token in blacklist or len(token.strip()) < 2

    def _normalize_characters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        seen = set()
        for idx, item in enumerate(items[:12], 1):
            canonical_name = item.get("canonical_name") or item.get("name") or f"角色{idx}"
            if canonical_name in seen:
                continue
            seen.add(canonical_name)
            normalized.append({
                "id": item.get("id") or f"char_{idx}",
                "name": item.get("name") or canonical_name,
                "canonical_name": canonical_name,
                "aliases": list(dict.fromkeys(item.get("aliases", [])))[:8],
                "role": item.get("role", ""),
                "role_type": item.get("role_type", "supporting"),
                "summary": item.get("summary", ""),
                "persona": item.get("persona", ""),
                "motivation": item.get("motivation", ""),
                "hidden_info": item.get("hidden_info", [])[:4],
                "goals": item.get("goals", [])[:4],
                "traits": item.get("traits", [])[:6],
                "secrets": item.get("secrets", [])[:4],
                "beliefs": item.get("beliefs", [])[:4],
                "knowledge_scope": item.get("knowledge_scope", [])[:6],
                "importance_score": float(item.get("importance_score", 0.5)),
                "status": item.get("status", "active"),
                "evidence": item.get("evidence", [])[:4],
            })
        return normalized

    def _normalize_relationships(self, items: List[Dict[str, Any]], characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        allowed_ids = {item["id"] for item in characters}
        normalized = []
        seen = set()
        for item in items[:40]:
            source = item.get("source")
            target = item.get("target")
            if not source or not target or source == target or source not in allowed_ids or target not in allowed_ids:
                continue
            key = tuple(sorted((source, target)))
            if key in seen:
                continue
            seen.add(key)
            normalized.append({
                "source": source,
                "target": target,
                "relation": item.get("relation", "KNOWS").upper(),
                "strength": float(item.get("strength", 0.5)),
                "summary": item.get("summary", ""),
                "evidence": item.get("evidence", [])[:4],
                "supporting_event_ids": item.get("supporting_event_ids", [])[:6],
            })
        return normalized

    def _normalize_events(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for idx, item in enumerate(items[:20], 1):
            normalized.append({
                "id": item.get("id") or f"event_{idx}",
                "title": item.get("title") or f"事件{idx}",
                "summary": item.get("summary", ""),
                "order": int(item.get("order", idx)),
                "event_type": item.get("event_type", "plot"),
                "participants": item.get("participants", [])[:6],
                "scenes": item.get("scenes", [])[:3],
                "clues": item.get("clues", [])[:4],
                "status": item.get("status", "pending"),
                "trigger_conditions": item.get("trigger_conditions", [])[:4],
                "preconditions": item.get("preconditions", [])[:4],
                "consequences": item.get("consequences", [])[:4],
                "outcomes": item.get("outcomes", [])[:4],
                "caused_by": item.get("caused_by", [])[:3],
                "leads_to": item.get("leads_to", [])[:3],
                "tags": item.get("tags", [])[:4],
                "is_key_node": bool(item.get("is_key_node", False)),
                "evidence": item.get("evidence", [])[:4],
            })
        normalized.sort(key=lambda item: item["order"])
        return normalized

    def _normalize_scenes(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": item.get("id") or f"scene_{idx}",
                "name": item.get("name") or f"场景 {idx}",
                "location": item.get("location", ""),
                "summary": item.get("summary", ""),
                "mood": item.get("mood", ""),
                "participants": item.get("participants", [])[:6],
                "items": item.get("items", [])[:6],
                "evidence": item.get("evidence", [])[:3],
            }
            for idx, item in enumerate(items[:12], 1)
        ]

    def _normalize_rules(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": item.get("id") or f"rule_{idx}",
                "rule": item.get("rule", ""),
                "implication": item.get("implication", ""),
                "evidence": item.get("evidence", [])[:3],
            }
            for idx, item in enumerate(items[:12], 1)
            if item.get("rule")
        ]

    def _normalize_clues(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": item.get("id") or f"clue_{idx}",
                "title": item.get("title") or f"线索 {idx}",
                "summary": item.get("summary", ""),
                "holders": item.get("holders", [])[:4],
                "related_events": item.get("related_events", [])[:4],
                "visibility": item.get("visibility", "private"),
                "evidence": item.get("evidence", [])[:4],
            }
            for idx, item in enumerate(items[:20], 1)
        ]

    def _normalize_secrets(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": item.get("id") or f"secret_{idx}",
                "title": item.get("title") or f"秘密 {idx}",
                "summary": item.get("summary", ""),
                "holders": item.get("holders", [])[:4],
                "exposed": bool(item.get("exposed", False)),
                "related_clues": item.get("related_clues", [])[:4],
                "evidence": item.get("evidence", [])[:4],
            }
            for idx, item in enumerate(items[:20], 1)
        ]

    def _normalize_arcs(self, items: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        event_ids = {item["id"] for item in events}
        return [
            {
                "id": item.get("id") or f"arc_{idx}",
                "title": item.get("title") or f"章节 {idx}",
                "summary": item.get("summary", ""),
                "events": [event_id for event_id in item.get("events", []) if event_id in event_ids],
                "phase": item.get("phase", "setup"),
                "key_node_event_ids": [event_id for event_id in item.get("key_node_event_ids", []) if event_id in event_ids],
            }
            for idx, item in enumerate(items[:8], 1)
        ]
