"""秘境游戏 REST 路由（供前端调用）"""
import json
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from typing import Optional
from pydantic import BaseModel

from app.auth_web import require_user
from app.db import get_db
from app.mcp_tools import (
    GAME_AREAS, GAME_MONSTERS, GAME_EQUIPMENT, GAME_CONSUMABLES,
    GAME_ACHIEVEMENTS, GAME_TITLES,
    _get_game_title, _exp_to_next_level, _class_bonus,
    _get_game_character, _add_game_event, _add_inventory_item,
    _check_achievements, _do_explore_event,
)

router = APIRouter(prefix="/api/game", tags=["game"])

# 导入游戏引擎数据（从 mcp_tools 复用）
GAME_AREAS = {
    "data_wasteland": {"name": "数据荒原", "min_level": 1, "desc": "新手区，散落着废弃的数据碎片"},
    "dark_forest":    {"name": "幽暗森林", "min_level": 5, "desc": "茂密的数据丛林，藏着草药与精灵"},
    "forgotten_mines":{"name": "遗忘矿洞", "min_level": 10, "desc": "古老的矿脉中闪烁着矿石光芒"},
    "cyber_market":   {"name": "赛博集市", "min_level": 15, "desc": "繁华的交易中心，商人与冒险者汇聚"},
    "abyss_rift":     {"name": "深渊裂隙", "min_level": 25, "desc": "危险的裂隙深处，潜伏着强大怪物"},
    "core_sanctum":   {"name": "核心圣殿", "min_level": 40, "desc": "秘境的核心，传说级存在守护着真相"},
}

GAME_TITLES_LOCAL = [(1, "冒险者"), (10, "探索者"), (20, "勇者"), (35, "英雄"), (50, "传说")]

def _get_title_local(level: int) -> str:
    title = "冒险者"
    for threshold, t in GAME_TITLES_LOCAL:
        if level >= threshold:
            title = t
    return title

class CreateCharRequest(BaseModel):
    name: str
    char_class: str = "warrior"


@router.post("/create")
async def create_character(req: CreateCharRequest, user: dict = Depends(require_user)):
    """创建游戏角色"""
    if req.char_class not in ("warrior", "mage", "ranger"):
        return {"ok": False, "error": {"message": "职业无效"}}

    db = await get_db()
    try:
        existing = await _get_game_character(db, user["id"])
        if existing:
            return {"ok": False, "error": {"message": "你已有角色"}}

        hp, max_hp, mp, max_mp = 100, 100, 50, 50
        str_val, wis_val, cha_val, luck_val = 5, 5, 5, 3
        bonus = _class_bonus(req.char_class)
        hp += bonus.get("hp", 0); max_hp += bonus.get("max_hp", 0)
        mp += bonus.get("mp", 0); max_mp += bonus.get("max_mp", 0)
        str_val += bonus.get("str", 0); wis_val += bonus.get("wis", 0)
        cha_val += bonus.get("cha", 0); luck_val += bonus.get("luck", 0)

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO game_characters (user_id, name, class, level, exp, hp, max_hp, mp, max_mp, str, wis, cha, luck, gold, current_area, status, created_at) "
            "VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, 100, 'data_wasteland', 'active', ?)",
            (user["id"], req.name, req.char_class, hp, max_hp, mp, max_mp, str_val, wis_val, cha_val, luck_val, now)
        )
        await db.commit()

        # 透传到社区频道
        try:
            from app.system_agent import write_event
            class_names = {"warrior": "战士", "mage": "法师", "ranger": "游侠"}
            await write_event(
                user["id"], req.name, "game_character_created",
                f"{req.name} 踏入秘境，以「{class_names.get(req.char_class, req.char_class)}」身份开启了冒险 ⚔️"
            )
        except Exception:
            pass

        return {"ok": True, "data": {"name": req.name, "class": req.char_class}}
    finally:
        await db.close()


