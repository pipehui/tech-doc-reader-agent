'''
primary      — 路由
parse        — 文档解析
explanation  — 概念解释
relation     — 关联检索
examination  — 考验出题
summary      — 摘要生成
'''
from .assistant_base import Assistant, CompleteOrEscalate, llm
from .primary_assistant import (
    primary_assistant,
    primary_assistant_tools,
    ToDocParserAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToExaminationAssistant,
    ToSummaryAssistant,
)
from .parser_assistant import doc_parser_assistant
from .explanation_assistant import explanation_assistant
from .relation_assistant import relation_assistant
from .examination_assistant import examination_assistant
from .summary_assistant import summary_assistant
