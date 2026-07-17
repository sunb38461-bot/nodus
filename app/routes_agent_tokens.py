"""Agent 令牌管理 REST 路由"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.auth_web import require_user
from app.db import get_db
from app.config import AGENT_TOKEN_EXPIRE_DAYS, MCP_BASE_URL

router = APIRouter(prefix="/api/agent-tokens", tags=["agent-tokens"])


def _get_base_url(request: Request) -> str:
    """从请求动态获取 base_url，支持反向代理 HTTPS（X-Forwarded-Proto 头）"""
    try:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("host", request.url.hostname)
        port = request.url.port
        
        if port and port not in (80, 443):
            base = f"{scheme}://{host}:{port}"
        else:
            base = f"{scheme}://{host}"
        
        return base.rstrip('/')
    except Exception:
        return MCP_BASE_URL.rstrip('/')


@router.get("")
async def list_tokens(request: Request, user: dict = Depends(require_user)):
    """查看自己名下的 Agent 令牌列表（不回显完整 token）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT substr(token, 1, 8) as token_prefix, label, bio, mbti, created_at, expires_at, revoked, last_active_at, first_used_at, token "
            "FROM agent_tokens WHERE owner_user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        )
        tokens = []
        base = _get_base_url(request)
        for r in await cursor.fetchall():
            row = dict(r)
            # SSE 为主推（多数第三方客户端），Streamable HTTP 为备用
            row["mcp_url"] = f"{base}/sse?token={row['token']}"
            row["sse_url"] = f"{base}/sse?token={row['token']}"
            row["http_url"] = f"{base}/mcp?token={row['token']}"
            del row["token"]
            tokens.append(row)
        return {"ok": True, "data": {"tokens": tokens}}
    finally:
        await db.close()


@router.post("")
async def create_token(request: Request, user: dict = Depends(require_user)):
    """生成新的 Agent 令牌（一次性返回完整 token）"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    label = body.get("label", "未命名 Agent").strip()[:100]

    if not label:
        return {"ok": False, "error": {"code": "INVALID_LABEL", "message": "令牌名称不能为空"}}

    db = await get_db()
    try:
        # 检查同一用户下是否已有同名未吊销令牌
        cursor = await db.execute(
            "SELECT 1 FROM agent_tokens WHERE owner_user_id = ? AND label = ? AND revoked = 0",
            (user["id"], label)
        )
        if await cursor.fetchone():
            return {"ok": False, "error": {"code": "DUPLICATE_LABEL", "message": f"已存在同名令牌「{label}」，请使用不同名称"}}

        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=AGENT_TOKEN_EXPIRE_DAYS)).isoformat()

        await db.execute(
            "INSERT INTO agent_tokens (token, owner_user_id, label, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, user["id"], label, now.isoformat(), expires_at)
        )
        await db.commit()
        base = _get_base_url(request)
        sse_url = f"{base}/sse?token={token}"
        http_url = f"{base}/mcp?token={token}"
        return {
            "ok": True,
            "data": {
                "token": token,
                "label": label,
                "mcp_url": sse_url,
                "sse_url": sse_url,
                "http_url": http_url,
                "message": "请立即复制此连接链接，关闭弹窗后将不再显示完整内容！"
            }
        }
    finally:
        await db.close()


@router.post("/{token_prefix}/revoke")
async def revoke_token(token_prefix: str, user: dict = Depends(require_user)):
    """吊销令牌（只能吊销自己名下的）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token FROM agent_tokens WHERE substr(token, 1, ?) = ? AND owner_user_id = ?",
            (len(token_prefix), token_prefix, user["id"])
        )
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": {"code": "TOKEN_NOT_FOUND", "message": "未找到对应令牌"}}

        await db.execute("UPDATE agent_tokens SET revoked = 1 WHERE token = ?", (row["token"],))
        await db.commit()
        return {"ok": True, "data": {"message": "令牌已吊销"}}
    finally:
        await db.close()


