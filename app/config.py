"""Nodus 集中配置"""
import os
import secrets

# ── 数据库 ──────────────────────────────────────────────────────────────────
DATABASE_PATH = os.getenv("NODUS_DB_PATH", "nodus.db")

# ── Web Session ─────────────────────────────────────────────────────────────
SESSION_COOKIE_NAME = "session_id"
SESSION_EXPIRE_DAYS = 7          # 默认 7 天
SESSION_REMEMBER_DAYS = 30       # "记住我" 30 天

# ── Agent Token ─────────────────────────────────────────────────────────────
AGENT_TOKEN_EXPIRE_DAYS = 90

# ── MCP 连接链接 ─────────────────────────────────────────────────────────────
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "http://localhost:8000")

# ── 在线判定 ─────────────────────────────────────────────────────────────────
ONLINE_THRESHOLD_SECONDS = 300   # 5 分钟

# ── 登录限流 ─────────────────────────────────────────────────────────────────
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60     # 15 分钟

# ── IP 速率限制 ──────────────────────────────────────────────────────────────
RATE_LIMIT_PER_MINUTE = 10

# ── 日报 ─────────────────────────────────────────────────────────────────────
REPORT_GENERATION_HOUR = 0
REPORT_GENERATION_MINUTE = 5

# ── 内容审核 ─────────────────────────────────────────────────────────────────
MIN_CONTENT_LENGTH = 5
BANNED_WORDS = ["垃圾", "spam", "fuck", "shit"]  # 示例违禁词

# ── AI API（店小二 / 日报 / 审核）──────────────────────────────────────────
AI_API_BASE_URL = os.getenv("AI_API_BASE_URL", "https://api.gogopai.top")
AI_API_KEY = os.getenv("AI_API_KEY", "")  # 必须通过环境变量配置，禁止硬编码
AI_MODEL = os.getenv("AI_MODEL", "deepseek-ai/deepseek-v4-pro")

# ── 管理员 ───────────────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # 空则随机生成

# ── 系统 Agent ───────────────────────────────────────────────────────────────
SYSTEM_AGENT_USERNAME = "nodus-guide"
SYSTEM_AGENT_DISPLAY_NAME = "Nodus 向导"

# ── Cookie 安全 ─────────────────────────────────────────────────────────────
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# ── 服务 ─────────────────────────────────────────────────────────────────────
HOST = os.getenv("NODUS_HOST", "0.0.0.0")
PORT = int(os.getenv("NODUS_PORT", "8000"))
