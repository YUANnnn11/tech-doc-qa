# backend/qa_handler.py

import requests
import os
import re
import logging
import jieba
import json

from langchain_ollama import OllamaEmbeddings
from .knowledge_base_processor import load_faiss_index
from .config import OLLAMA_EMBEDDING_MODEL, DEEPSEEK_API_KEY

logger = logging.getLogger("gadgetguide_ai.qa")

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL_NAME = "deepseek-chat"

vector_db = load_faiss_index()


def reload_vector_db():
    global vector_db
    vector_db = load_faiss_index()
    if vector_db:
        logger.info("FAISS 索引已在 qa_handler 中重新加载。")
    else:
        logger.warning("FAISS 索引在 qa_handler 中重新加载失败或索引为空。")
    return vector_db


def retrieve_context(query: str, k: int = 5, distance_threshold: float = 0.65) -> dict:
    """检索最相关片段。FAISS 默认 L2 距离，分数越小越相似，这里过滤掉距离过大（不够相似）的片段。"""
    if vector_db is None:
        logger.warning(f"retrieve_context (query: '{query}', k:{k}): 知识库索引未加载。")
        return {"error": "知识库索引未加载，请先处理知识库文档。"}
    try:
        logger.info(f"retrieve_context: 正在为查询 '{query}' 检索上下文 (k={k}, 距离阈值={distance_threshold})...")
        results = vector_db.similarity_search_with_score(query, k=k)
        # L2 距离：越小越相似。原 `score >= threshold` 方向写反了，会留下最不相似的片段。
        filtered_chunks = [doc.page_content for doc, score in results if score <= distance_threshold]
        logger.info(
            f"retrieve_context: 原始 top-{k} 命中距离 {[round(s, 4) for _, s in results]}，"
            f"过滤后保留 {len(filtered_chunks)} 个片段（距离 <= {distance_threshold}）"
        )
        return {"retrieved_chunks": filtered_chunks}
    except Exception as e:
        logger.error(f"retrieve_context: 检索上下文时出错 (查询: '{query}', k:{k}): {e}", exc_info=True)
        return {"error": f"检索上下文时出错: {e}"}


def extract_comparison_entities_refined(query: str) -> list[str]:
    """
    通用化的对比实体提取函数：
    - 不再仅限于 iPhone，而是适配任意英文/数字/连字符/空格组合的实体
    - 仍使用关键词判断是否是对比问题
    """
    pattern = r"([a-zA-Z0-9\- ]{2,})"
    comparison_keywords = ["对比", "区别", "升级", "和...相比", "与...比较", "差异", "不同点"]
    query_lower = query.lower()
    is_likely_comparison = any(keyword in query for keyword in comparison_keywords) and \
                           ("和" in query or "与" in query or "跟" in query)
    if is_likely_comparison:
        found_entities = re.findall(pattern, query)
        normalized_entities = sorted(list(set([name.strip() for name in found_entities if len(name.strip()) > 1])))
        if len(normalized_entities) >= 2:
            logger.info(f"extract_comparison_entities_refined: 识别到对比实体: {normalized_entities} 从查询: '{query}'")
            return normalized_entities[:2]  # 只返回前两个实体
        else:
            logger.debug(f"extract_comparison_entities_refined: 未能提取到至少两个实体。找到: {normalized_entities}")
    else:
        logger.debug(f"extract_comparison_entities_refined: 查询 '{query}' 未被识别为对比性查询。")
    return []


def chunks_relevant_to_query(chunks: list[str], query: str, min_hits: int = 1) -> bool:
    """
    判断知识块内容是否能直接用于回答本问题（关键字匹配，支持中英文）
    """
    # 英文/数字用正则分词，中文用 jieba 分词，避免整句中文被当成一个关键词
    keywords = set(re.findall(r'[a-zA-Z0-9]+', query.lower()))
    keywords |= set(w for w in jieba.cut(query) if len(w.strip()) > 1)
    if not keywords:
        return False
    hits = 0
    for chunk in chunks:
        content = chunk.lower()
        if any(word.lower() in content for word in keywords):
            hits += 1
    return hits >= min_hits


