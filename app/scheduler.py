"""APScheduler 定时任务：每日日报生成 + 秘境挂机探索"""
import json
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import REPORT_GENERATION_HOUR, REPORT_GENERATION_MINUTE
from app.db import get_db
from app.ai_service import generate_daily_report as ai_generate_report

logger = logging.getLogger("nodus.scheduler")

scheduler = AsyncIOScheduler()


async def cleanup_expired_sessions():
    """清理过期的 web session，避免数据无限累积"""
    now = datetime.now(timezone.utc).isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM web_sessions WHERE expires_at < ?", (now,)
        )
        deleted = cursor.rowcount
        await db.commit()
        if deleted > 0:
            logger.info(f"已清理 {deleted} 条过期 session")
    finally:
        await db.close()


async def generate_daily_report():
    """每日日报生成任务"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = await get_db()
    try:
        # 检查今天是否已生成
        cursor = await db.execute("SELECT id FROM reports WHERE report_date = ?", (today,))
        if await cursor.fetchone():
            logger.info(f"日报已存在: {today}，跳过")
            return

        # 查询当天 events
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await db.execute(
            "SELECT * FROM events WHERE created_at > ? ORDER BY created_at ASC",
            (today_start,)
        )
        events = [dict(r) for r in await cursor.fetchall()]

        # 调用 AI 生成日报摘要
        summary = await ai_generate_report(events, today)

        # 统计快照
        stats = {
            "total_events": len(events),
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO reports (report_date, summary, stats_snapshot, generated_at) VALUES (?, ?, ?, ?)",
            (today, summary, json.dumps(stats, ensure_ascii=False), now)
        )
        await db.commit()
        logger.info(f"日报生成成功: {today}")

        # 写入 events 流水
        from app.system_agent import ensure_system_agent, write_event
        system_id = await ensure_system_agent()
        await write_event(
            system_id, "Nodus 向导", "report_generated",
            f"{today} 的日报已生成，来看看今天社区都发生了什么吧～"
        )
    finally:
        await db.close()


def start_scheduler():
    """启动定时任务"""
    scheduler.add_job(
        generate_daily_report,
        "cron",
        hour=REPORT_GENERATION_HOUR,
        minute=REPORT_GENERATION_MINUTE,
        id="daily_report",
        replace_existing=True
    )
    # 每天凌晨 3:00 清理过期 session
    scheduler.add_job(
        cleanup_expired_sessions,
        "cron",
        hour=3,
        minute=0,
        id="cleanup_sessions",
        replace_existing=True
    )
    # 每 30 分钟秘境挂机探索
    scheduler.add_job(
        idle_game_explore,
        "interval",
        minutes=30,
        id="idle_game_explore",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"定时任务已启动，日报生成时间: {REPORT_GENERATION_HOUR:02d}:{REPORT_GENERATION_MINUTE:02d}")


async def idle_game_explore():
    """秘境挂机探索：每 30 分钟为所有活跃角色自动探索一次（收益减半）"""
    from app.mcp_tools import (
        GAME_AREAS, GAME_EQUIPMENT, GAME_MONSTERS,
        _get_game_character, _add_game_event, _add_inventory_item,
        _check_achievements, _do_explore_event, _exp_to_next_level,
    )

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM game_characters WHERE status = 'active' AND hp > 0"
        )
        characters = [dict(row) for row in await cursor.fetchall()]

        if not characters:
            return

        for char in characters:
            area_key = char["current_area"]
            event = await _do_explore_event(char, area_key)
            event_type = event["event_type"]
            result = event["result"]

            exp_gain = gold_gain = 0
            new_hp = char["hp"]

            if event_type == "combat":
                if result["won"]:
                    exp_gain = result.get("exp", 0) // 2  # 挂机收益减半
                    gold_gain = result.get("gold", 0) // 2
                    new_hp = result["hp_remaining"]
                    for drop in result.get("drops", []):
                        item_type = "equipment" if drop["key"] in GAME_EQUIPMENT else "material"
                        await _add_inventory_item(db, char["id"], item_type, drop["key"], drop["name"], 1, drop["rarity"])
                else:
                    new_hp = max(1, char["hp"] // 2)  # 挂机失败只扣半血
            elif event_type == "treasure":
                gold_gain = result.get("gold", 0) // 2
            elif event_type == "npc":
                if "gift" in result:
                    gift = result["gift"]
                    await _add_inventory_item(db, char["id"], gift["type"], gift["key"], gift["name"])
            elif event_type == "gather":
                qty = max(1, result["quantity"] // 2)
                await _add_inventory_item(db, char["id"], "material", result["item_key"], result["item_name"], qty)
            elif event_type == "trap":
                new_hp = max(1, char["hp"] - result["damage"] // 2)

            new_exp = char["exp"] + exp_gain
            new_gold = char["gold"] + gold_gain
            new_level = char["level"]
            leveled_up = False
            while new_exp >= _exp_to_next_level(new_level) and new_level < 50:
                new_exp -= _exp_to_next_level(new_level)
                new_level += 1
                leveled_up = True

            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE game_characters SET hp = ?, exp = ?, gold = ?, level = ?, last_explore_at = ? WHERE id = ?",
                (new_hp, new_exp, new_gold, new_level, now, char["id"])
            )
            await db.commit()

            summary = f"[挂机] {event['summary']}"
            await _add_game_event(db, char["id"], event_type, area_key, summary, result)

            char["hp"] = new_hp
            char["gold"] = new_gold
            char["level"] = new_level
            await _check_achievements(db, char)

            # 挂机升级透传到社区频道
            if leveled_up:
                try:
                    from app.system_agent import write_event
                    from app.mcp_tools import _get_game_title
                    area_name = GAME_AREAS.get(area_key, {}).get("name", area_key)
                    await write_event(
                        char["user_id"], char["name"], "game_level_up",
                        f"{char['name']} 在{area_name}挂机历练升到了 {new_level} 级，获得称号「{_get_game_title(new_level)}」 🎉"
                    )
                except Exception:
                    pass

        await db.commit()
        logger.info(f"秘境挂机探索完成，处理了 {len(characters)} 个角色")
    except Exception as e:
        logger.warning(f"秘境挂机探索失败: {e}")
    finally:
        await db.close()


def stop_scheduler():
    """停止定时任务"""
    if scheduler.running:
        scheduler.shutdown()
