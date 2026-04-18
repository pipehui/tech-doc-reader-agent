'''
创建 FastAPI app
在 app 生命周期里创建并保存 ChatRuntime
'''

from contextlib import asynccontextmanager
from fastapi import FastAPI

from customer_support_chat.app.services.chat_runtime import ChatRuntime
from customer_support_chat.app.api.routes.chat import router as chat_router
from fastapi.middleware.cors import CORSMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    with ChatRuntime() as runtime:
        app.state.runtime = runtime
        yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)