def prepare_context(query: str):
    """
    准备检索上下文：判断对比、检索、相关性校验。
    返回 (context_chunks, is_comparison, allow_free_gen)。
    """
    is_comparison = False
    comparison_entities = extract_comparison_entities_refined(query)
    context_chunks = []
    if comparison_entities:
        is_comparison = True
        temp_context_set = set()
        for entity_name in comparison_entities:
            entity_context_result = retrieve_context(entity_name, k=5)
            if entity_context_result.get("retrieved_chunks"):
                for chunk in entity_context_result["retrieved_chunks"]:
                    temp_context_set.add(chunk)
        context_chunks = list(temp_context_set)
    else:
        context_result = retrieve_context(query, k=10)
        context_chunks = context_result.get("retrieved_chunks", [])

    can_rag = len(context_chunks) > 0 and chunks_relevant_to_query(context_chunks, query)
    allow_free_gen = not can_rag
    if allow_free_gen:
        context_chunks = []
    return context_chunks, is_comparison, allow_free_gen


def _build_prompt(original_query: str, context_chunks: list[str], is_comparison: bool, allow_free_gen: bool, history: str = "") -> str:
    """根据场景构造发送给 LLM 的提示词。history 为多轮对话历史（仅用于生成，不参与检索）。"""
    context_str = "\n\n---\n\n".join(context_chunks)

    if is_comparison:
        prompt_instruction = (
            "你是一个技术对比分析师。请根据下方「参考信息」详细对比用户问题中提到的两个技术方案、版本或概念。"
            "重点列出它们在原理、用法、性能、适用场景等方面的差异。请**务必使用标准 Markdown 表格格式**输出对比结果，"
            "如果信息不足，请如实说明，不要编造内容。"
        )
    elif allow_free_gen:
        prompt_instruction = (
            "你是一个乐于助人的技术助手。如果下方「参考信息」为空或无用，请基于你的技术知识自由回答用户问题。"
            "务必在回答开头加上「【以下为AI自动生成，仅供参考】」。"
            "如果有「参考信息」，优先使用，答案应专业简洁、直接相关。"
        )
    else:
        prompt_instruction = (
            "你是一个技术文档问答助手。请严格根据下方「参考信息」回答用户的技术问题，确保答案准确、相关、简明扼要。"
            "如果参考信息中没有答案，请如实说明，不要编造内容。"
        )

    history_block = f"对话历史：\n---\n{history}\n---\n\n" if history else ""

    return f"""{prompt_instruction}

{history_block}参考信息：
---
{context_str}
---
用户问题：{original_query}

请给出您的详细、专业的回答：
"""


def generate_answer_from_llm(
    original_query: str,
    context_chunks: list[str],
    is_comparison: bool = False,
    allow_free_gen: bool = False,
    history: str = ""
) -> dict:
    """
    调用 DeepSeek API 生成答案。
    - 提示词复用 _build_prompt，保证与流式接口一致（技术文档主题）。
    """
    if not DEEPSEEK_API_KEY:
        logger.error("generate_answer_from_llm: DEEPSEEK_API_KEY 未配置。")
        return {"error": "AI 服务配置不完整 (API Key缺失)。"}

    prompt_template = _build_prompt(original_query, context_chunks, is_comparison, allow_free_gen, history)

    logger.debug(f"generate_answer_from_llm: 发送给 LLM 的 Prompt:\n{prompt_template}\n")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": DEEPSEEK_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt_template}],
        "max_tokens": 1500,
        "temperature": 0.3,
    }

    try:
        logger.info(f"generate_answer_from_llm: 正在调用 DeepSeek API (模型: {DEEPSEEK_MODEL_NAME})...")
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        response_data = response.json()
        if response_data.get("choices") and len(response_data["choices"]) > 0:
            message_content = response_data["choices"][0].get("message", {}).get("content", "")
            if message_content:
                logger.info("generate_answer_from_llm: 成功获取到答案。")
                return {"answer": message_content.strip()}
            else:
                logger.warning("generate_answer_from_llm: DeepSeek API 返回了空的答案。")
                return {"error": "AI 服务返回了空的答案内容。"}
        else:
            logger.error(f"generate_answer_from_llm: DeepSeek API 响应格式不符合预期: {response_data}")
            return {"error": "AI 服务响应格式不正确。"}
    except requests.exceptions.Timeout:
        logger.error("generate_answer_from_llm: DeepSeek API 超时。")
        return {"error": "AI 服务请求超时，请稍后再试。"}
    except requests.exceptions.RequestException as e:
        logger.error(f"generate_answer_from_llm: DeepSeek API 请求错误: {e}", exc_info=True)
        return {"error": f"与 AI 服务通信时发生错误: {e}"}
    except Exception as e:
        logger.error(f"generate_answer_from_llm: 处理 LLM 响应或未知错误: {e}", exc_info=True)
        return {"error": f"处理 AI 服务响应时发生未知错误: {e}"}


