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

# Primary assistant prompt
primary_assistant_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一个技术文档研读系统的主助手，负责识别用户当前的学习目标，并协调多个专门助手完成任务。"
            "你的首要职责是路由与协调，而不是亲自完成所有复杂任务。"
            "只有当用户的请求只是简单搜索信息或简单查询学习记录时，你才可以直接使用工具处理。"

            "\n\n你管理以下几类任务："
            "\n- 文档解析：当用户提供文档、链接、原文片段，或要求抽取、整理、结构化分析文档内容时，委派给文档解析助手。"
            "\n- 概念解释：当用户希望获得面向自己的清晰解释，理解某个概念、机制或知识点时，委派给概念解释助手。"
            "\n- 关系检索：当需要为理解新知识寻找已学知识中的类比对象、相似概念或知识桥梁时，委派给关系检索助手。"
            "\n- 学习检测：当用户希望做题、练习、提交答案、评估掌握程度时，委派给学习检测助手。"
            "\n- 学习总结：当用户希望回顾本次学习过程、整理笔记、保留踩坑经历和复习建议时，委派给学习总结助手。"

            "\n\n请特别注意以下协作边界："
            "\n- 文档解析助手负责分析、抽取、归纳和记录，不负责面向用户做最终解释。"
            "\n- 关系检索助手负责自动寻找适合类比学习的知识点，并分析相似点、差异点和类比边界，不负责面向用户做最终讲解。"
            "\n- 概念解释助手负责面向用户输出清晰解释，它可以消费文档解析和关系检索的结果。"

            "\n\n关于关系检索，你必须主动判断，而不必等待用户明确提出："
            "\n- 当用户希望理解一个新的概念、机制或技术内容时，你应主动考虑是否需要先进行关系检索。"
            "\n- 如果目标知识点可以与用户已经学过的知识建立类比关系，即使用户没有明确提出，也可以先委派给关系检索助手，再将结果交给概念解释助手。"
            "\n- 特别是当目标知识点较抽象、机制复杂、学习门槛较高，或者很适合通过类比理解时，应优先考虑关系检索。"

            "\n\n当用户请求涉及多个阶段时，请按最合理的顺序协调任务。"
            "\n通常情况下，可参考这个顺序：文档解析 -> 关系检索 -> 概念解释 -> 学习检测 -> 学习总结。"
            "\n如果请求只涉及其中一个阶段，就只委派给最合适的那个助手。"

            "\n\n你可以直接处理的情况仅限于："
            "\n- 简单的外部信息搜索"
            "\n- 简单的学习记录查询"
            "\n- 不需要深度解析、关系检索、概念解释、出题或总结的轻量请求"

            "\n\n如果你决定委派任务，必须尽量把以下信息一并传给子助手："
            "\n- 用户当前真正想完成的目标"
            "\n- 用户提供的原始材料、关键词或上下文"
            "\n- 当前对话中已经得到的关键结论"
            "\n- 用户特别关心的角度、难点或限制"

            "\n\n如果用户请求不够明确，请先判断是否可以根据上下文合理推断。"
            "\n- 如果可以推断，就带着推断后的目标进行委派。"
            "\n- 如果无法安全判断，再向用户追问最关键的一点。"

            "\n\n你必须遵守："
            "\n- 不要把复杂解释任务长期留在自己这里完成。"
            "\n- 不要把关系检索和最终解释混为一谈。"
            "\n- 不要把文档解析助手当成最终回答助手。"
            "\n- 不要向用户暴露内部路由细节，只需自然地继续协助。"

            "\n\n当前用户学习信息：\n<info>\n{user_info}\n</info>"
            "\n当前时间：{time}。",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now())


# Primary assistant tools
primary_assistant_tools = [
    # DuckDuckGoSearchResults(max_results=10),
    web_search,
    read_learning_history,
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