@router.post("/explore")
async def do_explore(user: dict = Depends(require_user)):
    """探索当前区域"""
    db = await get_db()
    try:
        char = await _get_game_character(db, user["id"])
        if not char:
            return {"ok": False, "error": {"message": "请先创建角色"}}
        if char["hp"] <= 0:
            return {"ok": False, "error": {"message": "HP 为 0，请先休息"}}

        area_key = char["current_area"]
        event = await _do_explore_event(char, area_key)
        event_type = event["event_type"]
        result = event["result"]

        exp_gain = gold_gain = 0
        new_hp = char["hp"]
        notable_drops = []  # 稀有+掉落，用于频道透传

        if event_type == "combat":
            if result["won"]:
                exp_gain = result.get("exp", 0)
                gold_gain = result.get("gold", 0)
                new_hp = result["hp_remaining"]
                for drop in result.get("drops", []):
                    item_type = "equipment" if drop["key"] in GAME_EQUIPMENT else "material"
                    await _add_inventory_item(db, char["id"], item_type, drop["key"], drop["name"], 1, drop["rarity"])
                    if drop.get("rarity") in ("rare", "epic", "legendary"):
                        notable_drops.append(drop)
            else:
                new_hp = 0
        elif event_type == "treasure":
            gold_gain = result.get("gold", 0)
        elif event_type == "npc":
            if "gift" in result:
                gift = result["gift"]
                await _add_inventory_item(db, char["id"], gift["type"], gift["key"], gift["name"])
        elif event_type == "gather":
            await _add_inventory_item(db, char["id"], "material", result["item_key"], result["item_name"], result["quantity"])
        elif event_type == "trap":
            new_hp = max(0, char["hp"] - result["damage"])

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
        await _add_game_event(db, char["id"], event_type, area_key, event["summary"], result)

        char["hp"] = new_hp; char["gold"] = new_gold; char["level"] = new_level
        new_achievements = await _check_achievements(db, char)

        # 里程碑透传到社区频道
        try:
            from app.system_agent import write_event
            area_name = GAME_AREAS.get(area_key, {}).get("name", area_key)
            if leveled_up:
                await write_event(
                    user["id"], char["name"], "game_level_up",
                    f"{char['name']} 在{area_name}历练升到了 {new_level} 级，获得称号「{_get_game_title(new_level)}」 🎉"
                )
            for ach_key in new_achievements:
                await write_event(
                    user["id"], char["name"], "game_achievement",
                    f"{char['name']} 解锁了秘境成就「{GAME_ACHIEVEMENTS[ach_key]['name']}」 🏆"
                )
            rarity_labels = {"rare": "稀有", "epic": "史诗", "legendary": "传说"}
            for drop in notable_drops:
                rl = rarity_labels.get(drop.get("rarity"), "稀有")
                await write_event(
                    user["id"], char["name"], "game_drop",
                    f"{char['name']} 在{area_name}获得了{rl}战利品「{drop['name']}」 ✨"
                )
        except Exception:
            pass

        resp = {"ok": True, "data": {"summary": event["summary"], "leveled_up": leveled_up}}
        if leveled_up:
            resp["data"]["level_up_message"] = f"恭喜！你升到了 {new_level} 级！"
        if new_achievements:
            resp["data"]["new_achievements"] = new_achievements
        return resp
    finally:
        await db.close()


@router.post("/rest")
async def do_rest(user: dict = Depends(require_user)):
    """休息恢复 HP/MP"""
    db = await get_db()
    try:
        char = await _get_game_character(db, user["id"])
        if not char:
            return {"ok": False, "error": {"message": "请先创建角色"}}

        hp_recover = char["max_hp"] // 2
        mp_recover = char["max_mp"] // 2
        new_hp = min(char["max_hp"], char["hp"] + hp_recover)
        new_mp = min(char["max_mp"], char["mp"] + mp_recover)

        await db.execute("UPDATE game_characters SET hp = ?, mp = ? WHERE id = ?", (new_hp, new_mp, char["id"]))
        await db.commit()
        return {"ok": True, "data": {"hp": new_hp, "mp": new_mp}}
    finally:
        await db.close()



@router.get("/status")
async def get_game_status(user: dict = Depends(require_user)):
    """获取当前用户的游戏角色状态"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        if not char:
            return {"ok": True, "data": {"has_character": False}}
        char = dict(char)

        area = GAME_AREAS.get(char["current_area"], {})
        exp_needed = int(50 * char["level"] * (1 + char["level"] * 0.1))

        return {
            "ok": True,
            "data": {
                "has_character": True,
                "id": char["id"],
                "name": char["name"],
                "class": char["class"],
                "title": _get_game_title(char["level"]),
                "level": char["level"],
                "exp": char["exp"],
                "exp_to_next": exp_needed,
                "hp": char["hp"], "max_hp": char["max_hp"],
                "mp": char["mp"], "max_mp": char["max_mp"],
                "str": char["str"], "wis": char["wis"], "cha": char["cha"], "luck": char["luck"],
                "gold": char["gold"],
                "current_area": char["current_area"],
                "area_name": area.get("name", char["current_area"]),
                "status": char["status"],
                "last_explore_at": char["last_explore_at"],
            }
        }
    finally:
        await db.close()


@router.get("/areas")
async def get_areas(user: dict = Depends(require_user)):
    """获取所有区域信息，标记已解锁状态"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT level FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        my_level = char["level"] if char else 0

        areas = []
        for key, info in GAME_AREAS.items():
            areas.append({
                "key": key,
                "name": info["name"],
                "min_level": info["min_level"],
                "desc": info["desc"],
                "unlocked": my_level >= info["min_level"],
            })
        return {"ok": True, "data": {"areas": areas}}
    finally:
        await db.close()


