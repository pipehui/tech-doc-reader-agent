from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class ParserResult(BaseModel):
    topic: str = ""
    core_content: str = ""
    key_concepts: list[str] = Field(default_factory=list)
    mechanisms: list[str] = Field(default_factory=list)
    relevant_conclusions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    relation_hints: list[str] = Field(default_factory=list)
    explanation_focus: list[str] = Field(default_factory=list)
    raw_text: str = ""
    parsed: bool = False


class RelationResult(BaseModel):
    target: str = ""
    target_features: list[str] = Field(default_factory=list)
    user_known_concepts: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    recommended_analogies: list[str] = Field(default_factory=list)
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    explanation_focus: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    raw_text: str = ""
    parsed: bool = False


ResultKind = Literal["parser", "relation"]

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(?P<title>.+?)\s*$")
_BOLD_HEADING_RE = re.compile(r"^\s*(?:\d+[.、]\s*)?\*\*(?P<title>.+?)\*\*\s*[:：]?\s*(?P<rest>.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)(?P<text>.+?)\s*$")
_TABLE_SPLIT_RE = re.compile(r"\s*\|\s*")


def parse_structured_result(kind: ResultKind, text: str) -> dict:
    if kind == "parser":
        return parse_parser_result(text).model_dump()
    return parse_relation_result(text).model_dump()


def parse_parser_result(text: str) -> ParserResult:
    sections = _extract_sections(text, _PARSER_HEADING_ALIASES)
    result = ParserResult(
        topic=_section_text(sections.get("topic", "")),
        core_content=_section_text(sections.get("core_content", "")),
        key_concepts=_section_items(sections.get("key_concepts", "")),
        mechanisms=_section_items(sections.get("mechanisms", "")),
        relevant_conclusions=_section_items(sections.get("relevant_conclusions", "")),
        evidence=_section_items(sections.get("evidence", "")),
        gaps=_section_items(sections.get("gaps", "")),
        relation_hints=_section_items(sections.get("relation_hints", "")),
        explanation_focus=_section_items(sections.get("explanation_focus", "")),
        raw_text=text,
        parsed=bool(sections),
    )
    return result


def parse_relation_result(text: str) -> RelationResult:
    sections = _extract_sections(text, _RELATION_HEADING_ALIASES)
    result = RelationResult(
        target=_section_text(sections.get("target", "")),
        target_features=_section_items(sections.get("target_features", "")),
        user_known_concepts=_section_items(sections.get("user_known_concepts", "")),
        candidates=_section_items(sections.get("candidates", "")),
        recommended_analogies=_section_items(sections.get("recommended_analogies", "")),
        similarities=_section_items(sections.get("similarities", "")),
        differences=_section_items(sections.get("differences", "")),
        boundaries=_section_items(sections.get("boundaries", "")),
        explanation_focus=_section_items(sections.get("explanation_focus", "")),
        gaps=_section_items(sections.get("gaps", "")),
        raw_text=text,
        parsed=bool(sections),
    )
    return result


def _normalize_heading(value: str) -> str:
    normalized = re.sub(r"[`*_#\s:：/／、，,。.!！?？（）()【】\[\]-]", "", value)
    return normalized.lower()


def _extract_heading(line: str, aliases: dict[str, str]) -> tuple[str | None, str]:
    match = _HEADING_RE.match(line)
    if match:
        title = match.group("title").strip()
        key = aliases.get(_normalize_heading(title))
        return key, ""

    match = _BOLD_HEADING_RE.match(line)
    if match:
        title = match.group("title").strip()
        key = aliases.get(_normalize_heading(title))
        if key:
            return key, match.group("rest").strip()

    plain_match = re.match(r"^\s*(?:\d+[.、]\s*)?(?P<title>[^:：]{2,50})\s*[:：]\s*(?P<rest>.*)$", line)
    if plain_match:
        key = aliases.get(_normalize_heading(plain_match.group("title")))
        if key:
            return key, plain_match.group("rest").strip()

    return None, ""


def _extract_sections(text: str, aliases: dict[str, str]) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in text.splitlines():
        key, rest = _extract_heading(raw_line, aliases)
        if key:
            current_key = key
            sections.setdefault(key, [])
            if rest:
                sections[key].append(rest)
            continue

        if current_key is not None:
            sections[current_key].append(raw_line)

    return {
        key: "\n".join(lines).strip()
        for key, lines in sections.items()
        if "\n".join(lines).strip()
    }


def _section_text(value: str) -> str:
    return " ".join(item for item in _section_items(value) if item).strip()


def _section_items(value: str) -> list[str]:
    items: list[str] = []

    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in _TABLE_SPLIT_RE.split(line.strip("|")) if cell.strip()]
            if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            if len(cells) >= 2:
                items.append(f"{cells[0]}: {'; '.join(cells[1:])}")
            else:
                items.append(cells[0])
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            items.append(_clean_item(bullet_match.group("text")))
            continue

        items.append(_clean_item(line))

    return [item for item in items if item]


def _clean_item(value: str) -> str:
    value = re.sub(r"^\s*>+\s*", "", value)
    value = value.strip().strip("-* ")
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    return value.strip()


def _aliases(mapping: dict[str, list[str]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, values in mapping.items():
        for value in values:
            aliases[_normalize_heading(value)] = key
    return aliases


_PARSER_HEADING_ALIASES = _aliases(
    {
        "topic": ["文档主题", "主题"],
        "core_content": ["文档的核心内容", "核心内容"],
        "key_concepts": ["关键概念/术语", "关键概念", "术语"],
        "mechanisms": ["核心机制、流程或规则", "核心机制", "流程或规则", "核心流程"],
        "relevant_conclusions": ["与当前学习目标最相关的解析结论", "解析结论", "相关结论"],
        "evidence": ["支撑结论的依据", "依据", "证据"],
        "gaps": ["信息不足或不确定之处", "信息不足", "不确定之处"],
        "relation_hints": ["建议 relation assistant 关注的关联点", "建议关系助手关注的关联点", "关联点"],
        "explanation_focus": ["建议 explanation assistant 重点解释的部分", "建议讲解助手重点解释的部分", "重点解释的部分"],
    }
)

_RELATION_HEADING_ALIASES = _aliases(
    {
        "target": ["目标知识点"],
        "target_features": ["目标知识点的关键特征", "关键特征"],
        "user_known_concepts": ["用户已学的相关知识点", "用户已学相关知识点", "已学相关知识点"],
        "candidates": ["候选类比知识点", "候选知识点"],
        "recommended_analogies": ["最推荐的类比对象", "推荐类比对象"],
        "similarities": ["相似点"],
        "differences": ["关键差异", "差异"],
        "boundaries": ["类比边界或容易误解的地方", "类比边界", "容易误解的地方"],
        "explanation_focus": ["建议 explanation assistant 重点讲解的部分", "建议讲解助手重点讲解的部分", "重点讲解的部分"],
        "gaps": ["信息不足或不确定之处", "信息不足", "不确定之处"],
    }
)
