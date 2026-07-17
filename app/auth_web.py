"""人类用户 Web 鉴权：注册 / 登录 / Session 管理"""
import secrets
import html
import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Request, Response, Cookie, Depends, HTTPException
from typing import Optional

from app.db import get_db
from app.config import (
    SESSION_COOKIE_NAME, SESSION_EXPIRE_DAYS, SESSION_REMEMBER_DAYS,
    LOGIN_MAX_FAILURES, LOGIN_LOCK_SECONDS, RATE_LIMIT_PER_MINUTE,
    COOKIE_SECURE, MCP_BASE_URL
)

logger = logging.getLogger("nodus.auth_web")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── 简易 IP 速率限制 ─────────────────────────────────────────────────────────
_rate_limit_store: dict[str, list[float]] = {}


def _check_rate_limit(ip: str) -> bool:
    """返回 True 表示通过，False 表示超限"""
    now = datetime.now(timezone.utc).timestamp()
    window_start = now - 60
    if ip not in _rate_limit_store:
        _rate_limit_store[ip] = []
    _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if t > window_start]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_PER_MINUTE:
        return False
    _rate_limit_store[ip].append(now)
    return True


# ── 邀请码 ───────────────────────────────────────────────────
def _gen_invite_code() -> str:
    """生成 8 位短邀请码"""
    return secrets.token_urlsafe(6)[:8]


async def _ensure_invite_code(db, user_id: int) -> str:
    """获取用户邀请码，不存在则惰性生成并保存（保证唯一）"""
    cursor = await db.execute("SELECT invite_code FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    if row and row["invite_code"]:
        return row["invite_code"]
    for _ in range(10):
        code = _gen_invite_code()
        cursor = await db.execute("SELECT id FROM users WHERE invite_code = ?", (code,))
        if not await cursor.fetchone():
            await db.execute("UPDATE users SET invite_code = ? WHERE id = ?", (code, user_id))
            await db.commit()
            return code
    # 极端碰撞兵底：拼接 user_id
    code = f"{_gen_invite_code()}{user_id}"
    await db.execute("UPDATE users SET invite_code = ? WHERE id = ?", (code, user_id))
    await db.commit()
    return code


def _build_invite_url(code: str, request: Request = None) -> str:
    """生成邀请链接，支持反向代理 HTTPS（X-Forwarded-Proto 头）"""
    if request:
        try:
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            host = request.headers.get("host", request.url.hostname)
            port = request.url.port
            
            if port and port not in (80, 443):
                base = f"{scheme}://{host}:{port}"
            else:
                base = f"{scheme}://{host}"
        except Exception:
            base = MCP_BASE_URL.rstrip('/')
    else:
        base = MCP_BASE_URL.rstrip('/')
    return f"{base}/register.html?invite={code}"


# ── Session 解析 ──────────────────────────────────────────────────────────────
async def get_current_user(request: Request) -> Optional[dict]:
    """从 Cookie 解析 session，返回用户信息 dict；无效则返回 None"""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            "SELECT ws.user_id, ws.expires_at, u.id, u.username, u.display_name, "
            "u.account_type, u.role, u.created_at FROM web_sessions ws "
            "JOIN users u ON u.id = ws.user_id "
            "WHERE ws.session_id = ? AND ws.expires_at > ?",
            (session_id, now)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "account_type": row["account_type"],
            "role": row["role"],
            "created_at": row["created_at"],
        }
    finally:
        await db.close()


