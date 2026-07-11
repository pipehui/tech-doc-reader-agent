'''
创建 FastAPI app
在 app 生命周期里创建并保存 ChatRuntime
'''

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tech_doc_agent.app.api.frontend import install_frontend
from tech_doc_agent.app.api.routes.chat import router as chat_router
from tech_doc_agent.app.api.routes.health import router as health_router
from tech_doc_agent.app.api.routes.learning import router as learning_router
from tech_doc_agent.app.bootstrap import build_chat_runtime
from tech_doc_agent.app.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    with build_chat_runtime() as runtime:
        app.state.runtime = runtime
        yield

settings = get_settings()
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)

app.include_router(chat_router)
app.include_router(health_router)
app.include_router(learning_router)

ROOT_DIR = Path(__file__).resolve().parents[3]
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
GRAPHS_DIR = ROOT_DIR / "graphs"

install_frontend(app, dist_dir=FRONTEND_DIST_DIR, graphs_dir=GRAPHS_DIR)