@router.post("/change-area/{area_key}")
async def change_area(area_key: str, user: dict = Depends(require_user)):
    """切换当前区域"""
    if area_key not in GAME_AREAS:
        return {"ok": False, "error": {"message": "区域不存在"}}

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        if not char:
            return {"ok": False, "error": {"message": "请先创建角色"}}
        char = dict(char)

        area = GAME_AREAS[area_key]
        if char["level"] < area["min_level"]:
            return {"ok": False, "error": {"message": f"等级不足，需要 {area['min_level']} 级"}}

        await db.execute("UPDATE game_characters SET current_area = ? WHERE id = ?", (area_key, char["id"]))
        await db.commit()

        return {"ok": True, "data": {"current_area": area_key, "area_name": area["name"]}}
    finally:
        await db.close()


@router.get("/events")
async def get_game_events(
    user: dict = Depends(require_user),
    limit: int = Query(20, ge=1, le=50),
):
    """获取最近的探索日志"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        if not char:
            return {"ok": True, "data": {"events": []}}

        cursor = await db.execute(
            "SELECT * FROM game_events WHERE character_id = ? ORDER BY created_at DESC LIMIT ?",
            (char["id"], limit)
        )
        events = [dict(row) for row in await cursor.fetchall()]

        # 解析 result JSON
        for e in events:
            if e.get("result"):
                try:
                    e["result"] = json.loads(e["result"])
                except:
                    pass

        return {"ok": True, "data": {"events": events}}
    finally:
        await db.close()


@router.get("/inventory")
async def get_inventory(user: dict = Depends(require_user)):
    """获取背包物品"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        if not char:
            return {"ok": True, "data": {"items": []}}

        cursor = await db.execute(
            "SELECT * FROM game_inventory WHERE character_id = ? ORDER BY rarity, item_name",
            (char["id"],)
        )
        items = [dict(row) for row in await cursor.fetchall()]

        rarity_names = {"common": "普通", "rare": "稀有", "epic": "史诗", "legendary": "传说"}
        result = []
        for item in items:
            result.append({
                "id": item["id"],
                "name": item["item_name"],
                "type": item["item_type"],
                "quantity": item["quantity"],
                "rarity": rarity_names.get(item["rarity"], item["rarity"]),
                "rarity_key": item["rarity"],
            })

        return {"ok": True, "data": {"items": result}}
    finally:
        await db.close()


@router.get("/achievements")
async def get_achievements(user: dict = Depends(require_user)):
    """获取成就列表"""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM game_characters WHERE user_id = ?", (user["id"],))
        char = await cursor.fetchone()
        if not char:
            return {"ok": True, "data": {"achievements": [], "total": len(GAME_ACHIEVEMENTS)}}

        cursor = await db.execute(
            "SELECT achievement_key, achieved_at FROM game_achievements WHERE character_id = ?",
            (char["id"],)
        )
        unlocked = {row["achievement_key"]: row["achieved_at"] for row in await cursor.fetchall()}

        result = []
        for key, info in GAME_ACHIEVEMENTS.items():
            result.append({
                "key": key,
                "name": info["name"],
                "desc": info["desc"],
                "unlocked": key in unlocked,
                "unlocked_at": unlocked.get(key),
            })

        return {"ok": True, "data": {"achievements": result, "unlocked_count": len(unlocked), "total": len(GAME_ACHIEVEMENTS)}}
    finally:
        await db.close()


@router.get("/leaderboard")
async def get_leaderboard(user: dict = Depends(require_user)):
    """获取排行榜"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT gc.*, u.display_name, u.username FROM game_characters gc "
            "JOIN users u ON u.id = gc.user_id "
            "ORDER BY gc.level DESC, gc.exp DESC LIMIT 20"
        )
        top = [dict(row) for row in await cursor.fetchall()]

        leaderboard = []
        for i, row in enumerate(top, 1):
            area_name = GAME_AREAS.get(row["current_area"], {}).get("name", row["current_area"])
            leaderboard.append({
                "rank": i,
                "name": row["name"],
                "level": row["level"],
                "title": _get_game_title(row["level"]),
                "class": row["class"],
                "area": area_name,
                "gold": row["gold"],
            })

        # 我的排名
        cursor = await db.execute("SELECT * FROM game_characters WHERE user_id = ?", (user["id"],))
        my_char = await cursor.fetchone()
        my_rank = None
        if my_char:
            cursor = await db.execute(
                "SELECT COUNT(*) + 1 as rank FROM game_characters WHERE level > ? OR (level = ? AND exp > ?)",
                (my_char["level"], my_char["level"], my_char["exp"])
            )
            my_rank = (await cursor.fetchone())["rank"]

        return {"ok": True, "data": {"leaderboard": leaderboard, "my_rank": my_rank}}
    finally:
        await db.close()
