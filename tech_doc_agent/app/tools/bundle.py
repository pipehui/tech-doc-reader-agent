from dataclasses import dataclass

from langchain_core.tools import BaseTool

from tech_doc_agent.app.tools.dependencies import ToolDependencies
from tech_doc_agent.app.tools.documents import build_document_tools
from tech_doc_agent.app.tools.learning import build_learning_tools
from tech_doc_agent.app.tools.profiles import build_profile_tools


@dataclass(frozen=True, slots=True)
class ToolBundle:
    web_search: BaseTool
    read_docs: BaseTool
    save_docs: BaseTool
    search_related_docs: BaseTool
    read_learning_history: BaseTool
    read_all_learning_history: BaseTool
    read_user_memory: BaseTool
    upsert_learning_history: BaseTool
    upsert_learning_state: BaseTool
    read_user_profile: BaseTool
    update_user_profile: BaseTool

    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.all())

    def all(self) -> tuple[BaseTool, ...]:
        return (
            self.web_search,
            self.read_docs,
            self.save_docs,
            self.search_related_docs,
            self.read_learning_history,
            self.read_all_learning_history,
            self.read_user_memory,
            self.upsert_learning_history,
            self.upsert_learning_state,
            self.read_user_profile,
            self.update_user_profile,
        )


def build_tool_bundle(dependencies: ToolDependencies) -> ToolBundle:
    documents = build_document_tools(dependencies)
    learning = build_learning_tools(dependencies)
    profiles = build_profile_tools(dependencies)
    return ToolBundle(
        web_search=documents.web_search,
        read_docs=documents.read_docs,
        save_docs=documents.save_docs,
        search_related_docs=documents.search_related_docs,
        read_learning_history=learning.read_learning_history,
        read_all_learning_history=learning.read_all_learning_history,
        read_user_memory=learning.read_user_memory,
        upsert_learning_history=learning.upsert_learning_history,
        upsert_learning_state=learning.upsert_learning_state,
        read_user_profile=profiles.read_user_profile,
        update_user_profile=profiles.update_user_profile,
    )
