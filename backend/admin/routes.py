# backend/admin/routes.py

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.auth.models import User
from backend.chat.models import Conversation, Message

from backend.auth.routes import get_current_user
from backend.knowledge_base_processor import create_index_from_files
from backend.qa_handler import reload_vector_db
from backend.config import UPLOAD_FOLDER

from typing import List
import shutil
from pathlib import Path
import os
import hashlib
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
from urllib.parse import urljoin, urlparse
import ipaddress
import socket
import logging

logger = logging.getLogger("gadgetguide_ai.admin")

router = APIRouter(
    prefix="/admin",
    tags=["admin"]
)

# ==== 数据库会话依赖 ====
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==== 管理员权限依赖 ====
def admin_required(
    current_user: User = Depends(get_current_user)
) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="无管理员权限")
    return current_user

# ==== 1. 获取所有用户列表 ====
@router.get("/users", response_model=List[dict])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required)
):
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "created_at": u.created_at
        } for u in users
    ]

# ==== 2. 获取指定用户的所有会话 ====
@router.get("/users/{user_id}/conversations", response_model=List[dict])
def user_conversations(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required)
):
    conversations = db.query(Conversation).filter(Conversation.user_id == user_id).all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at
        } for c in conversations
    ]

# ==== 3. 获取某个会话的所有消息 ====
@router.get("/conversations/{conversation_id}/messages", response_model=List[dict])
def conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required)
):
    messages = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at
        } for m in messages
    ]

import re  # 新增导入

def normalize_filename(filename: str) -> str:
    """
    规范化文件名：
    - 保留中文、英文、数字、下划线、连字符、括号
    - 替换中文括号为英文括号
    - 将空格和非法字符替换为下划线
    """
    name, ext = os.path.splitext(filename)
    name = name.replace("（", "(").replace("）", ")")
    name = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9_\-()]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return f"{name}{ext.lower()}"  # 统一扩展名小写

def resolve_filename_conflict(base_dir: Path, filename: str) -> str:
    """
    如果文件已存在，自动添加数字后缀避免冲突。
    """
    counter = 1
    target = base_dir / filename
    name, ext = os.path.splitext(filename)
    while target.exists():
        target = base_dir / f"{name}_{counter}{ext}"
        counter += 1
    return target.name

# ==== 4. 管理员上传文件并更新知识库索引 ====
@router.post("/upload-documents/", summary="上传文件并更新知识库索引（仅管理员）")
def admin_upload_documents(
    files: List[UploadFile] = File(...),
    admin: User = Depends(admin_required)
):
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    
    processed_files_info = []
    files_to_index = []

    for file in files:
        cleaned = normalize_filename(file.filename)
        final_name = resolve_filename_conflict(Path(UPLOAD_FOLDER), cleaned)
        file_path = Path(UPLOAD_FOLDER) / final_name
        try:
            with open(file_path, "wb+") as buffer:
                shutil.copyfileobj(file.file, buffer)
            files_to_index.append(final_name)
            processed_files_info.append({"filename": final_name, "status": "上传成功"})
        except Exception as e:
            processed_files_info.append({"filename": final_name, "status": "上传失败", "error": str(e)})
        finally:
            file.file.close()
    
    if not files_to_index:
        raise HTTPException(status_code=400, detail="文件保存失败，无法建立索引。")

    # 上传后重新构建所有文件的索引（不是只针对新上传的文件，而是全部）
    all_files = [
        f for f in os.listdir(UPLOAD_FOLDER)
        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f.lower().endswith((".pdf", ".txt", ".md", ".markdown"))
    ]
    if create_index_from_files(all_files):
        reload_vector_db()
        return {
            "message": f"{len(files_to_index)} 个文件已成功上传，索引已基于所有上传文件刷新。",
            "processed_files_details": processed_files_info,
            "indexed_files": all_files
        }
    else:
        raise HTTPException(status_code=500, detail="文件上传，但知识库索引构建失败，请检查后端日志。")

# ==== 5. 获取所有已上传文件列表 ====
@router.get("/uploaded-files", summary="列出所有已上传的知识库文件", response_model=List[dict])
def list_uploaded_files(admin: User = Depends(admin_required)):
    file_list = []
    for file in Path(UPLOAD_FOLDER).iterdir():
        if file.is_file():
            stat = file.stat()
            file_list.append({
                "filename": file.name,
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime)
            })
    file_list.sort(key=lambda x: x["modified_at"], reverse=True)
    return file_list

