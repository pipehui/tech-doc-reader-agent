from openai import OpenAI
from customer_support_chat.app.core.settings import get_settings
from typing import Union, List

settings = get_settings()
client = OpenAI(
    api_key=settings.EMBEDDING_API_KEY,
    base_url=settings.EMBEDDING_API_BASE.replace("/embeddings", ""),  # OpenAI client自己会拼 /embeddings
)

def generate_embedding(content: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
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