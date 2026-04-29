"""
故事导入与结构化抽取服务
"""

import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..models.story import (
    EvidenceRef,
    NarrativeBlock,
    PlayableBeat,
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
from .story_cleaner import StoryDataSanitizer
from .story_graph import NarrativeGraphService
from .story_play_runtime import ChatDrivenPlayRuntimeService
from .world_state import CharacterAgentRuntimeService, CharacterRegistry, ContinuationEngine, NarrativePlanner, WorldStateEngine


class StoryExtractionService:
    SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}
    TITLE_SUFFIXES = ["先生", "女士", "老师", "同学", "工程师", "医生", "警官", "组长", "经理"]
    GROUP_LABELS = ["警方", "公司", "网友", "启明科技", "调查组", "董事会", "媒体", "实验室"]
    COMMON_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫房裘缪解应宗丁宣贲邓郁单杭洪包左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊甄曲家封芮储靳焦牧山蔡田"
    TIME_WORDS = {"凌晨", "傍晚", "清晨", "深夜", "一秒", "片刻", "当天", "次日"}
    FUNCTION_WORDS = {"这时", "这样", "什么", "那个", "这个", "的人", "过来", "自己", "于是", "周围"}
    ABSTRACT_LABELS = {"消息", "短信", "电话", "邮件", "数据", "报告", "方案", "节点", "档案", "花纹", "蓝光", "陶罐"}
    LOCATION_WORDS = {"家里", "路边", "大厅", "中央", "窗前", "外面", "里面", "门口", "楼下", "屋里"}
    GENERIC_ROLE_WORDS = {"时间", "时代", "单元", "高贵", "许多", "很多", "一些", "多人", "第一批", "第二批"}
    DESCRIPTOR_WORDS = {"年轻", "整洁", "高贵", "焦灼", "欲望", "空气", "距离", "平静", "高等级"}
    ACTION_VERBS = [
        "联系", "发送", "收到", "追踪", "调查", "进入", "离开", "发现", "公开", "隐藏",
        "怀疑", "质问", "通知", "接近", "交给", "拿走", "追问", "拦下", "联系上", "失联",
        "震动", "响起", "出现", "消失", "切换", "前往", "打开", "查看", "确认", "告诉", "发语音",
    ]
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
        progress_callback=None,
    ) -> StoryWorld:
        self._report_progress(progress_callback, "preprocessing", "正在清理文本并切分为可分析片段。", 16)
        chunks = split_text_into_chunks(source_text, chunk_size=1500, overlap=140)
        extraction = self._extract_assets(
            title=title,
            genre=genre,
            source_text=source_text,
            chunks=chunks,
            progress_callback=progress_callback,
        )

        world = StoryWorld(
            story_id=story_id,
            title=title,
            genre=genre,
            source_type=source_type,
            source_files=source_files,
            summary=extraction["summary"],
            main_storyline=extraction["main_storyline"],
            characters=[StoryCharacter(**item) for item in extraction["characters"]],
            character_registry=extraction.get("character_registry", {}),
            relationships=[StoryRelationship(**item) for item in extraction["relationships"]],
            events=[StoryEvent(**item) for item in extraction["events"]],
            scenes=[StoryScene(**item) for item in extraction["scenes"]],
            world_rules=[WorldRule(**item) for item in extraction["world_rules"]],
            clues=[StoryClue(**item) for item in extraction["clues"]],
            secrets=[StorySecret(**item) for item in extraction["secrets"]],
            arcs=[StoryArc(**item) for item in extraction["arcs"]],
            narrative_blocks=[NarrativeBlock(**item) for item in extraction.get("narrative_blocks", [])],
            playable_beats=[PlayableBeat(**item) for item in extraction.get("playable_beats", [])],
            extraction_meta=extraction["extraction_meta"],
        )

        story_dict = world.to_dict()
        self._report_progress(progress_callback, "building_graph", "正在构建关系图谱与叙事索引。", 62)
        world.graph = NarrativeGraphService.build_graph(story_dict)
        story_dict = world.to_dict()
        self._report_progress(progress_callback, "initializing_world", "正在初始化角色状态和世界状态。", 78)
        world.runtime_agents = CharacterAgentRuntimeService.bootstrap_agents(story_dict)
        story_dict = world.to_dict()
        world.world_state = WorldStateEngine.initialize_world_state(story_dict)
        world.continuation = ContinuationEngine.generate(story_dict, world.world_state)
        self._report_progress(progress_callback, "initializing_play_state", "正在生成初始剧情回合。", 90)
        world.play_state = ChatDrivenPlayRuntimeService.start_session(world.to_dict())
        world.world_state.candidate_event_ids = [
            item["event_id"] for item in (world.play_state.get("event_queue") or [])
            if item.get("status") == "pending"
        ][:6]
        return world

    def _extract_assets(self, title: str, genre: str, source_text: str, chunks: List[str], progress_callback=None) -> Dict[str, Any]:
        heuristic = self._extract_with_pipeline(
            title=title,
            genre=genre,
            source_text=source_text,
            chunks=chunks,
            progress_callback=progress_callback,
        )
        if not self.llm:
            return heuristic
        try:
            refined = self._refine_with_llm(heuristic, title=title, genre=genre, source_text=source_text)
            refined["extraction_meta"]["used_llm"] = True
            return refined
        except Exception:
            heuristic["extraction_meta"]["used_llm"] = False
            return heuristic

    def _extract_with_pipeline(self, title: str, genre: str, source_text: str, chunks: List[str], progress_callback=None) -> Dict[str, Any]:
        preprocessed = self._preprocess_source_text(source_text)
        narrative_sentences = preprocessed["sentences"]
        event_sentences = preprocessed["event_sentences"]
        self._report_progress(progress_callback, "extracting_characters", "正在识别角色、别名和叙事关系。", 30)
        mentions = self._extract_candidate_mentions(narrative_sentences)
        clusters = self._resolve_alias_clusters(mentions)
        characters = self._build_character_cards(clusters, narrative_sentences)
        character_review = {
            "used_llm_review": False,
            "kept": len(characters),
            "discarded": [],
            "merged": [],
            "notes": [],
        }
        if self.llm and characters:
            characters, character_review = self._review_characters_with_llm(
                title=title,
                genre=genre,
                source_text=source_text,
                sentences=narrative_sentences,
                characters=characters,
            )
        self._report_progress(progress_callback, "extracting_events", "正在压缩剧情段并提取关键事件。", 46)
        events = self._build_events(event_sentences, characters)
        relationships = self._build_relationships(narrative_sentences, characters, events)
        scenes = self._build_scenes(preprocessed["scene_chunks"] or chunks, characters)
        clues = self._build_clues(narrative_sentences, characters, events)
        secrets = self._build_secrets(narrative_sentences, characters, clues)
        arcs = self._build_arcs(events)
        rules = self._build_world_rules(narrative_sentences)
        narrative_blocks = self._build_narrative_blocks(events, scenes, clues, characters)
        playable_beats = self._build_playable_beats(events, scenes, clues, characters, narrative_blocks)
        character_registry = CharacterRegistry.build_from_characters(characters)
        validation = self._validate_character_results(characters)
        mainline = " -> ".join(event["title"] for event in events[:5]) or title

        return {
            "summary": " ".join(narrative_sentences[:3])[:260] or title,
            "main_storyline": mainline,
            "characters": characters,
            "character_registry": character_registry,
            "relationships": relationships,
            "events": events,
            "scenes": scenes,
            "world_rules": rules,
            "clues": clues,
            "secrets": secrets,
            "arcs": arcs,
            "narrative_blocks": narrative_blocks,
            "playable_beats": playable_beats,
            "extraction_meta": {
                "used_llm": False,
                "pipeline": [
                    "entity_seed_extraction",
                    "coreference_resolution",
                "role_clustering",
                "importance_scoring",
                "relationship_inference",
                "semantic_event_building",
                "playable_beat_generation",
                "schema_validation",
            ],
                "raw_mentions_count": len(mentions),
                "preprocessing": {
                    "kept_lines": len(preprocessed["kept_lines"]),
                    "filtered_lines": preprocessed["filtered_lines"],
                    "title_lines": preprocessed["title_lines"],
                },
                "character_review": character_review,
                "validation": validation,
            },
        }

    def _report_progress(self, callback, stage: str, message: str, progress: int) -> None:
        if not callback:
            return
        callback(stage=stage, message=message, progress=progress)

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
        for key in ["summary", "main_storyline", "relationships", "events", "scenes", "world_rules", "clues", "secrets", "arcs", "narrative_blocks", "playable_beats"]:
            if refined.get(key):
                normalized[key] = refined[key]
        if not normalized.get("characters") and refined.get("characters"):
            normalized["characters"] = refined["characters"]
        normalized["characters"] = self._normalize_characters(normalized["characters"])
        normalized["character_registry"] = CharacterRegistry.build_from_characters(normalized["characters"])
        normalized["relationships"] = self._normalize_relationships(normalized["relationships"], normalized["characters"])
        normalized["events"] = self._normalize_events(normalized["events"])
        normalized["scenes"] = self._normalize_scenes(normalized["scenes"])
        normalized["world_rules"] = self._normalize_rules(normalized["world_rules"])
        normalized["clues"] = self._normalize_clues(normalized["clues"])
        normalized["secrets"] = self._normalize_secrets(normalized["secrets"])
        normalized["arcs"] = self._normalize_arcs(normalized["arcs"], normalized["events"])
        normalized["narrative_blocks"] = self._normalize_narrative_blocks(
            normalized.get("narrative_blocks", []),
            normalized["events"],
            normalized["scenes"],
            normalized["clues"],
        )
        normalized["playable_beats"] = self._normalize_playable_beats(
            normalized.get("playable_beats", []),
            normalized["events"],
            normalized["narrative_blocks"],
            normalized["characters"],
            normalized["clues"],
            normalized["scenes"],
        )
        normalized["extraction_meta"] = heuristic["extraction_meta"]
        return normalized

    def _preprocess_source_text(self, source_text: str) -> Dict[str, Any]:
        raw_lines = source_text.splitlines()
        kept_lines = []
        filtered_lines = []
        title_lines = []
        for line in raw_lines:
            cleaned = StoryDataSanitizer.clean_text(line)
            reason = self._line_reject_reason(cleaned)
            if reason:
                filtered_lines.append({"line": cleaned or line.strip(), "reason": reason})
                continue
            if self._is_title_line(cleaned):
                title_lines.append(cleaned)
                continue
            kept_lines.append(cleaned)

        joined = "\n".join(kept_lines)
        sentences = [
            StoryDataSanitizer.clean_text(item)
            for item in re.split(r"(?<=[。！？!?])|\n+", joined)
            if StoryDataSanitizer.clean_text(item) and len(StoryDataSanitizer.clean_text(item)) >= 5
        ]
        event_sentences = [item for item in sentences if self._is_semantic_event_sentence(item)]
        scene_chunks = split_text_into_chunks("\n".join(kept_lines), chunk_size=1200, overlap=100) if kept_lines else []
        return {
            "kept_lines": kept_lines,
            "filtered_lines": filtered_lines[:40],
            "title_lines": title_lines[:20],
            "sentences": sentences,
            "event_sentences": event_sentences,
            "scene_chunks": scene_chunks,
        }

    def _split_sentences(self, source_text: str) -> List[str]:
        return self._preprocess_source_text(source_text)["sentences"]

    def _line_reject_reason(self, cleaned_line: str) -> str:
        if not cleaned_line:
            return "empty"
        if len(cleaned_line) < 5:
            return "too_short"
        if re.fullmatch(r"[“”\"'`·,，。.！？!?：:;；\\-—_]+", cleaned_line):
            return "punctuation_only"
        if re.search(r"\.(txt|md|markdown|pdf)$", cleaned_line, re.IGNORECASE):
            return "filename"
        if cleaned_line.startswith("===") or cleaned_line.endswith("==="):
            return "separator"
        return ""

    def _is_title_line(self, cleaned_line: str) -> bool:
        return bool(
            re.match(r"^第[一二三四五六七八九十0-9]+[章节幕卷集部][:：]?", cleaned_line)
            or re.match(r"^[一二三四五六七八九十0-9]+、", cleaned_line)
            or any(keyword in cleaned_line for keyword in ["模块", "改造目标", "技术实现思路", "MVP", "项目背景"])
        )

    def _is_semantic_event_sentence(self, sentence: str) -> bool:
        if not StoryDataSanitizer.is_valid_event_text(sentence):
            return False
        if len(sentence) < 6:
            return False
        if any(verb in sentence for verb in self.ACTION_VERBS):
            return True
        if any(keyword in sentence for keyword in ["说", "问", "告诉", "决定", "收到", "发现", "匿名", "线索", "秘密"]):
            return True
        return False

    def _extract_candidate_mentions(self, sentences: List[str]) -> List[Dict[str, Any]]:
        mentions = []
        dirty_candidate_checker = getattr(self, "_looks_like_dirty_candidate", None)
        for idx, sentence in enumerate(sentences):
            seen = set()
            person_patterns = [
                rf"([{self.COMMON_SURNAMES}][\u4e00-\u9fff]{{1,2}})(?=(?:的|说|问|发|开始|决定|收到|追踪|联系|告诉|看到|怀疑|回头|抬头|低声|拿起|发来|把|将|和|与|，|。|：))",
                rf"(?:给|向|找|问|联系|通知|让|叫|对|与|和)([{self.COMMON_SURNAMES}][\u4e00-\u9fff]{{1,2}})",
            ]
            for pattern in person_patterns:
                for token in re.findall(pattern, sentence):
                    token = token.strip()
                    looks_dirty = dirty_candidate_checker(token, sentence) if callable(dirty_candidate_checker) else False
                    if token in seen or self._is_noise_token(token) or looks_dirty:
                        continue
                    seen.add(token)
                    mentions.append({
                        "mention": token,
                        "sentence": sentence[:180],
                        "sentence_index": idx,
                        "normalized": self._normalize_alias(token),
                    })

            org_candidates = set(self.GROUP_LABELS)
            org_candidates.update(re.findall(r"[\u4e00-\u9fff]{2,10}(?:公司|科技|实验室|媒体|调查组|董事会)", sentence))
            for token in org_candidates:
                if token and token in sentence:
                    mentions.append({
                        "mention": token,
                        "sentence": sentence[:180],
                        "sentence_index": idx,
                        "normalized": token,
                    })
            for label in re.findall(r"(匿名发件人|未知号码|匿名消息|匿名短信|内部节点|陌生车辆)", sentence):
                mentions.append({
                    "mention": label,
                    "sentence": sentence[:180],
                    "sentence_index": idx,
                    "normalized": label,
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
            stats = self._character_stats(cluster["canonical_name"], sentences)
            importance = round(min(1.0, mention_count * 0.08 + stats["action_count"] * 0.16 + stats["relation_count"] * 0.12), 2)
            role_type = self._infer_role_type(cluster["canonical_name"], mention_count, aliases, stats)
            entity_type = self._infer_entity_type(cluster["canonical_name"], role_type)
            if not self._is_valid_character_candidate(cluster["canonical_name"], aliases, stats, importance, role_type, entity_type):
                continue
            motivation = self._infer_motivation(cluster["canonical_name"], sentences)
            hidden_info = self._infer_hidden_info(cluster["canonical_name"], sentences)
            cards.append({
                "id": f"char_{idx}",
                "name": cluster["canonical_name"],
                "canonical_name": cluster["canonical_name"],
                "entity_type": entity_type,
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
                "review_source": "heuristic",
                "review_verdict": "keep",
                "review_notes": [
                    f"规则初筛：提及{mention_count}次，动作相关{stats['action_count']}次，关系相关{stats['relation_count']}次。"
                ],
                "evidence": cluster["evidence"],
            })
        cards.sort(key=lambda item: item["importance_score"], reverse=True)
        return self._normalize_characters(cards[:10])

    def _review_characters_with_llm(
        self,
        title: str,
        genre: str,
        source_text: str,
        sentences: List[str],
        characters: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.llm or not characters:
            return characters, {
                "used_llm_review": False,
                "kept": len(characters),
                "discarded": [],
                "merged": [],
                "notes": [],
            }

        candidates = []
        for item in characters:
            candidates.append({
                "candidate_id": item["id"],
                "canonical_name": item.get("canonical_name") or item.get("name"),
                "aliases": item.get("aliases", [])[:6],
                "entity_type": item.get("entity_type", "character"),
                "role_type": item.get("role_type", "supporting"),
                "importance_score": item.get("importance_score", 0.5),
                "summary": item.get("summary", ""),
                "motivation": item.get("motivation", ""),
                "evidence_quotes": [
                    StoryDataSanitizer.clean_text((evidence or {}).get("quote", ""))[:120]
                    for evidence in item.get("evidence", [])[:3]
                    if StoryDataSanitizer.clean_text((evidence or {}).get("quote", ""))
                ],
            })

        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 NarraWorld 的角色审核器。"
                    "你的任务是审核候选角色，逐个给出 keep、discard 或 merge。"
                    "必须严格过滤不是角色的词，例如时间词、地点词、形容词、抽象名词、普通名词、连词、副词、句子碎片。"
                    "不要创造新角色。只能基于候选列表判断，必要时只允许把 canonical_name 改成该候选已有 alias 或原文中更完整的人名。"
                    "如果一个候选只是地点、修饰词、数量词、语法碎片或普通物件，一律 discard。"
                    "如果两个候选明显是同一个人，用 merge 并指向 merge_into_id。"
                    "输出 JSON，格式为 {\"decisions\": [{\"candidate_id\":\"\",\"verdict\":\"keep|discard|merge\",\"merge_into_id\":\"\",\"canonical_name\":\"\",\"entity_type\":\"character|organization\",\"role_type\":\"core|supporting|group|hidden|functional\",\"summary\":\"\",\"motivation\":\"\",\"traits\":[],\"hidden_info\":[],\"reason\":\"\"}]}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": title,
                        "genre": genre,
                        "text_excerpt": source_text[:12000],
                        "narrative_sentences": sentences[:40],
                        "candidate_characters": candidates,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        try:
            reviewed = self.llm.chat_json(prompt, temperature=0.1, max_tokens=4000)
        except Exception as exc:
            return characters, {
                "used_llm_review": False,
                "kept": len(characters),
                "discarded": [],
                "merged": [],
                "notes": [f"LLM角色审核失败，回退规则结果：{exc}"],
            }

        decisions = {
            item.get("candidate_id"): item
            for item in reviewed.get("decisions", [])
            if item.get("candidate_id")
        }
        card_map = {item["id"]: dict(item) for item in characters}
        keep_ids = set()
        merges = []
        discarded = []

        for item in characters:
            candidate_id = item["id"]
            decision = decisions.get(candidate_id, {})
            verdict = (decision.get("verdict") or "keep").strip().lower()
            if verdict == "discard":
                discarded.append(item.get("canonical_name") or item.get("name") or candidate_id)
                continue
            if verdict == "merge":
                merge_into_id = decision.get("merge_into_id")
                if merge_into_id and merge_into_id in card_map and merge_into_id != candidate_id:
                    keep_ids.add(merge_into_id)
                    merges.append((candidate_id, merge_into_id))
                    continue
            keep_ids.add(candidate_id)

        kept_cards = {item_id: dict(card_map[item_id]) for item_id in keep_ids if item_id in card_map}
        for source_id, target_id in merges:
            source = card_map.get(source_id)
            target = kept_cards.get(target_id)
            if not source or not target:
                continue
            merged_aliases = list(dict.fromkeys(
                target.get("aliases", [])
                + [source.get("canonical_name") or source.get("name", "")]
                + source.get("aliases", [])
            ))
            merged_evidence = (target.get("evidence", []) + source.get("evidence", []))[:4]
            target["aliases"] = [alias for alias in merged_aliases if alias and alias != target.get("canonical_name")][:8]
            target["evidence"] = merged_evidence
            target["importance_score"] = max(float(target.get("importance_score", 0.0)), float(source.get("importance_score", 0.0)))

        for item_id, card in kept_cards.items():
            decision = decisions.get(item_id, {})
            corrected_name = StoryDataSanitizer.clean_text(decision.get("canonical_name", ""))
            if corrected_name:
                aliases = [card.get("canonical_name") or card.get("name", "")] + card.get("aliases", [])
                card["canonical_name"] = corrected_name
                card["name"] = corrected_name
                card["aliases"] = list(dict.fromkeys([alias for alias in aliases if alias and alias != corrected_name]))[:8]
            role_type = StoryDataSanitizer.clean_text(decision.get("role_type", "")) or card.get("role_type", "supporting")
            entity_type = StoryDataSanitizer.clean_text(decision.get("entity_type", "")) or card.get("entity_type", "character")
            card["role_type"] = role_type
            card["entity_type"] = entity_type
            card["role"] = self._infer_role_label(role_type)
            card["summary"] = StoryDataSanitizer.clean_text(decision.get("summary", "")) or card.get("summary", "")
            card["motivation"] = StoryDataSanitizer.clean_text(decision.get("motivation", "")) or card.get("motivation", "")
            if decision.get("traits"):
                card["traits"] = [
                    StoryDataSanitizer.clean_text(text)
                    for text in decision.get("traits", [])[:6]
                    if StoryDataSanitizer.clean_text(text)
                ] or card.get("traits", [])
            if decision.get("hidden_info"):
                card["hidden_info"] = [
                    StoryDataSanitizer.clean_text(text)
                    for text in decision.get("hidden_info", [])[:4]
                    if StoryDataSanitizer.clean_text(text)
                ]
                card["secrets"] = card["hidden_info"][:2]
            reason = StoryDataSanitizer.clean_text(decision.get("reason", ""))
            card["review_source"] = "llm"
            card["review_verdict"] = "keep"
            card["review_notes"] = [reason] if reason else card.get("review_notes", [])

        final_cards = self._normalize_characters(list(kept_cards.values()))
        merged_labels = []
        for source_id, target_id in merges:
            source_name = (card_map.get(source_id) or {}).get("canonical_name") or source_id
            target_name = (card_map.get(target_id) or {}).get("canonical_name") or target_id
            merged_labels.append(f"{source_name} -> {target_name}")
        return final_cards, {
            "used_llm_review": True,
            "kept": len(final_cards),
            "discarded": discarded[:12],
            "merged": merged_labels[:12],
            "notes": [StoryDataSanitizer.clean_text(note) for note in reviewed.get("notes", [])[:6] if StoryDataSanitizer.clean_text(note)],
        }

    def _build_events(self, sentences: List[str], characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events = []
        character_map = self._character_lookup(characters)
        previous_event_id = None
        previous_actor = ""
        for idx, sentence in enumerate(sentences[:16], 1):
            event_shape = self._extract_event_shape(sentence, character_map, characters, previous_actor=previous_actor)
            if not event_shape:
                continue
            participants = event_shape["participants"]
            event_type = event_shape["event_type"]
            is_key_node = idx <= 3 or event_type == "decision"
            event_id = f"event_{idx}"
            events.append({
                "id": event_id,
                "title": event_shape["title"],
                "summary": sentence[:180],
                "order": idx,
                "actor": event_shape["actor"],
                "action": event_shape["action"],
                "target": event_shape["target"],
                "event_type": event_type,
                "participants": participants,
                "scenes": [f"scene_{min(idx, 4)}"],
                "clues": [f"clue_{idx}"] if self._sentence_has_clue(sentence) else [],
                "status": "pending",
                "trigger_conditions": ["上一个关键节点已结束"] if previous_event_id else ["故事进入起始状态"],
                "preconditions": [events[-1]["summary"][:80]] if events else [],
                "consequences": [self._event_consequence(sentence)],
                "outcomes": [sentence[:80]],
                "caused_by": [previous_event_id] if previous_event_id else [],
                "leads_to": [],
                "tags": ["main"] if idx <= 5 else ["side"],
                "is_key_node": is_key_node,
                "evidence": [{
                    "quote": sentence[:180],
                    "source": "text",
                    "chunk_index": idx - 1,
                    "note": "事件原文证据",
                }],
            })
            if previous_event_id:
                events[-2]["leads_to"] = [event_id]
            previous_event_id = event_id
            previous_actor = event_shape["actor"] or previous_actor
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
        lookup = self._character_lookup(characters)
        for idx, chunk in enumerate(chunks[:4], 1):
            participants = self._match_characters_in_sentence(chunk, lookup)
            scenes.append({
                "id": f"scene_{idx}",
                "name": f"场景 {idx}",
                "location": self._infer_location(chunk, idx),
                "summary": chunk[:180],
                "mood": "紧绷" if idx == 1 else "推进中",
                "participants": participants[:4],
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
        lookup = self._character_lookup(characters)
        for idx, sentence in enumerate([item for item in sentences if self._sentence_has_clue(item)][:5], 1):
            matched_characters = self._match_characters_in_sentence(sentence, lookup)
            related_events = [event for event in events if sentence[:20] in event["summary"]]
            holders = matched_characters[:2]
            if not holders and related_events:
                holders = (related_events[0].get("participants") or [])[:2]
            clues.append({
                "id": f"clue_{idx}",
                "title": f"线索 {idx}",
                "summary": sentence[:100],
                "holders": holders,
                "related_events": [event["id"] for event in related_events][:1] or [f"event_{idx}"],
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
                "holders": (events[0].get("participants") or [])[:2],
                "related_events": [events[0]["id"]],
                "visibility": "private",
                "evidence": events[0].get("evidence", [])[:1],
            })
        return self._normalize_clues(clues)

    def _build_secrets(self, sentences: List[str], characters: List[Dict[str, Any]], clues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        secret_sentences = [item for item in sentences if any(key in item for key in ["秘密", "匿名", "失踪", "隐瞒", "未知"])]
        secrets = []
        lookup = self._character_lookup(characters)
        for idx, sentence in enumerate(secret_sentences[:3], 1):
            matched_characters = self._match_characters_in_sentence(sentence, lookup)
            secrets.append({
                "id": f"secret_{idx}",
                "title": f"秘密 {idx}",
                "summary": sentence[:110],
                "holders": matched_characters[:2],
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

    def _build_narrative_blocks(
        self,
        events: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not events:
            return []
        scene_map = {item["id"]: item for item in scenes}
        clue_map = {item["id"]: item for item in clues}
        char_map = {item["id"]: item for item in characters}
        chunk_size = 3 if len(events) <= 9 else 4
        blocks = []
        for block_index, start in enumerate(range(0, len(events), chunk_size), 1):
            batch = events[start:start + chunk_size]
            if not batch:
                continue
            scene_id = next(((event.get("scenes") or [None])[0] for event in batch if (event.get("scenes") or [None])[0]), None)
            scene = scene_map.get(scene_id, {})
            participant_ids = []
            clue_ids = []
            for event in batch:
                for participant in event.get("participants", []):
                    if participant not in participant_ids:
                        participant_ids.append(participant)
                for clue_id in event.get("clues", []):
                    if clue_id not in clue_ids:
                        clue_ids.append(clue_id)
            participant_names = [
                char_map[item]["canonical_name"] or char_map[item]["name"]
                for item in participant_ids if item in char_map
            ]
            clue_titles = [clue_map[item]["title"] for item in clue_ids if item in clue_map]
            lead_event = batch[0]
            evidence = []
            for event in batch:
                evidence.extend(event.get("evidence", [])[:1])
            summary = "；".join(event["summary"][:46] for event in batch[:3])
            blocks.append({
                "id": f"block_{block_index}",
                "title": lead_event["title"],
                "summary": summary[:220],
                "situation": self._build_block_situation(batch, scene, participant_names),
                "conflict": self._build_block_conflict(participant_names, clue_titles),
                "player_implication": self._build_block_player_implication(participant_names),
                "risk": self._build_block_risk(participant_names, clue_titles),
                "objective": self._build_block_objective(participant_names, clue_titles),
                "action_vectors": self._block_action_vectors(participant_names, clue_titles),
                "event_ids": [event["id"] for event in batch],
                "participant_ids": participant_ids[:5],
                "clue_ids": clue_ids[:4],
                "scene_id": scene_id,
                "phase": self._phase_for_block(block_index, len(events)),
                "evidence": evidence[:3],
            })
        return self._normalize_narrative_blocks(blocks, events, scenes, clues)

    def _build_playable_beats(
        self,
        events: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
        narrative_blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not events:
            return []
        event_map = {item["id"]: item for item in events}
        scene_map = {item["id"]: item for item in scenes}
        clue_map = {item["id"]: item for item in clues}
        char_map = {item["id"]: item for item in characters if item.get("entity_type", "character") == "character"}
        beats = []
        for index, block in enumerate(narrative_blocks, 1):
            source_events = [event_map[event_id] for event_id in block.get("event_ids", []) if event_id in event_map]
            if not source_events:
                continue
            lead_event = source_events[0]
            scene = scene_map.get(block.get("scene_id") or (lead_event.get("scenes") or [None])[0], {})
            present_character_ids = [
                char_id
                for char_id in block.get("participant_ids", [])
                if char_id in char_map and StoryDataSanitizer.is_valid_character_name(
                    char_map[char_id].get("canonical_name") or char_map[char_id].get("name") or "",
                    role_type=char_map[char_id].get("role_type", ""),
                    importance_score=float(char_map[char_id].get("importance_score", 0.0)),
                    aliases=char_map[char_id].get("aliases", []),
                )
            ][:4]
            present_names = [
                char_map[char_id].get("canonical_name") or char_map[char_id].get("name")
                for char_id in present_character_ids
            ]
            clue_titles = [
                clue_map[clue_id]["title"]
                for clue_id in block.get("clue_ids", [])
                if clue_id in clue_map
            ]
            importance = self._beat_importance(source_events, block)
            should_render_full_turn = self._beat_should_render_full_turn(source_events, present_character_ids, block, importance)
            scene_label = StoryDataSanitizer.normalize_scene_label(scene.get("name") or scene.get("location") or "")
            fallback_situation = block.get("situation") or lead_event.get("summary", "")
            beats.append({
                "beat_id": f"beat_{index}",
                "source_event_ids": [event["id"] for event in source_events],
                "source_block_id": block.get("id"),
                "importance": importance,
                "first_person_situation": StoryDataSanitizer.sanitize_playable_situation(
                    self._beat_first_person_situation(block, source_events, scene, present_names),
                    scene_label=scene_label,
                    fallback=fallback_situation,
                ),
                "player_objective": StoryDataSanitizer.sanitize_playable_objective(
                    self._beat_player_objective(block, present_names, clue_titles),
                    present_names=present_names,
                ),
                "dramatic_question": StoryDataSanitizer.sanitize_playable_question(
                    self._beat_dramatic_question(block, present_names, clue_titles),
                    present_names=present_names,
                ),
                "present_character_ids": present_character_ids,
                "suggested_action_intents": self._beat_action_intents(importance, present_character_ids, clue_titles),
                "revealed_clue_ids": block.get("clue_ids", [])[:4],
                "risk_summary": StoryDataSanitizer.sanitize_playable_risk(
                    self._beat_risk_summary(block, present_names, clue_titles, importance),
                    present_names=present_names,
                ),
                "should_render_full_turn": should_render_full_turn,
                "scene_id": block.get("scene_id") or scene.get("id"),
                "phase": block.get("phase", "setup"),
                "evidence": block.get("evidence", [])[:3] or lead_event.get("evidence", [])[:2],
            })
        return self._normalize_playable_beats(beats, events, narrative_blocks, characters, clues, scenes)

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

    def _character_stats(self, canonical_name: str, sentences: List[str]) -> Dict[str, int]:
        action_count = 0
        relation_count = 0
        mention_count = 0
        escaped = re.escape(canonical_name)
        for sentence in sentences:
            if canonical_name not in sentence:
                continue
            mention_count += 1
            if any(re.search(rf"{escaped}.{{0,6}}{re.escape(verb)}|{re.escape(verb)}.{{0,6}}{escaped}", sentence) for verb in self.ACTION_VERBS):
                action_count += 1
            if any(
                re.search(rf"{escaped}.{{0,10}}{re.escape(keyword)}|{re.escape(keyword)}.{{0,10}}{escaped}", sentence)
                for keywords in self.RELATION_KEYWORDS.values()
                for keyword in keywords
            ):
                relation_count += 1
        return {
            "mention_count": mention_count,
            "action_count": action_count,
            "relation_count": relation_count,
        }

    def _infer_role_type(self, canonical_name: str, mention_count: int, aliases: List[str], stats: Dict[str, int]) -> str:
        if canonical_name in self.GROUP_LABELS or any(hint in canonical_name for hint in ["科技", "公司", "实验室", "董事会", "媒体"]):
            return "group"
        if "匿名" in canonical_name or "未知" in canonical_name:
            return "hidden"
        if mention_count >= 4 or stats["action_count"] >= 2:
            return "core"
        if mention_count >= 2 or stats["relation_count"] >= 1:
            return "supporting"
        return "functional"

    def _infer_entity_type(self, canonical_name: str, role_type: str) -> str:
        if role_type == "group" or any(hint in canonical_name for hint in ["科技", "公司", "实验室", "董事会", "媒体", "警方"]):
            return "organization"
        return "character"

    def _is_valid_character_candidate(
        self,
        canonical_name: str,
        aliases: List[str],
        stats: Dict[str, int],
        importance: float,
        role_type: str,
        entity_type: str,
    ) -> bool:
        if canonical_name in self.TIME_WORDS or canonical_name in self.FUNCTION_WORDS or canonical_name in self.ABSTRACT_LABELS:
            return False
        if not StoryDataSanitizer.is_valid_character_name(
            canonical_name,
            role_type=role_type,
            importance_score=max(importance, 0.2 if stats["mention_count"] >= 2 else importance),
            aliases=aliases,
        ):
            return False
        if entity_type == "character":
            if stats["action_count"] == 0 and stats["relation_count"] == 0 and stats["mention_count"] < 2:
                return False
        return True

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
        if any(hint in name for hint in ["科技", "公司", "实验室", "董事会", "警方"]):
            return "维持组织稳定并控制风险。"
        return "查清当前局势，并保护关键关系。"

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

    def _extract_event_shape(
        self,
        sentence: str,
        lookup: Dict[str, str],
        characters: List[Dict[str, Any]],
        previous_actor: str = "",
    ) -> Optional[Dict[str, Any]]:
        participants = self._match_characters_in_sentence(sentence, lookup)
        actor = participants[0] if participants else ""
        target = participants[1] if len(participants) > 1 else ""
        if sentence.startswith(("她", "他", "她的", "他的")) and previous_actor:
            actor = previous_actor
            if actor not in participants:
                participants = [actor, *participants][:4]
            if not target and len(participants) > 1:
                target = participants[1]
        action = next((verb for verb in self.ACTION_VERBS if verb in sentence), "")
        if not action:
            if any(word in sentence for word in ["说", "问", "告诉", "发来"]):
                action = "告知"
            elif any(word in sentence for word in ["决定", "选择", "必须"]):
                action = "抉择"
            elif any(word in sentence for word in ["匿名", "线索", "秘密"]):
                action = "揭示"
        if not action:
            return None

        name_map = {item["id"]: (item.get("canonical_name") or item["name"]) for item in characters}
        actor_name = name_map.get(actor, "")
        target_name = name_map.get(target, "")
        title = self._build_event_title(actor_name, action, target_name, sentence)
        event_type = "decision" if action == "抉择" else "plot"
        if any(word in sentence for word in ["短信", "消息", "电话", "发语音"]):
            event_type = "communication"
        return {
            "actor": actor,
            "action": action,
            "target": target,
            "participants": participants,
            "title": title,
            "event_type": event_type,
        }

    def _build_event_title(self, actor_name: str, action: str, target_name: str, sentence: str) -> str:
        if actor_name and target_name:
            return f"{actor_name}{action}{target_name}"
        if actor_name:
            return f"{actor_name}{action}"
        cleaned = StoryDataSanitizer.clean_text(sentence)
        return cleaned[:28]

    def _event_consequence(self, sentence: str) -> str:
        cleaned = StoryDataSanitizer.clean_text(sentence)
        if len(cleaned) <= 60:
            return cleaned
        return f"{cleaned[:58]}…"

    def _phase_for_block(self, block_index: int, total_events: int) -> str:
        ratio = min(1.0, (block_index * 2) / max(total_events, 1))
        if ratio < 0.25:
            return "setup"
        if ratio < 0.6:
            return "confrontation"
        if ratio < 0.85:
            return "climax"
        return "resolution"

    def _build_block_situation(self, events: List[Dict[str, Any]], scene: Dict[str, Any], participant_names: List[str]) -> str:
        scene_name = StoryDataSanitizer.clean_text(scene.get("location", "")) or StoryDataSanitizer.clean_text(scene.get("name", "")) or "当前场景"
        summaries = [StoryDataSanitizer.clean_text(event.get("summary", "")) for event in events if StoryDataSanitizer.clean_text(event.get("summary", ""))]
        opening = summaries[0] if summaries else ""
        middle = summaries[1] if len(summaries) > 1 else ""
        ending = summaries[-1] if summaries else ""
        names = "、".join(participant_names[:3]) or "这些人"
        sentences = [f"这一段发生在{scene_name}。{names}没有正面摊牌，但每个人的动作都比话更早露出意图。"]
        if opening:
            sentences.append(f"开头先发生的是：{opening[:54]}。")
        if middle and middle != opening:
            sentences.append(f"紧跟着又冒出一处更不对劲的细节：{middle[:54]}。")
        if ending and ending not in {opening, middle}:
            sentences.append(f"等到这段结束时，局面已经被推到：{ending[:58]}。")
        return " ".join(sentences[:4])

    def _build_block_conflict(self, participant_names: List[str], clue_titles: List[str]) -> str:
        if clue_titles:
            return f"真正的冲突不在表面上的几句话，而在“{'、'.join(clue_titles[:2])}”究竟是谁先握住、谁先拿它改写局面。"
        if participant_names:
            return f"真正的问题是，{participant_names[0]}的动作到底是在提醒主角，还是在故意把主角往错误方向引。"
        return "真正的问题不是表面发生了什么，而是谁在控制信息先后落到主角手里的顺序。"

    def _build_block_player_implication(self, participant_names: List[str]) -> str:
        if participant_names:
            return f"这意味着主角必须尽快判断{participant_names[0]}值不值得继续逼近，同时避免被别人先带进他们设好的话题里。"
        return "这意味着主角不能只等局面自己说清楚，因为别人会先利用这份迟疑。"

    def _build_block_risk(self, participant_names: List[str], clue_titles: List[str]) -> str:
        if clue_titles:
            return f"如果主角过早暴露自己已经注意到“{clue_titles[0]}”，在场的人会立刻改写对主角的说法和态度。"
        if participant_names:
            return f"如果主角错判{participant_names[0]}的立场，接下来几轮对话都会建立在错误前提上。"
        return "最大的风险是，信息还没完整，别人却已经开始根据主角的反应下注。"

    def _build_block_objective(self, participant_names: List[str], clue_titles: List[str]) -> str:
        if participant_names and clue_titles:
            return f"先借{participant_names[0]}这条线确认“{clue_titles[0]}”是真是假，再决定要不要让别人看出主角已经知道多少。"
        if participant_names:
            return f"先弄清楚{participant_names[0]}到底是在给提示、下套，还是单纯拖时间。"
        return "先把真假信号分开，再决定剧情该往前顶，还是先按住不动。"

    def _block_action_vectors(self, participant_names: List[str], clue_titles: List[str]) -> List[str]:
        vectors = []
        if participant_names:
            target = participant_names[0]
            vectors.extend([
                f"盯住{target}，直接把最要紧的那句话当面问出去",
                f"把话停在半截，只看{target}会不会自己补后半句",
            ])
        if clue_titles:
            vectors.append(f"先按住眼前这场对话，转去核实“{clue_titles[0]}”")
        vectors.append("什么都不接，先把沉默留给房间里的人")
        return vectors[:4]

    def _sentence_has_clue(self, sentence: str) -> bool:
        return any(keyword in sentence for keyword in ["线索", "秘密", "短信", "车票", "证据", "档案", "编号", "匿名", "节点"])

    def _normalize_alias(self, token: str) -> str:
        result = token
        for suffix in self.TITLE_SUFFIXES:
            result = result.replace(suffix, "")
        return result.strip() or token

    def _looks_like_dirty_candidate(self, token: str, sentence: str) -> bool:
        if token in self.LOCATION_WORDS or token in self.GENERIC_ROLE_WORDS or token in self.DESCRIPTOR_WORDS:
            return True
        if any(hint in token for hint in self.LOCATION_WORDS | self.GENERIC_ROLE_WORDS):
            return True
        if re.search(rf"(并不|不是|没有|很|更|太){re.escape(token)}", sentence):
            return True
        if token.endswith(("里", "边", "前", "后")) and len(token) <= 2:
            return True
        return False

    def _is_noise_token(self, token: str) -> bool:
        blacklist = {
            "他们", "我们", "自己", "如果", "因为", "然后", "但是", "故事", "世界",
            "设定", "没有", "一个", "不是", "可以", "时候", "这个", "那个", "你们",
            "消息", "一秒", "不要相信", "方案", "设计", "剧情游戏", "群像推演", "式剧情游",
            "家里", "路边", "时间", "时代", "单元", "高贵", "许多", "于是", "花纹",
        }
        return (
            token in blacklist
            or token in self.TIME_WORDS
            or token in self.FUNCTION_WORDS
            or token in self.LOCATION_WORDS
            or token in self.GENERIC_ROLE_WORDS
            or token in self.DESCRIPTOR_WORDS
            or len(token.strip()) < 2
            or bool(re.search(r"[=《》/\\]", token))
            or token.endswith(".tx")
        )

    def _normalize_characters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        seen = set()
        for idx, item in enumerate(items[:12], 1):
            canonical_name = StoryDataSanitizer.clean_text(item.get("canonical_name") or item.get("name") or f"角色{idx}")
            aliases = [StoryDataSanitizer.clean_text(alias) for alias in item.get("aliases", []) if StoryDataSanitizer.clean_text(alias)]
            importance_score = float(item.get("importance_score", 0.5))
            role_type = item.get("role_type", "supporting")
            if not StoryDataSanitizer.is_valid_character_name(canonical_name, role_type=role_type, importance_score=importance_score, aliases=aliases):
                continue
            if canonical_name in seen:
                continue
            seen.add(canonical_name)
            normalized.append({
                "id": item.get("id") or f"char_{idx}",
                "name": item.get("name") or canonical_name,
                "canonical_name": canonical_name,
                "entity_type": item.get("entity_type", "character"),
                "aliases": list(dict.fromkeys([alias for alias in aliases if alias != canonical_name]))[:8],
                "role": item.get("role", ""),
                "role_type": role_type,
                "summary": StoryDataSanitizer.clean_text(item.get("summary", "")),
                "persona": StoryDataSanitizer.clean_text(item.get("persona", "")),
                "motivation": StoryDataSanitizer.clean_text(item.get("motivation", "")),
                "hidden_info": [StoryDataSanitizer.clean_text(v) for v in item.get("hidden_info", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "goals": [StoryDataSanitizer.clean_text(v) for v in item.get("goals", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "traits": [StoryDataSanitizer.clean_text(v) for v in item.get("traits", [])[:6] if StoryDataSanitizer.clean_text(v)],
                "secrets": [StoryDataSanitizer.clean_text(v) for v in item.get("secrets", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "beliefs": [StoryDataSanitizer.clean_text(v) for v in item.get("beliefs", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "knowledge_scope": [StoryDataSanitizer.clean_text(v) for v in item.get("knowledge_scope", [])[:6] if StoryDataSanitizer.clean_text(v)],
                "importance_score": importance_score,
                "status": item.get("status", "active"),
                "review_source": item.get("review_source", "heuristic"),
                "review_verdict": item.get("review_verdict", "keep"),
                "review_notes": [StoryDataSanitizer.clean_text(v) for v in item.get("review_notes", [])[:4] if StoryDataSanitizer.clean_text(v)],
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
            title = StoryDataSanitizer.clean_text(item.get("title") or item.get("summary") or f"事件{idx}")
            summary = StoryDataSanitizer.clean_text(item.get("summary", ""))
            if not StoryDataSanitizer.is_valid_event_text(title):
                continue
            normalized.append({
                "id": item.get("id") or f"event_{idx}",
                "title": title,
                "summary": summary,
                "order": int(item.get("order", idx)),
                "actor": item.get("actor", ""),
                "action": item.get("action", ""),
                "target": item.get("target", ""),
                "event_type": item.get("event_type", "plot"),
                "participants": item.get("participants", [])[:6],
                "scenes": item.get("scenes", [])[:3],
                "clues": item.get("clues", [])[:4],
                "status": item.get("status", "pending"),
                "trigger_conditions": [StoryDataSanitizer.clean_text(v) for v in item.get("trigger_conditions", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "preconditions": [StoryDataSanitizer.clean_text(v) for v in item.get("preconditions", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "consequences": [StoryDataSanitizer.clean_text(v) for v in item.get("consequences", [])[:4] if StoryDataSanitizer.clean_text(v)],
                "outcomes": [StoryDataSanitizer.clean_text(v) for v in item.get("outcomes", [])[:4] if StoryDataSanitizer.clean_text(v)],
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

    def _normalize_narrative_blocks(
        self,
        items: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        valid_event_ids = {item["id"] for item in events}
        valid_scene_ids = {item["id"] for item in scenes}
        valid_clue_ids = {item["id"] for item in clues}
        normalized = []
        for idx, item in enumerate(items[:10], 1):
            event_ids = [event_id for event_id in item.get("event_ids", []) if event_id in valid_event_ids]
            if not event_ids:
                continue
            normalized.append({
                "id": item.get("id") or f"block_{idx}",
                "title": StoryDataSanitizer.clean_text(item.get("title") or item.get("summary") or f"叙事块 {idx}")[:48],
                "summary": StoryDataSanitizer.clean_text(item.get("summary", ""))[:220],
                "situation": StoryDataSanitizer.clean_text(item.get("situation", ""))[:220],
                "conflict": StoryDataSanitizer.clean_text(item.get("conflict", ""))[:180],
                "player_implication": StoryDataSanitizer.clean_text(item.get("player_implication", ""))[:180],
                "risk": StoryDataSanitizer.clean_text(item.get("risk", ""))[:180],
                "objective": StoryDataSanitizer.clean_text(item.get("objective", ""))[:180],
                "action_vectors": [
                    StoryDataSanitizer.clean_text(text)[:72]
                    for text in item.get("action_vectors", [])
                    if StoryDataSanitizer.clean_text(text)
                ][:5],
                "event_ids": event_ids,
                "participant_ids": item.get("participant_ids", [])[:6],
                "clue_ids": [clue_id for clue_id in item.get("clue_ids", []) if clue_id in valid_clue_ids][:4],
                "scene_id": item.get("scene_id") if item.get("scene_id") in valid_scene_ids else None,
                "phase": item.get("phase", "setup"),
                "evidence": item.get("evidence", [])[:4],
            })
        return normalized

    def _normalize_playable_beats(
        self,
        items: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        narrative_blocks: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
        clues: List[Dict[str, Any]],
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        valid_event_ids = {item["id"] for item in events}
        valid_block_ids = {item["id"] for item in narrative_blocks}
        valid_character_ids = {
            item["id"] for item in characters
            if item.get("entity_type", "character") == "character"
        }
        valid_clue_ids = {item["id"] for item in clues}
        valid_scene_ids = {item["id"] for item in scenes}
        character_map = {
            item["id"]: item
            for item in characters
            if item.get("entity_type", "character") == "character"
        }
        scene_map = {item["id"]: item for item in scenes}
        normalized = []
        for idx, item in enumerate(items[:16], 1):
            source_event_ids = [event_id for event_id in item.get("source_event_ids", []) if event_id in valid_event_ids]
            if not source_event_ids:
                continue
            importance = item.get("importance", "minor")
            if importance not in {"major", "minor", "transition", "background"}:
                importance = "minor"
            present_character_ids = [
                item_id for item_id in item.get("present_character_ids", [])
                if item_id in valid_character_ids
            ][:4]
            present_names = [
                character_map[item_id].get("canonical_name") or character_map[item_id].get("name")
                for item_id in present_character_ids
                if item_id in character_map
            ]
            scene_id = item.get("scene_id") if item.get("scene_id") in valid_scene_ids else None
            scene_label = StoryDataSanitizer.normalize_scene_label((scene_map.get(scene_id) or {}).get("name") or (scene_map.get(scene_id) or {}).get("location") or "")
            normalized.append({
                "beat_id": item.get("beat_id") or f"beat_{idx}",
                "source_event_ids": source_event_ids,
                "source_block_id": item.get("source_block_id") if item.get("source_block_id") in valid_block_ids else None,
                "importance": importance,
                "first_person_situation": StoryDataSanitizer.sanitize_playable_situation(
                    item.get("first_person_situation", ""),
                    scene_label=scene_label,
                    fallback=item.get("summary", ""),
                )[:280],
                "player_objective": StoryDataSanitizer.sanitize_playable_objective(
                    item.get("player_objective", ""),
                    present_names=present_names,
                )[:180],
                "dramatic_question": StoryDataSanitizer.sanitize_playable_question(
                    item.get("dramatic_question", ""),
                    present_names=present_names,
                )[:180],
                "present_character_ids": present_character_ids,
                "suggested_action_intents": [
                    StoryDataSanitizer.clean_text(intent)
                    for intent in item.get("suggested_action_intents", [])
                    if StoryDataSanitizer.clean_text(intent)
                ][:5],
                "revealed_clue_ids": [
                    clue_id for clue_id in item.get("revealed_clue_ids", [])
                    if clue_id in valid_clue_ids
                ][:4],
                "risk_summary": StoryDataSanitizer.sanitize_playable_risk(
                    item.get("risk_summary", ""),
                    present_names=present_names,
                )[:180],
                "should_render_full_turn": bool(item.get("should_render_full_turn", True)),
                "scene_id": scene_id,
                "phase": item.get("phase", "setup"),
                "evidence": item.get("evidence", [])[:4],
            })
        return normalized

    def _beat_importance(self, source_events: List[Dict[str, Any]], block: Dict[str, Any]) -> str:
        if any(event.get("is_key_node") or "main" in event.get("tags", []) for event in source_events):
            return "major"
        if any(event.get("event_type") == "transition" for event in source_events) or block.get("phase") == "confrontation":
            return "transition"
        if not block.get("participant_ids") and not block.get("clue_ids"):
            return "background"
        return "minor"

    def _beat_should_render_full_turn(
        self,
        source_events: List[Dict[str, Any]],
        present_character_ids: List[str],
        block: Dict[str, Any],
        importance: str,
    ) -> bool:
        if importance == "major":
            return True
        if importance == "background":
            return False
        if present_character_ids and (block.get("clue_ids") or any(event.get("evidence") for event in source_events)):
            return True
        return importance == "minor"

    def _beat_first_person_situation(
        self,
        block: Dict[str, Any],
        source_events: List[Dict[str, Any]],
        scene: Dict[str, Any],
        present_names: List[str],
    ) -> str:
        scene_name = StoryDataSanitizer.clean_text(scene.get("name", "")) or "眼前这个场面"
        opener = StoryDataSanitizer.clean_text(block.get("situation", "")) or StoryDataSanitizer.clean_text(source_events[0].get("summary", ""))
        conflict = StoryDataSanitizer.clean_text(block.get("conflict", ""))
        if present_names:
            names = "、".join(present_names[:2])
            return f"你站在{scene_name}里，最先撞上的就是{StoryDataSanitizer.clean_text(opener)[:72]}。{names}都在场，但真正不对劲的地方不在他们说了什么，而在{conflict or '谁都没有把话说满'}。"
        return f"你站在{scene_name}里，先看到的是{StoryDataSanitizer.clean_text(opener)[:72]}。真正让你不能立刻表态的，是{conflict or '这轮变化背后的意图还没有露出来'}。"

    def _beat_player_objective(self, block: Dict[str, Any], present_names: List[str], clue_titles: List[str]) -> str:
        if block.get("objective"):
            return StoryDataSanitizer.clean_text(block["objective"])
        if clue_titles:
            return f"先确认“{clue_titles[0]}”到底是谁故意放到你面前的，再决定要不要立刻拆穿。"
        if present_names:
            return f"先判断{present_names[0]}的话是在提醒你、拦你，还是在故意把你往错误方向推。"
        return "先确认眼前这轮变化是谁在主导，再决定自己要不要立刻表态。"

    def _beat_dramatic_question(self, block: Dict[str, Any], present_names: List[str], clue_titles: List[str]) -> str:
        if present_names:
            return f"你要不要当面逼问{present_names[0]}？"
        if clue_titles:
            return f"你要不要立刻去核实“{clue_titles[0]}”？"
        return "你要不要现在就把自己的判断亮出来？"

    def _beat_action_intents(self, importance: str, present_character_ids: List[str], clue_titles: List[str]) -> List[str]:
        if importance == "background":
            return ["observe"]
        intents = []
        if present_character_ids:
            intents.extend(["press_character", "probe_character"])
        if clue_titles:
            intents.append("verify_clue")
        if len(present_character_ids) > 1:
            intents.append("reveal_partial")
        intents.extend(["observe", "reposition"])
        return list(dict.fromkeys(intents))[:5]

    def _beat_risk_summary(self, block: Dict[str, Any], present_names: List[str], clue_titles: List[str], importance: str) -> str:
        if block.get("risk"):
            return StoryDataSanitizer.clean_text(block["risk"])
        if importance == "background":
            return "这一拍不值得你立刻亮底，但错过也可能让真正危险的人先藏回去。"
        if present_names:
            return f"{'、'.join(present_names[:2])}都在等你的反应。你一旦太快把话说穿，后面每个人都会顺着你的判断来布局。"
        if clue_titles:
            return f"你现在碰“{clue_titles[0]}”，很可能会让真正持有它的人提前警觉。"
        return "你还没看清全局，但别人已经开始根据你的停顿和措辞重新判断你。"
