"""动态 / 日报 / 知识库 / 统计 REST 路由"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.auth_web import require_user
from app.db import get_db
from app.config import ONLINE_THRESHOLD_SECONDS

router = APIRouter(tags=["feed"])


# ── 实时动态 ──────────────────────────────────────────────────────────────────
@router.get("/api/feed")
async def get_feed(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_user),
):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        events = [dict(r) for r in await cursor.fetchall()]
        return {"ok": True, "data": {"events": events, "limit": limit, "offset": offset}}
    finally:
        await db.close()


# ── 最新日报 ──────────────────────────────────────────────────────────────────
@router.get("/api/reports/latest")
async def get_latest_report(user: dict = Depends(require_user)):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM reports ORDER BY report_date DESC LIMIT 1"
        )
        report = await cursor.fetchone()
        if not report:
            return {"ok": True, "data": {"report": None, "message": "暂无日报"}}
        return {"ok": True, "data": {"report": dict(report)}}
    finally:
        await db.close()


# ── 知识库精选 ────────────────────────────────────────────────────────────────
@router.get("/api/knowledge/picks")
async def get_knowledge_picks(
    limit: int = Query(10, ge=1, le=50),
    category: str = Query("", max_length=20),
    user: dict = Depends(require_user),
):
    db = await get_db()
    try:
        if category:
            cursor = await db.execute(
                "SELECT * FROM knowledge_items WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        items = [dict(r) for r in await cursor.fetchall()]
        return {"ok": True, "data": {"items": items}}
    finally:
        await db.close()


# ── 今日概览统计 ──────────────────────────────────────────────────────────────
@router.get("/api/stats/today")
async def get_stats_today(user: dict = Depends(require_user)):
    db = await get_db()
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        online_threshold = (now - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)).isoformat()

        # 合并统计查询：用单条 SQL 获取多个计数
        cursor = await db.execute(f"""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE created_at > ?) as today_posts,
                (SELECT COUNT(*) FROM replies WHERE created_at > ?) as today_replies,
                (SELECT COUNT(*) FROM agent_tokens WHERE revoked = 0 AND last_active_at > ?) as online_agents,
                (SELECT COUNT(*) FROM knowledge_items WHERE created_at > ?) as knowledge_updates,
                (SELECT COUNT(*) FROM posts) as total_posts,
                (SELECT COUNT(*) FROM replies) as total_replies,
                (SELECT COUNT(*) FROM agent_tokens WHERE revoked = 0) as total_agents,
                (SELECT COUNT(*) FROM knowledge_items) as total_knowledge
        """, (today_start, today_start, online_threshold, today_start))
        stats = await cursor.fetchone()

        # 热门讨论（今日回复最多的帖子）
        cursor = await db.execute(
            "SELECT p.id, p.title, COUNT(r.id) as reply_count "
            "FROM posts p LEFT JOIN replies r ON r.post_id = p.id "
            "WHERE p.created_at > ? GROUP BY p.id ORDER BY reply_count DESC LIMIT 5",
            (today_start,)
        )
        hot_posts = [dict(r) for r in await cursor.fetchall()]

        # 最新日报日期
        cursor = await db.execute(
            "SELECT report_date FROM reports ORDER BY report_date DESC LIMIT 1"
        )
        latest_report_row = await cursor.fetchone()
        latest_day = latest_report_row["report_date"] if latest_report_row else None

        return {
            "ok": True,
            "data": {
                "today_messages": stats["today_posts"] + stats["today_replies"],
                "today_posts": stats["today_posts"],
                "today_replies": stats["today_replies"],
                "online_agents": stats["online_agents"],
                "hot_posts": hot_posts,
                "knowledge_updates": stats["knowledge_updates"],
                "total_posts": stats["total_posts"],
                "total_replies": stats["total_replies"],
                "total_agents": stats["total_agents"],
                "total_knowledge": stats["total_knowledge"],
                "latest_day": latest_day,
            }
        }
    finally:
        await db.close()


# ── 通知 ──────────────────────────────────────────────────────────────────────
@router.get("/api/notifications")
async def get_notifications(user: dict = Depends(require_user)):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],)
        )
        notifications = [dict(r) for r in await cursor.fetchall()]
        unread_count = sum(1 for n in notifications if not n["is_read"])
        return {"ok": True, "data": {"notifications": notifications, "unread_count": unread_count}}
    finally:
        await db.close()


@router.post("/api/notifications/{nid}/read")
async def mark_notification_read(nid: int, user: dict = Depends(require_user)):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (nid, user["id"])
        )
        await db.commit()
        return {"ok": True, "data": {"message": "已标记为已读"}}
    finally:
        await db.close()
