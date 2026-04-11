'''
read_learning_history -> 从学习记录中读取与查询相关的历史学习内容
upsert_learning_history -> 将新的学习内容保存到学习记录中，或者更新已有的学习内容
'''

from customer_support_chat.app.core.settings import get_settings
from langchain_core.tools import tool
from typing import List, Dict, Optional, Union

settings = get_settings()
class LearningState:
    '''
    这是一个学习记录的类，用于存储学习内容、时间戳、学习评分和复习次数等信息。
    '''
    def __init__(self, knowledge: str, timestamp: str, score: float = 0.0, reviewtimes: int = 0):
        self.knowledge = knowledge
        self.timestamp = timestamp
        self.score = score
        self.reviewtimes = reviewtimes
    
# 模拟一个学习记录数据库
_learning_history_db: List[LearningState] = [
    LearningState(knowledge="LangGraph StateGraph", timestamp="2024-01-01T10:00:00Z", score=0.8, reviewtimes=1),
    LearningState(knowledge="FastAPI 依赖注入", timestamp="2024-01-02T11:00:00Z", score=0.9, reviewtimes=2),
]

@tool
def read_learning_history(query: str) -> List[Dict]:
    """
    读取学习记录中与查询相关的历史学习内容。
    例如用户提问'我之前学过哪些关于LangGraph的内容'时，用'LangGraph'作为query进行检索，返回相关的学习记录。
    或者复习时需要检索之前学习过的内容时，也可以用这个工具进行查询。
    """
    # Implement your learning history retrieval logic here
    history = []
    for record in _learning_history_db:
        if query.lower() in record.knowledge.lower():
            history.append({
                "knowledge": record.knowledge,
                "timestamp": record.timestamp,
                "score": record.score,
                "reviewtimes": record.reviewtimes,
            })
    return history

@tool
def upsert_learning_history(knowledge: str, timestamp: str, score: Optional[float] = None) -> str:
    """
    将学习的内容写入学习记录中，如果该内容已经存在则更新其时间戳和评分，并将复习次数加一。
    例如用户学习了一个新的知识点时，就调用这个工具将其保存到学习记录中。
    或者用户复习了一个知识点时，也可以调用这个工具更新其学习记录。
    """
    # Implement your learning history upsert logic here
    for record in _learning_history_db:
        if record.knowledge == knowledge:
            record.timestamp = timestamp
            record.score = score if score is not None else record.score
            record.reviewtimes += 1
            return f"Learning record for '{knowledge}' has been updated successfully."
    
    new_record = LearningState(knowledge=knowledge, timestamp=timestamp, score=score if score is not None else 0.0, reviewtimes=1)
    _learning_history_db.append(new_record)
    return f"Learning record for '{knowledge}' has been added successfully."