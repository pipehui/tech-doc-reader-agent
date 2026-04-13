from typing import Literal
from datetime import datetime
from langchain_core.prompts import ChatPromptTemplate
from customer_support_chat.app.services.tools import (
    read_learning_history,
    web_search,
)
# from langchain_community.tools.ddg_search.tool import DuckDuckGoSearchResults
from customer_support_chat.app.services.assistants.assistant_base import Assistant, llm
from customer_support_chat.app.core.state import State
from pydantic import BaseModel, Field

class ToDocParserAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle document parsing."""
    content: str = Field(description="The content of the document that needs to be parsed, or a URL pointing to the document, or any other relevant information that can help the document parsing assistant understand what needs to be parsed.")
    request: str = Field(description="Any specific information the user wants to extract from the document or any particular questions they have about the document.")

class ToExplanationAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle concept explanation."""
    concept: str = Field(description="The specific concept or topic that the user wants to understand better.")
    request: str = Field(description="Any specific questions the user has about the concept or any particular aspects they want the explanation to focus on.")

class ToRelationAssistant(BaseModel):
    """Transfers work to a specialized assistant to retrieve analogous or related knowledge that may help the user understand the target concept, even when the user did not explicitly ask for analogy."""
    entity: str = Field(description="The target concept, mechanism, or topic that needs analogical or relational retrieval.")
    request: str = Field(description="Why this relation retrieval is useful for the current learning goal, including any user question, confusion point, or context that should guide the retrieval.")

class ToExaminationAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle examination and quiz generation."""
    topic: str = Field(description="The specific topic or subject that the user wants to be tested on.")
    request: str = Field(description="Any specific questions the user has about the topic or any particular types of questions they want the examination assistant to generate.")

class ToSummaryAssistant(BaseModel):
    """Transfers work to a specialized assistant to summarize the user's learning process."""
    request: str = Field(description="What kind of learning summary the user needs, including whether the focus should be on key takeaways, mistakes, corrections, or review suggestions.")

class PlanWorkflow(BaseModel):
    steps: list[Literal["parser", "relation", "explanation", "examination", "summary"]]
    goal: str = Field(description="The user's learning goal in this turn.")

# Primary assistant prompt
primary_assistant_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一个技术文档研读系统的主助手，负责理解用户当前的学习目标，并为本轮任务制定合适的工作流计划。"
            "你的首要职责是规划与协调，而不是亲自完成所有复杂任务。"

            "\n\n你的工作原则如下："
            "\n1. 对于涉及文档解析、关系检索、概念解释、学习检测、学习总结中的复杂请求，优先使用 PlanWorkflow 制定一个最小必要的执行计划。"
            "\n2. 计划应尽量简洁，只包含完成当前用户目标所必需的步骤，不要机械地把所有助手都加入计划。"
            "\n3. 对于简单的外部信息搜索或简单的学习记录查询，你可以直接调用工具处理，而不必生成计划。"
            "\n4. 如果某个子助手因为任务变化、信息不足或不适合继续而退出，你需要重新接管，并判断是重新规划、改写计划，还是直接向用户追问关键缺失信息。"

            "\n\n你可以使用的工作流步骤包括："
            "\n- parser：解析技术文档，提取结构化信息"
            "\n- relation：检索适合类比学习的相关知识点"
            "\n- explanation：面向用户解释概念和机制"
            "\n- examination：围绕知识点进行学习检测"
            "\n- summary：整理本轮学习总结"

            "\n\n制定计划时请遵守："
            "\n- 默认先考虑用户的直接目标，而不是展示完整流程。"
            "\n- 如果用户的目标是理解一个新知识点，通常优先考虑 parser、relation、explanation 这几个步骤中的必要部分。"
            "\n- 如果用户已经明确提供足够上下文，不要加入多余步骤。"
            "\n- 如果用户后续还想练习或总结，再把 examination 或 summary 放入计划。"

            "\n\n关于 relation 步骤："
            "\n- 当用户希望理解一个新的概念、机制或技术内容时，你应主动考虑是否需要加入 relation 步骤。"
            "\n- 即使用户没有明确提出类比需求，只要类比有助于理解，也可以把 relation 纳入计划。"

            "\n\n当你接管异常退出的任务时，请这样处理："
            "\n- 先结合当前对话上下文判断，原任务是否还成立。"
            "\n- 如果原任务仍成立，但当前计划不完整或不合适，重新生成一个更合适的 PlanWorkflow。"
            "\n- 如果只是缺少关键信息，先向用户追问最必要的一点。"
            "\n- 如果只是简单查询，则直接调用工具，不必重新生成复杂计划。"

            "\n\n你必须遵守："
            "\n- 不要在复杂任务上一开始就直接调用某个子助手，而跳过 PlanWorkflow。"
            "\n- 不要把所有请求都规划成完整长链路。"
            "\n- 不要向用户暴露内部工作流、路由或状态栈。"
            "\n- 你的计划应服务于用户目标，而不是服务于系统结构本身。"

            "\n\n当前用户学习信息：\n<info>\n{user_info}\n</info>"
            "\n当前时间：{time}。"
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now())


# Primary assistant tools
primary_assistant_tools = [
    # DuckDuckGoSearchResults(max_results=10),
    web_search,
    read_learning_history,
    PlanWorkflow,
    ToDocParserAssistant,
    ToExplanationAssistant,
    ToRelationAssistant,
    ToExaminationAssistant,
    ToSummaryAssistant,
]

# Create the primary assistant runnable
primary_assistant_runnable = primary_assistant_prompt | llm.bind_tools(primary_assistant_tools)

# Instantiate the primary assistant
primary_assistant = Assistant(primary_assistant_runnable)