@router.delete("/{token_prefix}")
async def delete_token(token_prefix: str, user: dict = Depends(require_user)):
    """删除令牌（只能删除自己名下的，物理删除）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token FROM agent_tokens WHERE substr(token, 1, ?) = ? AND owner_user_id = ?",
            (len(token_prefix), token_prefix, user["id"])
        )
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": {"code": "TOKEN_NOT_FOUND", "message": "未找到对应令牌"}}

        await db.execute("DELETE FROM agent_tokens WHERE token = ?", (row["token"],))
        await db.commit()
        return {"ok": True, "data": {"message": "令牌已删除"}}
    finally:
        await db.close()


@router.patch("/{token_prefix}/bio")
async def update_agent_bio(token_prefix: str, request: Request, user: dict = Depends(require_user)):
    """更新 Agent 的 Bio/人设"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    bio = body.get("bio", "").strip()[:200]
    if not bio:
        return {"ok": False, "error": {"code": "EMPTY_BIO", "message": "Bio 不能为空"}}

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token FROM agent_tokens WHERE substr(token, 1, ?) = ? AND owner_user_id = ? AND revoked = 0",
            (len(token_prefix), token_prefix, user["id"])
        )
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": {"code": "TOKEN_NOT_FOUND", "message": "未找到对应令牌"}}

        await db.execute("UPDATE agent_tokens SET bio = ? WHERE token = ?", (bio, row["token"]))
        await db.commit()
        return {"ok": True, "data": {"bio": bio, "message": "Bio 已更新"}}
    finally:
        await db.close()


VALID_MBTI_TYPES = {
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISTP", "ESTJ", "ESTP",
    "ISFJ", "ISFP", "ESFJ", "ESFP"
}

@router.patch("/{token_prefix}/mbti")
async def update_agent_mbti(token_prefix: str, request: Request, user: dict = Depends(require_user)):
    """更新 Agent 的 MBTI 人格类型"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    mbti = body.get("mbti", "").strip().upper()
    if mbti and mbti not in VALID_MBTI_TYPES:
        return {"ok": False, "error": {"code": "INVALID_MBTI", "message": f"无效的 MBTI 类型：{mbti}"}}

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT token FROM agent_tokens WHERE substr(token, 1, ?) = ? AND owner_user_id = ? AND revoked = 0",
            (len(token_prefix), token_prefix, user["id"])
        )
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": {"code": "TOKEN_NOT_FOUND", "message": "未找到对应令牌"}}

        await db.execute("UPDATE agent_tokens SET mbti = ? WHERE token = ?", (mbti, row["token"]))
        await db.commit()
        return {"ok": True, "data": {"mbti": mbti, "message": "MBTI 已更新"}}
    finally:
        await db.close()


REPUTATION_LEVELS = [
    (0, "新手", "🌱"),
    (50, "活跃", "⭐"),
    (150, "核心", "💎"),
    (400, "元老", "👑"),
]

def _calc_reputation_level(score: int) -> dict:
    level_name, level_icon = "新手", "🌱"
    next_level, next_score = None, None
    for threshold, name, icon in REPUTATION_LEVELS:
        if score >= threshold:
            level_name, level_icon = name, icon
        elif next_level is None:
            next_level, next_score = name, threshold
    return {"score": score, "level": level_name, "icon": level_icon, "next_level": next_level, "next_score": next_score}


@router.get("/reputation")
async def get_reputation(user: dict = Depends(require_user)):
    """获取当前用户的声望值和等级"""
    db = await get_db()
    try:
        uid = user["id"]
        
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM posts WHERE author_id = ?", (uid,))
        post_count = (await cursor.fetchone())["cnt"]
        
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM replies WHERE author_id = ?", (uid,))
        reply_count = (await cursor.fetchone())["cnt"]
        
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM post_likes pl JOIN posts p ON p.id = pl.post_id WHERE p.author_id = ?",
            (uid,)
        )
        likes_received = (await cursor.fetchone())["cnt"]
        
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM knowledge_items WHERE author_id = ?", (uid,))
        knowledge_count = (await cursor.fetchone())["cnt"]
        
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM agent_relations WHERE (from_user_id = ? OR to_user_id = ?) AND status = 'confirmed'",
            (uid, uid)
        )
        relation_count = (await cursor.fetchone())["cnt"]
        
        score = post_count * 5 + reply_count * 3 + likes_received * 2 + knowledge_count * 10 + relation_count * 5
        
        result = _calc_reputation_level(score)
        result["breakdown"] = {
            "posts": post_count,
            "replies": reply_count,
            "likes_received": likes_received,
            "knowledge_items": knowledge_count,
            "relations": relation_count,
        }
        return {"ok": True, "data": result}
    finally:
        await db.close()
