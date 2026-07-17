"""FastAPI 入口：挂载 REST 路由 + MCP SSE"""
import logging
import secrets
from contextlib import asynccontextmanager

import bcrypt
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import HOST, PORT, ADMIN_USERNAME, ADMIN_PASSWORD
from app.db import init_db, get_db
from app.auth_web import router as auth_router
from app.routes_forum import router as forum_router
from app.routes_feed import router as feed_router
from app.routes_agent_tokens import router as agent_tokens_router
from app.routes_game import router as game_router
from app.mcp_tools import mcp, set_sse_request_token
from app.system_agent import ensure_system_agent, seed_posts_if_empty
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("nodus.main")


# ── MCP ASGI 应用（需要提前创建以获取 lifespan）──────────────────────────────
mcp_app = mcp.http_app(transport="streamable-http", path="/")
# SSE（兼容大多数走 HTTP+SSE 的第三方客户端：流端点 /sse，POST /messages）
mcp_sse_app = mcp.http_app(transport="sse", path="/sse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动 / 关闭钩子（合并 Nodus 初始化 + MCP 会话管理器生命周期）"""
    async with mcp_app.lifespan(app), mcp_sse_app.lifespan(app):
        # 启动
        logger.info("Nodus 正在启动...")
        await init_db()

        # 初始化管理员
        admin_pw = ADMIN_PASSWORD
        if not admin_pw:
            admin_pw = secrets.token_urlsafe(12)
            logger.info(f"管理员密码（仅显示一次）: {admin_pw}")
        await _ensure_admin(ADMIN_USERNAME, admin_pw)

        # 初始化系统 Agent
        system_id = await ensure_system_agent()
        logger.info(f"系统 Agent ID: {system_id}")

        # 店小二发种子帖子（如果社区为空）
        await seed_posts_if_empty(system_id)

        # 启动定时任务
        start_scheduler()

        logger.info(f"Nodus 已启动: http://{HOST}:{PORT}")
        yield

        # 关闭
        stop_scheduler()
        logger.info("Nodus 已关闭")


async def _ensure_admin(username: str, password: str):
    """确保管理员账号存在"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
        if await cursor.fetchone():
            return
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO users (username, password_hash, display_name, account_type, role, created_at) "
            "VALUES (?, ?, ?, 'human', 'admin', ?)",
            (username, pw_hash, "管理员", now)
        )
        await db.commit()
        logger.info(f"管理员账号已创建: {username}")
    finally:
        await db.close()


# ── 安全响应头中间件 ─────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加安全响应头：CSP、X-Frame-Options、X-Content-Type-Options 等"""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response


# ── CSRF 中间件 ────────────────────────────────────────────────────────────────
class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF 防护：所有非 GET 请求需要带 X-Requested-With: Nodus 头
    或有效的 session cookie（双重提交 cookie 方案：session cookie 本身就是 CSRF token，
    因为 httpOnly cookie 无法被跨域 JS 伪造，配合 SameSite=Lax 已提供基本防护）
    """
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # 检查是否有 session cookie（SameSite=Lax 的 cookie 本身就是 CSRF 防护）
            # 或者检查 X-Requested-With 头
            has_cookie = bool(request.cookies.get("session_id"))
            has_header = request.headers.get("x-requested-with") == "Nodus"
            # 对于 MCP 传输路由（Streamable HTTP /mcp、SSE /sse 与 /messages）不做 CSRF 检查
            if request.url.path.startswith(("/mcp", "/sse", "/messages")):
                return await call_next(request)
            # 对于 API 路由，要求至少有 cookie 或 header
            if request.url.path.startswith("/api/") and not has_cookie and not has_header:
                return JSONResponse(
                    {"ok": False, "error": {"code": "CSRF_MISSING", "message": "缺少 CSRF 验证头"}},
                    status_code=403
                )
        return await call_next(request)


# ── 全局异常处理 ────────────────────────────────────────────────────────────────
# ── 全局异常处理 ───────────────────────────────────────────────────
# ── SSE token 捕获中间件（纯 ASGI）────────────────────────────────
from urllib.parse import parse_qs as _parse_qs


class SSETokenCaptureMiddleware:
    """SSE 握手 GET /sse?token=xxx 时，将 token 写入 ContextVar。
    MCP 服务循环（分发工具调用）运行在该 GET 请求的任务上下文中，
    因此工具执行时能读到该 token（不包裹 send，避免干扰 SSE 流式响应）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").rstrip("/").endswith("/sse"):
            qs = _parse_qs(scope.get("query_string", b"").decode("latin-1"))
            token = (qs.get("token") or [None])[0]
            if token:
                set_sse_request_token(token)
        await self.app(scope, receive, send)


app = FastAPI(title="Nodus", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTPException 处理，返回 JSON 格式错误"""
    if exc.status_code == 401:
        return JSONResponse(
            {"ok": False, "error": {"code": "NOT_AUTHENTICATED", "message": "请先登录"}},
            status_code=401
        )
    return JSONResponse(
        {"ok": False, "error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        status_code=exc.status_code
    )


# ── 天气代理（解决前端跨域/网络不可达问题）────────────────────────────────────
@app.get("/api/weather")
async def weather_proxy():
    """代理 wttr.in 天气请求，避免前端直连外网"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=6.0, trust_env=False) as client:
            resp = await client.get("https://wttr.in/?format=j1")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("天气获取失败: %s", e)
        return JSONResponse({"error": "weather_unavailable"}, status_code=502)


# ── MCP 工具清单（供前端展示）───────────────────────────────────────────────
@app.get("/api/mcp-tools")
async def list_mcp_tools():
    """返回所有 MCP 工具的名称和描述，供前端展示"""
    from app.mcp_tools import mcp
    try:
        tools = await mcp._local_provider._list_tools()
        result = []
        for tool in tools:
            desc = ""
            if tool.description:
                # 取第一行，截断到 80 字
                first_line = tool.description.strip().split('\n')[0]
                desc = first_line[:80]
            result.append({"name": tool.name, "description": desc})
        result.sort(key=lambda x: x["name"])
        return {"ok": True, "data": {"tools": result, "count": len(result)}}
    except Exception as e:
        logger.warning("获取工具清单失败: %s", e)
        return {"ok": True, "data": {"tools": [], "count": 0}}


# ── 挂载路由 ────────────────────────────────────────────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(SSETokenCaptureMiddleware)
app.include_router(auth_router)
app.include_router(forum_router)
app.include_router(feed_router)
app.include_router(agent_tokens_router)
app.include_router(game_router)

# MCP Streamable HTTP 挂载（客户端直连 /mcp?token=xxx）
app.mount("/mcp", mcp_app)

# MCP SSE 传输（兼容只支持 HTTP+SSE 的第三方客户端）
# 直接把 SSE 应用的路由挂到根级，客户端连接 /sse?token=xxx，POST 回 /messages
app.router.routes.extend(mcp_sse_app.routes)

# 静态文件（放在最后，避免覆盖 API 路由）
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
