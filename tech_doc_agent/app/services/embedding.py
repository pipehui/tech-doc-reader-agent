from openai import OpenAI

from tech_doc_agent.app.core.errors import ApplicationError, ValidationError, classify_error
from tech_doc_agent.app.core.retry import RetryExecutor, build_retry_executor
from tech_doc_agent.app.core.settings import Settings, get_settings


def _embedding_api_base_or_none(value: str) -> str | None:
    if not value:
        return None
    return value.replace("/embeddings", "")  # OpenAI client appends /embeddings itself.


def _build_embedding_client(settings: Settings | None = None) -> OpenAI:
    settings = settings or get_settings()
    missing = []
    if not settings.EMBEDDING_API_KEY:
        missing.append("EMBEDDING_API_KEY")
    if not settings.EMBEDDING_MODEL:
        missing.append("EMBEDDING_MODEL")
    if missing:
        raise ValidationError(
            "The embedding service is not configured.",
            code="embedding_not_configured",
            dependency="embedding",
            cause_type="MissingConfiguration",
        )

    return OpenAI(
        api_key=settings.EMBEDDING_API_KEY,
        base_url=_embedding_api_base_or_none(settings.EMBEDDING_API_BASE),
        max_retries=0,
    )


def generate_embedding(
    content: str | list[str],
    *,
    settings: Settings | None = None,
    retry_executor: RetryExecutor | None = None,
) -> list[float] | list[list[float]]:
    settings = settings or get_settings()

    if not isinstance(content, (str, list)) or (
        isinstance(content, list) and not all(isinstance(item, str) for item in content)
    ):
        raise ValidationError(
            "Embedding input must be text or a list of text values.",
            dependency="embedding",
            cause_type=type(content).__name__,
        )

    try:
        client = _build_embedding_client(settings)
    except ApplicationError:
        raise
    except Exception as exc:
        raise classify_error(exc, dependency="embedding") from exc

    executor = retry_executor or build_retry_executor(settings)
    response = executor.run(
        lambda: client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=[content] if isinstance(content, str) else content,
        ),
        operation_name="embedding.create",
        dependency="embedding",
        idempotent=True,
    )

    try:
        if isinstance(content, str):
            return response.data[0].embedding
        return [item.embedding for item in response.data]
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValidationError(
            "The embedding dependency returned an invalid response.",
            code="embedding_response_invalid",
            dependency="embedding",
            cause=exc,
        ) from exc