# ==== 6. 删除指定文件并刷新索引 ====
@router.delete("/uploaded-files/{filename}", summary="删除已上传的知识库文件（仅管理员）")
def delete_uploaded_file(
    filename: str,
    admin: User = Depends(admin_required)
):
    file_path = Path(UPLOAD_FOLDER) / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        file_path.unlink()
        # 删除后，全量重建索引（force_rebuild=True 会清空旧索引，避免已删文件的向量残留）
        all_files = [
            f for f in os.listdir(UPLOAD_FOLDER)
            if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f.lower().endswith((".pdf", ".txt", ".md", ".markdown"))
        ]
        if not create_index_from_files(all_files, force_rebuild=True):
            raise HTTPException(status_code=500, detail="文件已删除，但索引重建失败")
        reload_vector_db()
        return {"message": f"文件 {filename} 已删除，索引已刷新。"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除文件失败: {e}")

class BatchDeleteRequest(BaseModel):
    filenames: List[str]

# ==== 6.5 批量删除文件并刷新索引 ====
@router.post("/uploaded-files/batch-delete", summary="批量删除已上传文件（仅管理员）")
def batch_delete_files(
    payload: BatchDeleteRequest,
    admin: User = Depends(admin_required)
):
    if not payload.filenames:
        raise HTTPException(status_code=400, detail="请选择要删除的文件")

    deleted = []
    for filename in payload.filenames:
        file_path = Path(UPLOAD_FOLDER) / filename
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            deleted.append(filename)

    if not deleted:
        raise HTTPException(status_code=404, detail="没有可删除的文件")

    # 删除后全量重建索引
    all_files = [
        f for f in os.listdir(UPLOAD_FOLDER)
        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f.lower().endswith((".pdf", ".txt", ".md", ".markdown"))
    ]
    if create_index_from_files(all_files, force_rebuild=True):
        reload_vector_db()
        return {"message": f"已删除 {len(deleted)} 个文件，索引已刷新", "deleted": deleted}
    else:
        raise HTTPException(status_code=500, detail="文件已删除，但索引重建失败")

# ==== 7. 热词统计（返回所有消息中的高频词） ====
from collections import Counter
import jieba  # 中文分词，若只考虑英文可直接 split

@router.get("/hot-words", summary="聊天内容热词统计（高频词）", tags=["admin"])
def get_hot_words(
    top_n: int = 30,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required)
):
    # 拉取所有聊天消息
    messages = db.query(Message.content).all()
    all_text = " ".join([m[0] for m in messages if m and m[0]])
    # 分词
    words = list(jieba.cut(all_text))
    # 停用词表
    stop_words = set(["的", "了", "是", "我", "你", "吗", "和", "有", "在", "我们", "他们", "它", "这", "那", "会", "吧", "请", "能", "为", "就", "不", "也", "但", "要", "与", "对", "到", "其", "等", "与", "及", "或", "一个", "如何", "是什么", "可以", "请问"])
    # 过滤
    filtered = [w for w in words if w.strip() and w not in stop_words and len(w) > 1]
    # 高频词统计
    count = Counter(filtered)
    return [{"word": w, "count": c} for w, c in count.most_common(top_n)]

# ==== 8. 主动刷新全部索引 ====
@router.post("/refresh-index", summary="刷新知识库索引（基于当前所有文件）", tags=["admin"])
def refresh_index(admin: User = Depends(admin_required)):
    """
    主动刷新 FAISS 索引（不上传，仅重新读取 uploads 文件夹内容）。
    """
    try:
        # 读取 uploads 文件夹中所有合法后缀的文件
        all_files = [
            f for f in os.listdir(UPLOAD_FOLDER)
            if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))
            and f.lower().endswith((".txt", ".pdf", ".md", ".markdown"))
        ]

        if not all_files:
            raise HTTPException(status_code=404, detail="知识库中没有可索引文件")

        # 重新构建索引并保存（force_rebuild=True 全量重建，确保与 uploads 目录一致）
        if create_index_from_files(all_files, force_rebuild=True):
            reload_vector_db()
            return {
                "success": True,
                "message": f"索引刷新成功，共处理 {len(all_files)} 个文件。",
                "files": all_files
            }
        else:
            raise HTTPException(status_code=500, detail="索引刷新失败，请检查后端日志")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"索引刷新出错：{str(e)}")

# ==== SSRF 防护：仅允许公网 http/https，并逐跳校验重定向目标 ====
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not any(ip in net for net in _PRIVATE_NETWORKS)

def _validate_public_url(url: str):
    """校验 URL 协议，并解析主机名确认所有 IP 均为公网地址，拒绝内网/回环/保留地址（防 SSRF）。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持 http/https 协议")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="URL 缺少有效主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="无法解析主机名")
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise HTTPException(status_code=400, detail="禁止访问内网/回环地址")

def _safe_get(url: str, timeout: int = 30):
    """带 SSRF 防护的 GET：每次跳转前都校验目标，最多跟随 5 次重定向。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for _ in range(5):
        _validate_public_url(url)
        resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=False)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                raise HTTPException(status_code=500, detail="重定向响应缺少 Location")
            url = urljoin(url, location)
            continue
        resp.raise_for_status()
        return resp
    raise HTTPException(status_code=500, detail="重定向次数过多")

# ==== 9. 抓取网页 URL 并入库 ====
class FetchUrlRequest(BaseModel):
    url: str

