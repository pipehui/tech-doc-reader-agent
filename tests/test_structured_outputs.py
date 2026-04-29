from langchain_core.messages import AIMessage

from tech_doc_agent.app.core.structured_outputs import (
    parse_parser_result,
    parse_relation_result,
)
from tech_doc_agent.app.services.utils import create_finish_node


PARSER_TEXT = """
## 文档主题
LangGraph StateGraph

## 文档的核心内容
StateGraph 用于构建状态驱动的工作流。

## 关键概念/术语
- State
- Node
- Edge

## 核心机制、流程或规则
1. 定义 State
2. 添加节点和边

## 支撑结论的依据
| 来源 | 内容 |
|---|---|
| seed | 本地文档 |

## 信息不足或不确定之处
- 缺少复杂 checkpoint 示例

## 建议 relation assistant 关注的关联点
- reducer

## 建议 explanation assistant 重点解释的部分
- 条件边
"""


RELATION_TEXT = """
### 目标知识点
StateGraph

### 目标知识点的关键特征
- 状态驱动
- 节点执行

### 用户已学的相关知识点
- FastAPI 依赖注入

### 候选类比知识点
- Redux reducer

### 最推荐的类比对象
- Redux reducer

### 相似点
- 都围绕状态更新

### 关键差异
- StateGraph 是工作流图，Redux 是前端状态管理

### 类比边界或容易误解的地方
- 不要把两者视为完全等价

### 建议 explanation assistant 重点讲解的部分
- 节点如何读写状态

### 信息不足或不确定之处
- 缺少用户是否学过 Redux 的记录
"""


def test_parse_parser_result_from_markdown_sections():
    result = parse_parser_result(PARSER_TEXT)

    assert result.parsed is True
    assert result.topic == "LangGraph StateGraph"
    assert result.core_content == "StateGraph 用于构建状态驱动的工作流。"
    assert result.key_concepts == ["State", "Node", "Edge"]
    assert result.mechanisms == ["定义 State", "添加节点和边"]
    assert result.evidence == ["来源: 内容", "seed: 本地文档"]
    assert result.gaps == ["缺少复杂 checkpoint 示例"]
    assert result.relation_hints == ["reducer"]
    assert result.explanation_focus == ["条件边"]
    assert result.raw_text == PARSER_TEXT


def test_parse_relation_result_from_markdown_sections():
    result = parse_relation_result(RELATION_TEXT)

    assert result.parsed is True
    assert result.target == "StateGraph"
    assert result.target_features == ["状态驱动", "节点执行"]
    assert result.user_known_concepts == ["FastAPI 依赖注入"]
    assert result.candidates == ["Redux reducer"]
    assert result.recommended_analogies == ["Redux reducer"]
    assert result.similarities == ["都围绕状态更新"]
    assert result.differences == ["StateGraph 是工作流图，Redux 是前端状态管理"]
    assert result.boundaries == ["不要把两者视为完全等价"]
    assert result.explanation_focus == ["节点如何读写状态"]
    assert result.gaps == ["缺少用户是否学过 Redux 的记录"]


def test_parse_result_preserves_raw_text_when_sections_are_missing():
    result = parse_parser_result("这是一段没有标准标题的普通输出。")

    assert result.parsed is False
    assert result.raw_text == "这是一段没有标准标题的普通输出。"
    assert result.topic == ""


def test_finish_node_stores_structured_parser_result():
    finish = create_finish_node("parser_result", structured_kind="parser")
    state = {
        "messages": [AIMessage(content=PARSER_TEXT, name="parser")],
        "dialog_state": ["parser"],
        "plan_index": 1,
    }

    update = finish(state)

    assert update["dialog_state"] == "pop"
    assert update["plan_index"] == 2
    assert update["parser_result"]["topic"] == "LangGraph StateGraph"
    assert update["parser_result"]["parsed"] is True
    assert update["parser_result"]["raw_text"] == PARSER_TEXT
