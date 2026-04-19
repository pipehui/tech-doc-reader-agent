from typing import Literal
from datetime import datetime
from langchain_core.prompts import ChatPromptTemplate
from customer_support_chat.app.services.tools import (
    read_learning_history,
    web_search,
    upsert_learning_history,
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
    learning_target: str = Field(description="The canonical learning target for this turn. Use one stable, concise, reusable topic name. Prefer the exact term used by the user or document. Do not add suffixes like 'core concepts', 'basics', 'summary', or 'notes'.")


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

            "\n\n关于学习记录，请明确理解："
            "\n- 学习记录只是轻量记录，用于说明用户学过什么、最近何时学过、掌握分数如何、复习过多少次。"
            "\n- 学习记录不包含某个知识点的详细技术内容、完整定义、机制说明、代码示例或系统化讲解。"
            "\n- 如果用户需要详细内容、文档依据、机制分析或面向学习的解释，不要把学习记录当作正文来源，应优先通过 parser、read_docs 或其他文档相关步骤获取。"
            "\n- 只有在简单查询用户学过什么、掌握情况如何、复习过几次时，才直接调用学习记录工具。"

            "\n\n关于“用户明确提出更新学习记录”的处理，请严格遵守："
            "\n- 如果用户明确要求“更新学习记录”“记录这次复习”“保存本次学习情况”“写入复习记录”“更新掌握分数”或表达了同等含义，这类请求属于显式的学习记录管理请求。"
            "\n- 对于显式的学习记录管理请求，如果所需信息已经足够，你可以直接调用学习记录相关工具处理，而不必为了这类简单记录管理请求额外生成复杂的 PlanWorkflow。"
            "\n- 如果当前请求的核心是“更新记录”而不是“生成总结”或“进行学习检测”，不要机械地把任务交给 summary 或 examination。"
            "\n- 只有当用户还同时需要一份完整学习总结、掌握度分析、出题评估，或者当前记录更新所需信息明显不足时，才考虑把任务交给 summary 或 examination。"
            "\n- 如果用户明确要求更新学习记录，而你当前已具备可用的学习记录更新工具，不要因为其他 assistant 在历史消息中说过“无法更新”或“没有相关工具”，就直接继承这个判断。你必须只根据你当前绑定的工具和你自己的职责来判断是否可以更新。"
            "\n- 不要把其他 assistant 关于工具能力的自然语言说明，当作你自己的能力边界。"

            "\n\n关于 primary 直接更新学习记录时的规则，请严格遵守："
            "\n- 优先使用当前已明确的 learning_target 作为 knowledge 名称；如果 learning_target 为空，再考虑使用用户明确指定的知识点名称。"
            "\n- 如果用户只是明确要求“记录本次复习/学习”，但没有要求你修改分数，且当前没有可靠依据评估新的掌握分数，那么可以只更新时间与复习次数，不要随意猜测新的 score。"
            "\n- 如果用户明确给出了新的掌握分数，或者本轮上下文中已经有充分、明确、可靠的评估依据，你才可以更新 score。"
            "\n- 如果当前知识点是全新记录，而你又没有可靠依据给出 score，不要随意创建一个带猜测分数的记录。此时应先向用户确认，或改由 summary / examination 在完成评估后再更新。"
            "\n- 在学习记录工具真正返回成功之前，不要对用户说“已更新学习记录”“复习次数已增加”“分数已写入”等已完成表述。"
            "\n- 如果工具尚未执行、正在等待审批、或执行失败，你必须如实说明状态，而不能口头假装更新成功。"

            "\n\n你可以使用的工作流步骤包括："
            "\n- parser：解析技术文档，提取结构化信息"
            "\n- relation：检索适合类比学习的相关知识点"
            "\n- explanation：面向用户解释概念和机制"
            "\n- examination：围绕知识点进行学习检测"
            "\n- summary：整理本轮学习总结"

            "\n\n关于工作流路径选择（Adaptive 策略），你必须在以下三档中选一档："
            "\n- 直接回答路径：适用于打招呼、闲聊、简单事实问题、明确的学习记录查询、明确的学习记录更新。此时不生成 PlanWorkflow，直接调用工具或回复用户。"
            "\n- 单Agent路径：用户目标明确且只需一个面向用户的助手完成。例如\"给我出一道题\"只需 [examination]，\"帮我总结刚才讨论的内容\"只需 [summary]。"
            "\n- 多Agent链路径：用户想理解一个新的技术概念或机制。标准链路是 [parser, relation, explanation]，必要时后接 [examination] 或 [summary]。"

            "\n\n硬性约束："
            "\n- plan 的最后一个步骤必须是 explanation / examination / summary 中的一个。"
            "\n- parser 和 relation 是后端助手，不面向用户产出最终回复，它们绝不能作为 plan 的最后一步。"
            "\n- 如果 plan 中包含 parser 或 relation，后面必须至少跟一个 explanation / examination / summary。"

            "\n\n制定计划时请遵守："
            "\n- 默认先考虑用户的直接目标，而不是展示完整流程。"
            "\n- 如果用户的目标是理解一个新知识点，通常优先考虑 parser、relation、explanation 这几个步骤中的必要部分。"
            "\n- 如果用户已经明确提供足够上下文，不要加入多余步骤。"
            "\n- 如果用户后续还想练习或总结，再把 examination 或 summary 放入计划。"
            "\n- 当任务需要详细解释、类比分析或机制理解时，不要先把学习记录查询当成主要内容来源。"
            "\n- 如果计划同时包含 parser、relation、explanation，这三个步骤的顺序必须是 parser -> relation -> explanation。"
            "\n- 不要把 relation 放在 explanation 之后。类比检索应当先于最终解释。"
            "\n- 当用户的目标是理解一个新的技术概念、机制、框架、协议或设计思想时，如果用户没有提供完整原文，默认先加入 parser。"
            "\n- explanation 通常应当放在 parser 或 relation 之后，而不是单独抢在前面开始。"
            "\n- 只有当任务非常简单、上下文已经足够完整，或者用户明确只要一个简短直答时，才可以跳过 parser。"


            "\n- 当你使用 PlanWorkflow 时，必须同时给出本轮学习目标的标准名称 learning_target。"
            "\n- 这个名称必须稳定、简洁、可复用。"
            "\n- 优先复用用户或文档中已经出现的原始术语，不要自行扩写、缩写，或添加“核心概念”“基础知识”“总结”“笔记”等后缀。"
            "\n- 如果本轮涉及多个概念，选择当前最核心、最值得被记录为学习对象的那个主题。"


            "\n\n关于 relation 步骤："
            "\n- 当用户希望理解一个新的概念、机制或技术内容时，你应主动考虑是否需要加入 relation 步骤。"
            "\n- 即使用户没有明确提出类比需求，只要类比有助于理解，也可以把 relation 纳入计划。"

            "\n\n当你接管异常退出的任务时，请这样处理："
            "\n- 先结合当前对话上下文判断，原任务是否还成立。"
            "\n- 如果原任务仍成立，但当前计划不完整或不合适，重新生成一个更合适的 PlanWorkflow。"
            "\n- 如果只是缺少关键信息，先向用户追问最必要的一点。"
            "\n- 如果只是简单查询，则直接调用工具，不必重新生成复杂计划。"
            "\n- 如果只是想确认学习记录中的轻量信息，可以直接查询；如果需要详细内容，则应回到文档或解释相关步骤。"

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
primary_assistant_sensitive_tools = [upsert_learning_history]
# Create the primary assistant runnable
primary_assistant_runnable = primary_assistant_prompt | llm.bind_tools(primary_assistant_tools + primary_assistant_sensitive_tools)

# Instantiate the primary assistant
primary_assistant = Assistant(primary_assistant_runnable)