@router.post("/fetch-url", summary="抓取网页 URL 并入库（仅管理员）")
def fetch_url(payload: FetchUrlRequest, admin: User = Depends(admin_required)):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="请提供 URL")

    # 1. 抓取网页（_safe_get 内含 SSRF 校验）
    try:
        resp = _safe_get(url, timeout=30)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"抓取网页失败: {e}")

    # 2. 解析 HTML，提取正文（去掉导航/脚本等噪音）
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator="\n", strip=True) if main else ""
    if not text:
        raise HTTPException(status_code=500, detail="未提取到正文内容")

    # 3. 生成文件名（优先用页面标题，否则用 URL 哈希）
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    safe_title = normalize_filename(title).strip("._")
    if safe_title:
        filename = safe_title[:60] + ".md"
    else:
        filename = "fetched_" + hashlib.md5(url.encode()).hexdigest()[:8] + ".md"
    filename = resolve_filename_conflict(Path(UPLOAD_FOLDER), filename)

    # 4. 保存为 .md（标注来源 URL）
    with open(Path(UPLOAD_FOLDER) / filename, "w", encoding="utf-8") as f:
        f.write(f"<!-- 来源: {url} -->\n\n# {title}\n\n{text}")

    # 5. 重建索引
    all_files = [
        f for f in os.listdir(UPLOAD_FOLDER)
        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f.lower().endswith((".pdf", ".txt", ".md", ".markdown"))
    ]
    if create_index_from_files(all_files, force_rebuild=True):
        reload_vector_db()
        return {"message": f"已抓取并入库: {filename}", "filename": filename}
    else:
        raise HTTPException(status_code=500, detail="索引重建失败")

# ==== 10. 抓取整个文档站点（同目录所有页面） ====
def _extract_same_dir_links(html: str, base_url: str) -> set:
    """提取与 base_url 同域名、同目录下的所有文档链接。"""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    base_dir = urlparse(base_url).path.rsplit("/", 1)[0] + "/"

    links = set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"]).split("#")[0]
        p = urlparse(full)
        if p.netloc != base_host or p.scheme not in ("http", "https"):
            continue
        if not p.path.startswith(base_dir):
            continue
        if p.path.endswith((".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".gif")):
            continue
        links.add(full)
    return links


def _already_fetched_urls() -> set:
    """扫描 uploads 目录，返回已抓取过的来源 URL（用于去重）。"""
    fetched = set()
    for f in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, f)
        if not os.path.isfile(path) or not f.lower().endswith((".md", ".txt", ".markdown")):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                head = fh.read(800)  # 来源标注在文件开头
                m = re.search(r"<!--\s*来源:\s*(.+?)\s*-->", head)
                if m:
                    fetched.add(m.group(1).strip())
        except Exception:
            continue
    return fetched


@router.post("/crawl-site", summary="抓取整个文档站点（同目录所有页面，仅管理员）")
def crawl_site(payload: FetchUrlRequest, admin: User = Depends(admin_required)):
    base_url = payload.url.strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="请提供 URL")

    # 1. 抓取首页，提取所有同目录链接（_safe_get 内含 SSRF 校验）
    try:
        resp = _safe_get(base_url, timeout=30)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"抓取首页失败: {e}")

    urls = _extract_same_dir_links(resp.text, base_url)
    urls.add(base_url)  # 包含首页本身

    # 2. 逐个抓取（限制最多 30 页，避免失控；跳过已抓取过的 URL 去重）
    fetched_urls = _already_fetched_urls()
    MAX_PAGES = 30
    fetched_files = []
    for url in sorted(urls)[:MAX_PAGES]:
        if url in fetched_urls:
            logger.info(f"跳过已抓取过的 URL: {url}")
            continue
        try:
            page = _safe_get(url, timeout=20)
            soup = BeautifulSoup(page.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            main = soup.find("main") or soup.find("article") or soup.body
            text = main.get_text(separator="\n", strip=True) if main else ""
            if not text:
                continue
            title = soup.title.string.strip() if soup.title and soup.title.string else url
            safe_title = normalize_filename(title).strip("._")
            filename = (safe_title[:60] if safe_title else "page_" + hashlib.md5(url.encode()).hexdigest()[:8]) + ".md"
            filename = resolve_filename_conflict(Path(UPLOAD_FOLDER), filename)
            with open(Path(UPLOAD_FOLDER) / filename, "w", encoding="utf-8") as f:
                f.write(f"<!-- 来源: {url} -->\n\n# {title}\n\n{text}")
            fetched_files.append(filename)
        except Exception:
            continue

    if not fetched_files:
        raise HTTPException(status_code=404, detail="未抓取到新页面（可能均已抓取过）")

    # 3. 重建索引
    all_files = [
        f for f in os.listdir(UPLOAD_FOLDER)
        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f)) and f.lower().endswith((".pdf", ".txt", ".md", ".markdown"))
    ]
    if create_index_from_files(all_files, force_rebuild=True):
        reload_vector_db()
        return {"message": f"已抓取 {len(fetched_files)} 个页面", "files": fetched_files}
    else:
        raise HTTPException(status_code=500, detail="索引重建失败")
