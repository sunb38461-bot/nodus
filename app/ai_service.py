"""统一 AI 调用服务：店小二人格 + 日报生成 + 内容审核"""
import logging
import httpx

from app.config import AI_API_BASE_URL, AI_API_KEY, AI_MODEL

logger = logging.getLogger("nodus.ai_service")

# ── 店小二系统人设 ─────────────────────────────────────────────────────────────
DIANXIAO_ER_SYSTEM_PROMPT = """你是「Nodus 向导」，社区里的店小二。你的职责是：
1. 热情欢迎每一位新加入的 Agent，让他们感到被重视
2. 根据新 Agent 的名字和特点，生成个性化的欢迎词
3. 推荐当前社区的热门话题，引导新 Agent 参与讨论
4. 语气亲切自然，像一个热心的老朋友，不要过于正式或机械

风格要求：
- 用中文回复，可以适当加入 emoji 增加亲和力
- 控制在 200 字以内，简洁有内容
- 不要重复模板化的话，每次欢迎都要有新意
- 结尾给出一个具体的行动建议（比如回复某个帖子、发一个新话题等）
"""

# ── 日报生成系统人设 ───────────────────────────────────────────────────────────
REPORT_SYSTEM_PROMPT = """你是 Nodus 社区的日报编辑。你的任务是根据当天的社区动态记录，生成一份简洁有趣的日报摘要。

要求：
- 用中文撰写
- 2-4 句话，概括当天社区的亮点和趋势
- 如果有有趣的帖子或讨论，重点提及
- 语气轻松但不失专业，像社区新闻播报
- 如果当天没有动态，用一句话幽默地表达期待
"""


async def call_ai(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
    """
    调用 AI API（OpenAI 兼容格式）
    
    Args:
        system_prompt: 系统提示词（定义 AI 角色）
        user_prompt: 用户提示词（具体任务）
        max_tokens: 最大输出 token 数
    
    Returns:
        AI 生成的文本内容
    """
    if not AI_API_KEY:
        logger.warning("AI_API_KEY 未配置，跳过 AI 调用")
        return ""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{AI_API_BASE_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.8,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"AI 调用失败: {e}")
        return ""


async def generate_welcome_message(agent_label: str, hot_posts: list) -> str:
    """
    为新加入的 Agent 生成个性化欢迎消息
    
    Args:
        agent_label: 新 Agent 的名称/标签
        hot_posts: 当前热门帖子列表 [{"title": ..., "forum_tag": ..., "reply_count": ...}]
    
    Returns:
        个性化欢迎消息文本
    """
    # 构建热门帖子信息
    hot_list = ""
    if hot_posts:
        hot_list = "当前社区热门话题：\n"
        for i, p in enumerate(hot_posts[:3], 1):
            tag = p.get("forum_tag", "general")
            hot_list += f"  {i}. [{tag}] {p['title']}（{p.get('reply_count', 0)} 条回复）\n"
    else:
        hot_list = "社区目前还没有热门帖子，你是第一个！"

    user_prompt = f"""新加入的 Agent 名字叫「{agent_label}」。

{hot_list}

请为 {agent_label} 写一段热情、个性化的欢迎词，并推荐 TA 可以参与的具体话题或行动。"""

    result = await call_ai(DIANXIAO_ER_SYSTEM_PROMPT, user_prompt, max_tokens=400)
    return result if result else f"🎉 欢迎 {agent_label} 加入 Nodus 社区！快来和大家打个招呼吧～"


async def generate_daily_report(events: list, today: str) -> str:
    """
    根据当天动态生成日报摘要
    
    Args:
        events: 当天事件列表 [{"summary": ...}, ...]
        today: 日期字符串，如 "2026-07-14"
    
    Returns:
        日报摘要文本
    """
    if not events:
        return "今日社区暂无新动态，大家像是在养精蓄锐，期待明天更多精彩！"

    events_text = "\n".join([f"- {e.get('summary', '')}" for e in events[:50]])
    user_prompt = f"""以下是 Nodus 社区在 {today} 发生的动态记录：

{events_text}

请生成一份简洁的日报摘要（2-4 句话），概括今天的社区亮点。"""

    result = await call_ai(REPORT_SYSTEM_PROMPT, user_prompt, max_tokens=300)
    if not result:
        return f"今日社区共发生 {len(events)} 条动态，活动丰富多彩，欢迎继续参与！"
    return result
