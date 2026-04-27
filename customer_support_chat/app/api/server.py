'''
创建 FastAPI app
在 app 生命周期里创建并保存 ChatRuntime
'''

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from customer_support_chat.app.services.chat_runtime import ChatRuntime
from customer_support_chat.app.api.routes.chat import router as chat_router
from customer_support_chat.app.api.routes.learning import router as learning_router
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
app.include_router(learning_router)

ROOT_DIR = Path(__file__).resolve().parents[3]
FRONTEND_DIR = ROOT_DIR / "frontend"
GRAPHS_DIR = ROOT_DIR / "graphs"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if GRAPHS_DIR.exists():
    app.mount("/graphs", StaticFiles(directory=GRAPHS_DIR), name="graphs")


@app.get("/", include_in_schema=False)
def frontend_index():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/studio", include_in_schema=False)
@app.get("/inspector", include_in_schema=False)
@app.get("/learner", include_in_schema=False)
def frontend_app_view():
    return FileResponse(FRONTEND_DIR / "index.html")
