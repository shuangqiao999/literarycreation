"""系统健康校验 — 启动前检测嵌入模型兼容性 + 蓝图完整性。"""
from __future__ import annotations

from typing import Any


def validate_embedding_model(embed_base: str, embed_model: str,
                              log_fn: Any = None) -> bool:
    """快速校验嵌入模型是否可用。失败返回 False。"""
    import json as _json
    import logging
    logger = logging.getLogger(__name__)
    try:
        import httpx
        r = httpx.post(
            f"{embed_base.rstrip('/')}/embeddings",
            json={"model": embed_model, "input": "你好世界"},
            timeout=15.0,
        )
        if r.status_code == 200:
            return True
        msg = f"嵌入模型校验失败 ({r.status_code})"
        if r.status_code == 404:
            msg += "：模型不存在——请确认模型名称是否正确"
        elif r.status_code == 400:
            msg += "：请求格式错误——模型可能不兼容此 API"
        if log_fn:
            log_fn("orchestrator", f"[嵌入模型校验] {msg}")
        logger.warning("[Health] Embedding validation: %s", msg)
        return False
    except Exception as e:
        msg = f"嵌入模型连接失败: {e}"
        if log_fn:
            log_fn("orchestrator", f"[嵌入模型校验] {msg}")
        logger.warning("[Health] Embedding validation: %s", msg)
        return False


def validate_blueprint(blueprint: dict[str, Any] | None) -> list[str]:
    """校验蓝图必填字段完整性。返回缺失字段列表，空列表表示通过。"""
    if blueprint is None:
        return ["蓝图为空"]
    missing = []
    for field in ["logline", "key_events", "characters"]:
        if not blueprint.get(field):
            missing.append(field)
    chars = blueprint.get("characters") or []
    if chars and not isinstance(chars, list):
        missing.append("characters(应为数组)")
    if len(chars) < 2:
        missing.append(f"角色数不足({len(chars)}，至少需要 2 个)")
    events = blueprint.get("key_events") or []
    if events and not isinstance(events, list):
        missing.append("key_events(应为数组)")
    # 检测空内容
    for c in chars:
        if not isinstance(c, dict):
            continue
        if not c.get("name"):
            missing.append("角色缺失 name 字段")
            break
        if not c.get("arc"):
            missing.append(f"角色「{c.get('name','?')}」缺失弧光")
    for e in events:
        if not isinstance(e, dict):
            continue
        if not e.get("event"):
            missing.append(f"第{e.get('round','?')}轮事件缺失 description")
            break
    return missing
