# Tech-Doc QA · 技术文档智能问答系统

基于 **RAG（检索增强生成）** 的技术文档问答系统。上传或抓取技术文档，即可对文档内容进行自然语言提问，AI 基于文档内容回答（可溯源、可流式、带反馈）。

> 本项目基于开源项目 [GadgetGuide AI](https://github.com/zysyy/gadgetguide_ai-) 深度改造，重构为「技术文档智能问答」系统，在 RAG 核心架构上新增了流式输出、引用溯源、反馈闭环、网页抓取、安全加固等能力。

---

## ✨ 功能特性

- **RAG 智能问答**：基于知识库检索增强生成，答案可追溯
- **多格式文档**：支持 PDF / TXT / Markdown 上传
- **网页抓取**：粘贴 URL 抓取单页，或整站爬取（同目录文档）
- **SSE 流式输出**：AI 回答逐字渲染，体验接近 ChatGPT
- **回答引用溯源**：答案下方展示检索到的来源片段
- **反馈闭环**：点赞 / 点踩 / 重试，记录回答质量
- **多轮对话**：会话管理，支持历史上下文
- **管理员后台**：知识库管理、批量删除、热词统计
- **安全机制**：JWT 鉴权、bcrypt 密码加密、SSRF 防护、防 XSS

---

## 🛠 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue3 + Element Plus + Pinia + Vite |
| 后端 | FastAPI + SQLAlchemy |
| RAG | LangChain + FAISS + bge-m3 |
| 向量化 | Ollama（本地 bge-m3 嵌入模型） |
| 生成 | DeepSeek API |
| 数据库 | SQLite |

---

## 🏗 架构

```
前端 Vue3 SPA
   │  HTTP + JSON (axios/fetch)
   ▼
后端 FastAPI
   ├── auth/    注册登录（JWT + bcrypt）
   ├── chat/    会话消息（多轮对话 + 流式）
   ├── admin/   知识库管理 + 网页抓取 + 热词
   ├── qa_handler.py          RAG 检索 + 生成
   └── knowledge_base_processor.py  文档切分 + 向量化 + FAISS
   │
   ├── Ollama (bge-m3)   ← 本地向量化
   ├── FAISS             ← 向量索引
   ├── DeepSeek API      ← 答案生成
   └── SQLite (users.db) ← 用户/会话/消息
```

---

## 🚀 快速开始

### 前置要求

- Python ≥ 3.9
- Node.js ≥ 18
- Ollama（本地，已拉取 `bge-m3`）
- DeepSeek API Key

### 1. 安装依赖

```bash
# 后端
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# 前端
cd ../frontend
npm install
```

### 2. 配置

在 `backend/.env` 中配置：

```env
DEEPSEEK_API_KEY=sk-你的key
NO_PROXY=localhost,127.0.0.1
```

### 3. 启动 Ollama 并拉取嵌入模型

```bash
ollama pull bge-m3
```

### 4. 启动服务

```bash
# 后端（项目根目录）
backend\.venv\Scripts\python -m uvicorn backend.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm run dev
```

访问 http://localhost:5173

### 5. 初始化管理员

```bash
backend\.venv\Scripts\python -m backend.init_admin
# 默认账号：T0 / 123456
```

---

## 📚 使用

1. 用管理员账号 `T0 / 123456` 登录
2. 进入「知识库管理」：
   - **上传文件**：PDF / TXT / Markdown
   - **抓取单页**：粘贴单个文档网址
   - **抓取整站**：粘贴文档站入口（自动爬取同目录所有页面）
3. 回到聊天页提问，AI 基于文档回答（流式 + 溯源 + 反馈）

---

## 📖 核心 API

| 接口 | 说明 |
|---|---|
| `POST /auth/register` / `login` | 注册 / 登录 |
| `POST /chat/conversations/` | 创建会话 |
| `POST /chat/conversations/{id}/messages/` | 发送消息（非流式） |
| `POST /chat/conversations/{id}/messages/stream` | 发送消息（SSE 流式） |
| `POST /chat/messages/{id}/feedback` | 点赞 / 点踩 |
| `POST /admin/upload-documents/` | 上传文档 |
| `POST /admin/fetch-url` | 抓取单页 URL |
| `POST /admin/crawl-site` | 整站爬取 |
| `POST /admin/uploaded-files/batch-delete` | 批量删除 |
| `GET /admin/hot-words` | 热词统计 |

---

## 📁 项目结构

```
backend/
├── main.py                     # 入口
├── config.py                   # 配置
├── qa_handler.py               # RAG 检索 + 生成
├── knowledge_base_processor.py # 文档切分 + 向量化
├── auth/                       # 用户认证
├── chat/                       # 会话消息
├── admin/                      # 知识库管理 + 网页抓取
frontend/
└── src/
    ├── views/                  # 页面（聊天、登录、管理）
    ├── components/             # 组件
    └── config.ts               # 统一配置
```

---

## 🙏 致谢

- 原项目：[GadgetGuide AI](https://github.com/zysyy/gadgetguide_ai-)（MIT License）
- 嵌入模型：[bge-m3](https://huggingface.co/BAAI/bge-m3)
- 大模型：[DeepSeek](https://www.deepseek.com/)

---

## ⚠️ 说明

本项目为个人学习与求职项目，部分功能（反馈闭环、爬虫）为雏形而非生产级，仅供学习交流。
