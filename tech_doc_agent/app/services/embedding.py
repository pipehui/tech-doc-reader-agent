from typing import Union, List

from openai import OpenAI

from tech_doc_agent.app.core.settings import get_settings


def _embedding_api_base_or_none(value: str) -> str | None:
    if not value:
        return None
    return value.replace("/embeddings", "")  # OpenAI client appends /embeddings itself.


def _build_embedding_client() -> OpenAI:
    settings = get_settings()
    missing = []
    if not settings.EMBEDDING_API_KEY:
        missing.append("EMBEDDING_API_KEY")
    if not settings.EMBEDDING_MODEL:
        missing.append("EMBEDDING_MODEL")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"{joined} must be configured before generating embeddings.")

    return OpenAI(
        api_key=settings.EMBEDDING_API_KEY,
        base_url=_embedding_api_base_or_none(settings.EMBEDDING_API_BASE),
    )


def generate_embedding(content: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
    settings = get_settings()
    client = _build_embedding_client()

    if isinstance(content, str):
        response = client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=[content]
        )
        return response.data[0].embedding
    elif isinstance(content, list):
        response = client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=content
        )
        return [item.embedding for item in response.data]
    else:
        raise ValueError("Content must be either a string or a list of strings")
