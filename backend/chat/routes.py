# backend/chat/routes.py

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from backend.chat import crud, schemas, models
import json
from backend.auth.routes import get_current_user
from backend.auth.models import User
from backend.database import SessionLocal
from typing import List

# === 新增，导入问答核心模块（生成智能回复）===
from backend.qa_handler import get_final_answer, prepare_context, generate_answer_stream

router = APIRouter(
    prefix="/chat",
    tags=["chat"]
)

# === 依赖注入：获取数据库 Session ===
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === 创建新会话 ===
@router.post("/conversations/", response_model=schemas.ConversationOut)
def create_conversation(
    payload: schemas.ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建新会话，支持自定义标题（可空）。
    """
    return crud.create_conversation(db, current_user, title=payload.title)

# === 获取当前用户的所有会话列表 ===
@router.get("/conversations/", response_model=List[schemas.ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取当前用户所有会话，按创建时间排序。
    """
    return crud.get_user_conversations(db, current_user)

# === 重命名会话 ===
@router.put("/conversations/{conversation_id}/rename")
def rename_conversation(
    conversation_id: int,
    payload: schemas.ConversationRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    重命名指定会话标题。
    """
    conversation = crud.get_conversation_by_id(db, conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权限访问")
    conversation.title = payload.title
    db.commit()
    db.refresh(conversation)
    return {"success": True, "new_title": conversation.title}

# === 删除会话 ===
@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除指定会话及其消息（物理删除）。
    """
    conversation = crud.get_conversation_by_id(db, conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权限访问")
    db.delete(conversation)
    db.commit()
    return {"success": True, "deleted_id": conversation_id}

# === 发送消息并让 AI 回复（多轮上下文拼接）===
@router.post("/conversations/{conversation_id}/messages/", response_model=schemas.MessageOut)
def send_message(
    conversation_id: int,
    payload: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    用户向指定会话发送新消息，AI 自动回复，均存库。
    """
    conversation = crud.get_conversation_by_id(db, conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权限访问")

    # 1️⃣ 保存用户消息
    user_msg = crud.create_message(db, conversation, role=payload.role, content=payload.content)

    # 2️⃣ 获取最近 N 条历史（仅拼进生成 prompt，不参与检索）
    previous_msgs = crud.get_messages_by_conversation(db, conversation)
    N = 10
    previous_msgs = previous_msgs[-N:] if len(previous_msgs) > N else previous_msgs
    history_context = "\n".join([f"{m.role}: {m.content}" for m in previous_msgs])

    # 3️⃣ 调用 AI（检索只用当前问题 payload.content，历史作为多轮上下文单独传入）
    try:
        result = get_final_answer(payload.content, history=history_context)
        ai_content = result.get("answer", "很抱歉，未能获取到明确的回答。")
    except Exception as e:
        ai_content = f"AI内部错误：{str(e)}"

    # 4️⃣ 保存 AI 消息
    crud.create_message(db, conversation, role="assistant", content=ai_content)

    return user_msg

# === 流式发送消息（SSE）：逐字返回 AI 回复 ===
@router.post("/conversations/{conversation_id}/messages/stream")
def send_message_stream(
    conversation_id: int,
    payload: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    conversation = crud.get_conversation_by_id(db, conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权限访问")

    # 1️⃣ 保存用户消息
    crud.create_message(db, conversation, role=payload.role, content=payload.content)

    # 2️⃣ 拼最近 N 条历史（仅用于生成，不参与检索）
    previous_msgs = crud.get_messages_by_conversation(db, conversation)
    N = 10
    previous_msgs = previous_msgs[-N:] if len(previous_msgs) > N else previous_msgs
    history_context = "\n".join([f"{m.role}: {m.content}" for m in previous_msgs])

    # 3️⃣ 准备检索上下文：只用当前问题检索，历史不作为检索输入
    context_chunks, is_comparison, allow_free_gen = prepare_context(payload.content)
    conv_id = conversation.id  # 提前取出 id，避免流式期间 session 已关闭

    # 4️⃣ 流式生成并保存
    def generate():
        # 先发送来源片段（供前端展示溯源）
        sources = [c.strip() for c in context_chunks if c.strip()]
        yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"

        full_answer = []
        for chunk in generate_answer_stream(payload.content, context_chunks, is_comparison, allow_free_gen, history=history_context):
            full_answer.append(chunk)
            yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
        # 保存 assistant 消息（用新 session，因为原请求 session 在流式期间已关闭）
        session = SessionLocal()
        try:
            msg = models.Message(conversation_id=conv_id, role="assistant", content="".join(full_answer))
            session.add(msg)
            session.commit()
        finally:
            session.close()
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

# === 获取会话的所有消息（按时间排序）===
@router.get("/conversations/{conversation_id}/messages/", response_model=List[schemas.MessageOut])
def list_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取指定会话的所有消息，按时间升序返回。
    """
    conversation = crud.get_conversation_by_id(db, conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权限访问")
    return crud.get_messages_by_conversation(db, conversation)

# === 消息反馈（点赞/点踩）===
@router.post("/messages/{message_id}/feedback")
def set_message_feedback(
    message_id: int,
    payload: schemas.MessageFeedback,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message = db.query(models.Message).filter(models.Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在")
    # 校验归属：消息必须属于当前用户的会话
    conversation = crud.get_conversation_by_id(db, message.conversation_id, current_user)
    if not conversation:
        raise HTTPException(status_code=404, detail="消息不存在或无权限")
    message.feedback = payload.feedback
    db.commit()
    return {"success": True, "message_id": message_id, "feedback": payload.feedback}
