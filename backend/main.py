import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# --- 日志配置 ---
logger = logging.getLogger("tech_doc_qa")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s')
ch.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(ch)

# --- .env 加载 ---
BACKEND_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BACKEND_DIR / ".env"
logger.debug(f"Attempting to load .env from: {DOTENV_PATH}")
if DOTENV_PATH.is_file():
    logger.debug(f".env file found at {DOTENV_PATH}. Loading...")
    load_dotenv(dotenv_path=DOTENV_PATH, verbose=True)
else:
    logger.warning(f".env file NOT found at {DOTENV_PATH}. Attempting default load_dotenv().")
    load_dotenv(verbose=True)

from fastapi import FastAPI, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any

# --- 模块导入 ---
from backend.qa_handler import get_final_answer
from backend.auth.routes import router as auth_router
from backend.chat.routes import router as chat_router
from backend.admin.routes import router as admin_router        # <--- 新增
from backend.auth import models
from backend.chat import models as chat_models
from backend.database import Base, engine

# --- 创建 FastAPI 实例 ---
app = FastAPI(title="Tech-Doc QA API")

# --- 创建所有数据表（用户表、会话表、消息表等） ---
Base.metadata.create_all(bind=engine)

# --- CORS 配置 ---
# 允许所有 localhost/127.0.0.1 任意端口（Vite 端口可能变化，避免端口一变就跨域失败）
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 路由挂载 ---
logger.debug("Mounting /auth, /chat and /admin routes...")
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(admin_router)     # <--- 新增
logger.info("Routes mounted successfully.")

@app.on_event("startup")
async def startup_event():
    logger.info("应用程序启动，qa_handler 将尝试加载现有索引...")

@app.get("/")
async def read_root():
    logger.info("Root endpoint / was called")
    return {"message": "Welcome to Tech-Doc QA API!"}

@app.post("/ask", response_model=Dict[str, Any])
def ask_question_endpoint(query: str = Form(...)):
    logger.info(f"Received query for /ask endpoint: '{query}'")
    if not query.strip():
        logger.warning("Empty query received for /ask endpoint.")
        raise HTTPException(status_code=400, detail="查询不能为空。")
    result = get_final_answer(query)
    if result.get("error"):
        logger.error(f"Error in /ask endpoint for query '{query}': {result.get('error')}")
        raise HTTPException(status_code=500, detail=result.get("error", "处理请求时发生未知错误。"))
    logger.info(f"Successfully answered query for /ask endpoint: '{query}'")
    return {"question": query, "answer": result.get("answer", "未能获取到明确的回答。")}

# 说明：旧的 /upload-documents/ 与 /build_index_from_sample 端点已移除。
# 上传统一走 /admin/upload-documents/（需管理员鉴权）；原硬编码 iPhone 样例的建索引用例已删除。
