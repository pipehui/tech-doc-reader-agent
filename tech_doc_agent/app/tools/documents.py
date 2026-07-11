import json
from dataclasses import dataclass

from langchain_core.tools import BaseTool, tool

from tech_doc_agent.app.services.retrieval.filters import normalize_filter
from tech_doc_agent.app.tools.dependencies import ToolDependencies


@dataclass(frozen=True, slots=True)
class DocumentTools:
    web_search: BaseTool
    read_docs: BaseTool
    save_docs: BaseTool
    search_related_docs: BaseTool


def _build_filters(
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> dict:
    return normalize_filter(
        {
            "category": category,
            "tags": tags,
            "source": source,
        }
    )


def build_document_tools(dependencies: ToolDependencies) -> DocumentTools:
    @tool
    def web_search(query: str) -> str:
        """
        在外部网络上搜索与查询相关的信息，并返回搜索结果列表。
        例如用户问'最新的AI技术有哪些'时，就用这个查询在网络上搜索相关信息，并返回搜索结果。
        """

        results = dependencies.web_search.search(query)
        return json.dumps(results, ensure_ascii=False)

    @tool
    def read_docs(
        query: str,
        category: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> str:
        """
        当需要查找已存储的技术文档内容时，根据关键词从知识库中检索匹配的文档。
        文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 或 source 来限制检索范围。
        category 只能使用内部标准分类；RAG、LangGraph 这类宽泛主题应使用 tags=["rag"] 或 tags=["langgraph"]，不要作为 category 传入。
        """

        filters = _build_filters(category=category, tags=tags, source=source)
        documents = dependencies.document_retriever.search(query, filters=filters)
        return json.dumps(documents, ensure_ascii=False)

    @tool
    def save_docs(
        title: str,
        content: str,
        source: str = "",
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        当需要将新的技术文档内容保存到知识库时，使用该工具将文档标题和内容存储起来。
        例如用户提供了一个新的文档标题和内容时，就调用这个工具进行保存。
        文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 作为文档 metadata。
        """

        result = dependencies.document_store.add_documents(
            [
                {
                    "title": title,
                    "content": content,
                    "source": source,
                    "category": category,
                    "tags": tags,
                }
            ]
        )
        dependencies.document_store.save()
        dependencies.document_retriever.refresh()
        return f"Document '{title}' has been saved successfully. Added {result['added_chunks']} chunks."

    @tool
    def search_related_docs(
        query: str,
        k: int,
        category: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> str:
        """
        使用向量索引搜索与查询语义相关的文档。
        例如用户问'LangGraph是什么'时，用'LangGraph'作为query进行相似度计算，找出最多k个相关文档。
        文档库是共享知识库，不按当前用户隔离；可选传入 category、tags 或 source 来过滤结果。
        """

        try:
            filters = _build_filters(category=category, tags=tags, source=source)
            results = dependencies.document_retriever.search(
                query,
                top_k=k,
                mode="vector",
                filters=filters,
            )
        except Exception:
            results = []
        return json.dumps(results, ensure_ascii=False)

    return DocumentTools(
        web_search=web_search,
        read_docs=read_docs,
        save_docs=save_docs,
        search_related_docs=search_related_docs,
    )
