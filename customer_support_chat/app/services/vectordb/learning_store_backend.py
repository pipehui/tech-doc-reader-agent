"""
LearningStore backend:
- 负责学习记录的本地持久化
- 不暴露 @tool
- tool 层在 learning_store.py
"""
import json
from pathlib import Path
from customer_support_chat.app.core.settings import get_settings

class LearningStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.store_dir = Path(settings.DATA_PATH) / "learning_store"
        self.records_path = self.store_dir / "records.json"
        self.records = []

    def load(self) -> bool:
        if not self.records_path.exists():
            return False
        with open(self.records_path, "r", encoding="utf-8") as f:
            self.records = json.load(f)
        return True
    
    def save(self) -> bool:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        with open(self.records_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)
        return True
    
    def _make_record(self, knowledge: str, timestamp: str, score: float | None, reviewtimes: int = 1) -> dict:
        return {
            "knowledge": knowledge,
            "timestamp": timestamp,
            "score": score if score is not None else 0.0,
            "reviewtimes": reviewtimes,
        }
    
    def read_by_query(self, query: str) -> list[dict]:
        res = []
        query_lower = query.lower()
        for record in self.records:
            if query_lower in record["knowledge"].lower():
                res.append(record)

        return res

    def read_overview(self) -> list[dict]:
        return [dict(record) for record in self.records]
    
    def upsert_record(self, knowledge: str, timestamp: str, score: float | None = None) -> str:
        idx = -1
        for i, record in enumerate(self.records):
            if knowledge == record["knowledge"]:
                idx = i
                break
        if idx == -1:
            self.records.append(self._make_record(knowledge, timestamp, score))
            return f"Learning record for '{knowledge}' has been added successfully."
        
        self.records[idx]["timestamp"] = timestamp
        if score is not None:
            self.records[idx]["score"] = score
        self.records[idx]["reviewtimes"] += 1
        return f"Learning record for '{knowledge}' has been updated successfully."
