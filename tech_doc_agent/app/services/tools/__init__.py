# tech_doc_agent/app/services/tools/__init__.py
'''
parse:
  safe:   read_docs, web_search
  sensitive: save_docs

explanation:
  safe:   read_docs
  sensitive: 无

relation:
  safe:   search_related_docs, read_docs
  sensitive: 无

examination:
  safe:   read_learning_history
  sensitive: upsert_learning_history

summary:
  safe:   read_learning_history, read_user_memory
  sensitive: upsert_learning_state


read_docs            → 操作的是文档数据库
save_docs            → 操作的是文档数据库
search_related_docs  → 操作的是文档向量索引
web_search           → 操作的是外部网络
read_learning_history    → 操作的是学习记录
upsert_learning_history  → 操作的是学习记录
read_user_memory         → 操作的是长期学习轨迹记忆
upsert_learning_state    → 合并更新学习记录和长期学习轨迹记忆

根据操作对象划分工具到文件如下：
tools/
├── __init__.py
├── doc_store.py          → web_search, read_docs, save_docs, search_related_docs
└── learning_store.py     → read_learning_history, upsert_learning_history
'''
from .doc_store import web_search, read_docs, save_docs, search_related_docs
from .learning_store import (
    read_all_learning_history,
    read_learning_history,
    read_user_memory,
    upsert_learning_history,
    upsert_learning_state,
)

__all__ = [
    "web_search",
    "read_docs",
    "save_docs",
    "search_related_docs",
    "read_learning_history",
    "read_all_learning_history",
    "read_user_memory",
    "upsert_learning_history",
    "upsert_learning_state",
]