def generate_answer_stream(original_query: str, context_chunks: list[str], is_comparison: bool = False, allow_free_gen: bool = False, history: str = ""):
    """
    流式生成答案：逐块 yield 文本内容（供 SSE 接口使用）。
    """
    if not DEEPSEEK_API_KEY:
        yield "AI 服务配置不完整 (API Key缺失)。"
        return

    prompt_template = _build_prompt(original_query, context_chunks, is_comparison, allow_free_gen, history)

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": DEEPSEEK_MODEL_NAME,
        "messages": [{"role": "user", "content": prompt_template}],
        "max_tokens": 1500,
        "temperature": 0.3,
        "stream": True,  # 开启流式输出
    }

    try:
        logger.info("generate_answer_stream: 开始流式调用 DeepSeek ...")
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, stream=True, timeout=60)
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
    except requests.exceptions.Timeout:
        logger.error("generate_answer_stream: DeepSeek 超时。")
        yield "AI 服务请求超时，请稍后再试。"
    except requests.exceptions.RequestException as e:
        logger.error(f"generate_answer_stream: DeepSeek 请求错误: {e}", exc_info=True)
        yield f"与 AI 服务通信时发生错误: {e}"
    except Exception as e:
        logger.error(f"generate_answer_stream: 未知错误: {e}", exc_info=True)
        yield f"处理 AI 服务响应时发生未知错误: {e}"


def get_final_answer(query: str, history: str = "") -> dict:
    """
    核心对话入口：智能判断是否对比问题，是否有可用知识库，智能切换自由生成/基于知识的回答。
    query 为当前用户问题（用于检索），history 为多轮历史（仅用于生成）。
    """
    logger.info(f"get_final_answer: 开始处理查询: '{query}'")
    is_comparison = False

    # 先判断是否是对比问题
    comparison_entities = extract_comparison_entities_refined(query)
    context_chunks = []
    if comparison_entities:
        is_comparison = True
        temp_context_set = set()
        k_per_entity = 5
        for entity_name in comparison_entities:
            entity_context_result = retrieve_context(entity_name, k=k_per_entity)
            if entity_context_result.get("retrieved_chunks"):
                for chunk in entity_context_result["retrieved_chunks"]:
                    temp_context_set.add(chunk)
        context_chunks = list(temp_context_set)
    else:
        context_result = retrieve_context(query, k=10)
        context_chunks = context_result.get("retrieved_chunks", [])

    # 如果知识块无用，则走自由生成
    can_rag = len(context_chunks) > 0 and chunks_relevant_to_query(context_chunks, query)
    if not can_rag:
        logger.info("get_final_answer: 知识块无用，直接让AI自由发挥并加标注。")
        llm_result = generate_answer_from_llm(query, [], is_comparison=is_comparison, allow_free_gen=True, history=history)
        if "error" in llm_result:
            return {"error": llm_result["error"]}
        answer = llm_result.get("answer", "")
        if not answer.strip().startswith("【以下为AI自动生成，仅供参考】"):
            answer = "【以下为AI自动生成，仅供参考】" + answer
        return {"answer": answer}

    # 有可用知识块则优先使用
    llm_result = generate_answer_from_llm(query, context_chunks, is_comparison=is_comparison, history=history)
    if "error" in llm_result:
        return {"error": llm_result["error"]}
    return {"answer": llm_result.get("answer", "【以下为AI自动生成，仅供参考】AI 未能生成有效的回答。")}
