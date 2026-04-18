'''
设置数据进出格式
'''
from pydantic import BaseModel

class ChatRequest(BaseModel):
    session_id: str
    message: str


class ApproveRequest(BaseModel):
    session_id: str
    approved: bool
    feedback: str = ""