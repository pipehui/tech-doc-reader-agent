from os import environ
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY: str = environ.get("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")   # 热切换与降级容错
    DATA_PATH: str = "./customer_support_chat/data"
    LOG_LEVEL: str = environ.get("LOG_LEVEL", "DEBUG")
    SQLITE_DB_PATH: str = environ.get(
        "SQLITE_DB_PATH", "./customer_support_chat/data/travel2.sqlite"
    )
    QDRANT_URL: str = environ.get("QDRANT_URL", "http://localhost:6333")
    RECREATE_COLLECTIONS: bool = environ.get("RECREATE_COLLECTIONS", "False")
    # LIMIT_ROWS: int = environ.get("LIMIT_ROWS", "100")
    EMBEDDING_API_KEY: str = environ.get("EMBEDDING_API_KEY", "")
    EMBEDDING_API_BASE: str = environ.get("EMBEDDING_API_BASE", "")
    EMBEDDING_MODEL: str = environ.get("EMBEDDING_MODEL", "")

    TAVILY_API_KEY: str = environ.get("TAVILY_API_KEY", "")
    TAVILY_DAILY_LIMIT: int = environ.get("TAVILY_DAILY_LIMIT", "10")
    
    PROXY_URL: str = environ.get("PROXY_URL", "")

def get_settings():
    return Config()