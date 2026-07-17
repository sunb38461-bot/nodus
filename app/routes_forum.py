"""论坛相关 REST 路由"""
import html
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from typing import Optional

from app.auth_web import require_user
from app.db import get_db

router = APIRouter(prefix="/api/forum", tags=["forum"])


@router.get("/posts")
async def list_posts(
    user: dict = Depends(require_user),
    forum_tag: Optional[str] = None,
    sort_by: str = Query("latest", pattern="^(latest|replies)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    db = await get_db()
    try:
        where = ""
        params = []
        if forum_tag:
            where = "WHERE p.forum_tag = ?"
            params.append(forum_tag)

        order = "p.created_at DESC" if sort_by == "latest" else "reply_count DESC"
        query = f"""
            SELECT p.*, COUNT(r.id) as reply_count
            FROM posts p LEFT JOIN replies r ON r.post_id = p.id
            {where}
            GROUP BY p.id
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        cursor = await db.execute(query, params)
        posts = [dict(row) for row in await cursor.fetchall()]
        return {"ok": True, "data": {"posts": posts, "limit": limit, "offset": offset}}
    finally:
        await db.close()


@router.get("/posts/{post_id}")
async def get_post(post_id: int, user: dict = Depends(require_user)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
        post = await cursor.fetchone()
        if not post:
            return {"ok": False, "error": {"code": "POST_NOT_FOUND", "message": "帖子不存在"}}

        cursor = await db.execute(
            "SELECT * FROM replies WHERE post_id = ? ORDER BY created_at ASC", (post_id,)
        )
        replies = [dict(r) for r in await cursor.fetchall()]
        return {"ok": True, "data": {"post": dict(post), "replies": replies}}
    finally:
        await db.close()


@router.get("/my-stats")
async def my_stats(user: dict = Depends(require_user)):
    """当前用户的发帖数、回复数统计"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE author_id = ?", (user["id"],)
        )
        post_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM replies WHERE author_id = ?", (user["id"],)
        )
        reply_count = (await cursor.fetchone())["cnt"]

        return {"ok": True, "data": {"post_count": post_count, "reply_count": reply_count}}
    finally:
        await db.close()


@router.get("/search")
async def search_posts(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_user),
):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM posts WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit)
        )
        posts = [dict(r) for r in await cursor.fetchall()]
        return {"ok": True, "data": {"posts": posts, "keyword": keyword}}
    finally:
        await db.close()


@router.post("/posts/{post_id}/promote")
async def promote_post(post_id: int, user: dict = Depends(require_user)):
    """将帖子沉淀到知识库"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
        post = await cursor.fetchone()
        if not post:
            return {"ok": False, "error": {"code": "POST_NOT_FOUND", "message": "帖子不存在"}}
        post = dict(post)

        now = datetime.now(timezone.utc).isoformat()
        kb_content = f"[原文由 {post['author_name']} 发布于论坛]\n\n{post['content']}"

        cursor = await db.execute(
            "INSERT INTO knowledge_items (title, content, category, author_id, author_name, created_at) "
            "VALUES (?, ?, 'wiki', ?, ?, ?)",
            (post["title"], kb_content, user["id"], user.get("display_name", user["username"]), now)
        )
        item_id = cursor.lastrowid
        await db.commit()

        return {"ok": True, "data": {"item_id": item_id, "title": post["title"], "source_post_id": post_id}}
    finally:
        await db.close()


@router.get("/subscriptions")
async def get_subscriptions(user: dict = Depends(require_user)):
    """获取当前用户的话题订阅列表"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT tag, created_at FROM tag_subscriptions WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],)
        )
        subs = [dict(r) for r in await cursor.fetchall()]
        return {"ok": True, "data": {"subscriptions": subs, "count": len(subs)}}
    finally:
        await db.close()


@router.post("/subscriptions/{tag}")
async def subscribe_tag_api(tag: str, user: dict = Depends(require_user)):
    """订阅一个话题标签"""
    valid_tags = {"tech", "diary", "relation", "night", "fun", "general"}
    tag = tag.strip().lower()
    if tag not in valid_tags:
        return {"ok": False, "error": {"message": f"无效标签 '{tag}'"}}

    db = await get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        try:
            await db.execute(
                "INSERT INTO tag_subscriptions (user_id, tag, created_at) VALUES (?, ?, ?)",
                (user["id"], tag, now)
            )
            await db.commit()
            return {"ok": True, "data": {"tag": tag, "message": f"已订阅 #{tag}"}}
        except Exception:
            return {"ok": False, "error": {"message": f"你已经订阅了 #{tag}"}}
    finally:
        await db.close()


@router.delete("/subscriptions/{tag}")
async def unsubscribe_tag_api(tag: str, user: dict = Depends(require_user)):
    """取消订阅一个话题标签"""
    tag = tag.strip().lower()
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM tag_subscriptions WHERE user_id = ? AND tag = ?",
            (user["id"], tag)
        )
        await db.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "error": {"message": f"你没有订阅 #{tag}"}}
        return {"ok": True, "data": {"tag": tag, "message": f"已取消订阅 #{tag}"}}
    finally:
        await db.close()
