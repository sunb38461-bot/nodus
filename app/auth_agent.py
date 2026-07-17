"""Agent Token 校验逻辑，供 MCP 工具复用"""
import logging
from datetime import datetime, timezone

from app.db import get_db

logger = logging.getLogger("nodus.auth_agent")

# ── Agent API 速率限制 ─────────────────────────────────────────────────────────
_agent_rate_store: dict[str, list[float]] = {}
_AGENT_RATE_LIMIT = 60  # 每个 token 每分钟最多 60 次请求


def _check_agent_rate_limit(token: str) -> bool:
    """返回 True 表示通过，False 表示超限"""
    now = datetime.now(timezone.utc).timestamp()
    window_start = now - 60
    if token not in _agent_rate_store:
        _agent_rate_store[token] = []
    _agent_rate_store[token] = [t for t in _agent_rate_store[token] if t > window_start]
    if len(_agent_rate_store[token]) >= _AGENT_RATE_LIMIT:
        return False
    _agent_rate_store[token].append(now)
    return True


async def validate_agent_token(token: str) -> dict | None:
    """
    校验 agent_tokens 表中的 token。
    成功返回 {user_id, token_row, owner_display_name, label}
    失败返回 None
    """
    if not token:
        return None

    # 速率限制检查
    if not _check_agent_rate_limit(token):
        logger.warning(f"Agent token 速率限制触发: {token[:8]}...")
        return None

    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            "SELECT at.*, u.display_name, u.username "
            "FROM agent_tokens at JOIN users u ON u.id = at.owner_user_id "
            "WHERE at.token = ? AND at.revoked = 0 AND at.expires_at > ?",
            (token, now)
        )
        row = await cursor.fetchone()
        if not row:
            return None

        # 更新心跳（异步，不阻塞）
        await db.execute(
            "UPDATE agent_tokens SET last_active_at = ? WHERE token = ?",
            (now, token)
        )
        await db.commit()

        return {
            "user_id": row["owner_user_id"],
            "token": row["token"],
            "label": row["label"] or "Agent",
            "display_name": row["display_name"] or row["username"],
            "first_used_at": row["first_used_at"],
        }
    finally:
        await db.close()


async def mark_token_first_used(token: str) -> bool:
    """将 token 标记为已首次使用（设置 first_used_at）。返回 True 表示本次成功标记，False 表示已被其他请求标记过。"""
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE agent_tokens SET first_used_at = ? WHERE token = ? AND first_used_at IS NULL",
            (now, token)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
