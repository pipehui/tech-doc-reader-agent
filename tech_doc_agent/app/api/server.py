'''
创建 FastAPI app
在 app 生命周期里创建并保存 ChatRuntime
'''

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tech_doc_agent.app.services.chat_runtime import ChatRuntime
from tech_doc_agent.app.api.routes.chat import router as chat_router
from tech_doc_agent.app.api.routes.learning import router as learning_router
from fastapi.middleware.cors import CORSMiddleware

from tech_doc_agent.app.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    with ChatRuntime() as runtime:
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
app.include_router(learning_router)

ROOT_DIR = Path(__file__).resolve().parents[3]
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
GRAPHS_DIR = ROOT_DIR / "graphs"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if (FRONTEND_DIST_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST_DIR / "assets"), name="frontend-assets")

if GRAPHS_DIR.exists():
    app.mount("/graphs", StaticFiles(directory=GRAPHS_DIR), name="graphs")

def frontend_index_file() -> Path:
    dist_index = FRONTEND_DIST_DIR / "index.html"
    if dist_index.exists():
        return dist_index
    return FRONTEND_DIR / "index.html"


@app.get("/", include_in_schema=False)
def frontend_index():
    return FileResponse(frontend_index_file())

@app.get("/studio", include_in_schema=False)
@app.get("/inspector", include_in_schema=False)
@app.get("/learner", include_in_schema=False)
def frontend_app_view():
    return FileResponse(frontend_index_file())