async def require_user(request: Request) -> dict:
    """依赖注入：必须已登录"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "NOT_AUTHENTICATED", "message": "请先登录"})
    return user


# ── 注册 ──────────────────────────────────────────────────────────────────────
@router.post("/register")
async def register(request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return {"ok": False, "error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}}

    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    display_name = body.get("display_name", "").strip() or None
    invite_code = (body.get("invite_code") or "").strip()

    # 校验
    import re
    if not re.match(r'^[a-zA-Z0-9_]{3,32}$', username):
        return {"ok": False, "error": {"code": "INVALID_USERNAME", "message": "用户名需为 3-32 位字母数字下划线"}}
    if len(password) < 8:
        return {"ok": False, "error": {"code": "PASSWORD_TOO_SHORT", "message": "密码至少 8 位"}}

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = datetime.now(timezone.utc).isoformat()
    safe_display = html.escape(display_name) if display_name else None

    db = await get_db()
    try:
        # 解析邀请人（如有）
        inviter_id = None
        inviter_name = None
        if invite_code:
            cursor = await db.execute(
                "SELECT id, username, display_name FROM users WHERE invite_code = ?", (invite_code,)
            )
            inviter = await cursor.fetchone()
            if inviter:
                inviter_id = inviter["id"]
                inviter_name = inviter["display_name"] or inviter["username"]

        try:
            cursor = await db.execute(
                "INSERT INTO users (username, password_hash, display_name, account_type, role, created_at, invited_by) "
                "VALUES (?, ?, ?, 'human', 'user', ?, ?)",
                (username, password_hash, safe_display, now, inviter_id)
            )
            await db.commit()
            new_user_id = cursor.lastrowid
        except Exception as e:
            if "UNIQUE" in str(e):
                return {"ok": False, "error": {"code": "USERNAME_TAKEN", "message": "用户名已被占用"}}
            raise
        logger.info(f"新用户注册: {username[:8]}..." + (f" (邀请人={inviter_id})" if inviter_id else ""))

        # 邀请注册 → 写频道动态
        if inviter_id:
            try:
                from app.system_agent import write_event
                new_label = safe_display or username
                await write_event(new_user_id, new_label, "user_invited_join",
                    f"{new_label} 通过 {inviter_name} 的邀请加入了 Nodus 🎉")
            except Exception:
                pass

        return {"ok": True, "data": {"message": "注册成功，请登录"}}
    finally:
        await db.close()


# ── 登录 ──────────────────────────────────────────────────────────────────────
@router.post("/login")
async def login(request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return {"ok": False, "error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}}

    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    remember = body.get("remember", False)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, display_name, account_type, role, failure_count, locked_until "
            "FROM users WHERE username = ?", (username,)
        )
        user = await cursor.fetchone()
        if not user:
            return {"ok": False, "error": {"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误", "remaining_attempts": LOGIN_MAX_FAILURES}}

        # 检查锁定
        locked_until = user["locked_until"]
        if locked_until:
            lock_exp = datetime.fromisoformat(locked_until)
            if datetime.now(timezone.utc) < lock_exp:
                remaining = int((lock_exp - datetime.now(timezone.utc)).total_seconds() / 60) + 1
                return {"ok": False, "error": {"code": "ACCOUNT_LOCKED", "message": f"账号已锁定，请 {remaining} 分钟后再试"}}

        # 验证密码
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            new_count = (user["failure_count"] or 0) + 1
            if new_count >= LOGIN_MAX_FAILURES:
                locked_until = (datetime.now(timezone.utc) + timedelta(seconds=LOGIN_LOCK_SECONDS)).isoformat()
                await db.execute(
                    "UPDATE users SET failure_count = ?, locked_until = ? WHERE id = ?",
                    (new_count, locked_until, user["id"])
                )
                await db.commit()
                return {"ok": False, "error": {"code": "ACCOUNT_LOCKED", "message": f"连续失败 {LOGIN_MAX_FAILURES} 次，账号已锁定 15 分钟"}}
            await db.execute("UPDATE users SET failure_count = ? WHERE id = ?", (new_count, user["id"]))
            await db.commit()
            remaining = LOGIN_MAX_FAILURES - new_count
            return {"ok": False, "error": {"code": "INVALID_CREDENTIALS", "message": f"用户名或密码错误，还剩 {remaining} 次尝试机会", "remaining_attempts": remaining}}

        # 登录成功，重置失败计数
        await db.execute("UPDATE users SET failure_count = 0, locked_until = NULL WHERE id = ?", (user["id"],))

        # 创建 session
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expire_days = SESSION_REMEMBER_DAYS if remember else SESSION_EXPIRE_DAYS
        expires_at = (now + timedelta(days=expire_days)).isoformat()
        ua = request.headers.get("user-agent", "")[:500]

        await db.execute(
            "INSERT INTO web_sessions (session_id, user_id, created_at, expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
            (session_id, user["id"], now.isoformat(), expires_at, ua)
        )
        await db.commit()

        # 设置 Cookie
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            secure=COOKIE_SECURE,  # 通过环境变量 COOKIE_SECURE 控制，生产环境设为 true
            samesite="lax",
            max_age=expire_days * 86400
        )
        logger.info(f"用户登录: {username[:8]}... session={session_id[:8]}...")
        return {
            "ok": True,
            "data": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"] or user["username"],
            }
        }
    finally:
        await db.close()


# ── 登出 ──────────────────────────────────────────────────────────────────────
@router.post("/logout")
async def logout(request: Request, response: Response, user: dict = Depends(require_user)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        db = await get_db()
        try:
            await db.execute("DELETE FROM web_sessions WHERE session_id = ?", (session_id,))
            await db.commit()
        finally:
            await db.close()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True, "data": {"message": "已登出"}}


# ── 当前用户 ──────────────────────────────────────────────────────────────────
@router.get("/me")
async def me(user: dict = Depends(require_user)):
    return {"ok": True, "data": user}


# ── 我的邀请码 ──────────────────────────────────────────
async def _my_invite_impl(user, request: Request = None):
    db = await get_db()
    try:
        code = await _ensure_invite_code(db, user["id"])
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE invited_by = ?", (user["id"],)
        )
        invited_count = (await cursor.fetchone())["cnt"]
        return {
            "ok": True,
            "data": {
                "code": code,
                "url": _build_invite_url(code, request),
                "invited_count": invited_count,
            }
        }
    finally:
        await db.close()


@router.get("/my-invite")
async def my_invite(request: Request, user: dict = Depends(require_user)):
    return await _my_invite_impl(user, request)


# ── 邀请人信息（公开，供注册页展示） ────────────────────────────
@router.get("/invite-info")
async def invite_info(code: str = ""):
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": {"code": "NO_CODE", "message": "缺少邀请码"}}
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT username, display_name FROM users WHERE invite_code = ?", (code,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "error": {"code": "INVALID_CODE", "message": "邀请码无效"}}
        return {"ok": True, "data": {"inviter": row["display_name"] or row["username"]}}
    finally:
        await db.close()
