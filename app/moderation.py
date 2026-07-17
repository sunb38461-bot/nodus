"""内容前置审核：本地规则层 + 可选 AI 审核层"""
import logging
import re
from typing import Optional

from app.config import MIN_CONTENT_LENGTH, BANNED_WORDS, AI_API_KEY

logger = logging.getLogger("nodus.moderation")


class ModerationError(Exception):
    """审核未通过时抛出，携带人类可读的引导提示"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def local_moderate(title: str, content: str):
    """
    本地规则层（同步、零延迟）：
    - 正文长度是否达标
    - 是否为纯重复字符
    - 是否命中违禁词
    未通过则抛出 ModerationError
    """
    # 正文长度
    if len(content.strip()) < MIN_CONTENT_LENGTH:
        raise ModerationError(f"内容有点太短啦，试着展开说说你想讨论的问题（至少 {MIN_CONTENT_LENGTH} 个字符）")

    # 纯重复字符检测（如 "test test test" 或 "aaaa"）
    stripped = content.strip()
    if len(set(stripped.replace(" ", "").replace("\n", ""))) == 1:
        raise ModerationError("内容看起来是重复字符，请写点有意义的内容哦～")

    # 重复词组检测（同一词出现 3 次以上）
    words = re.findall(r'\S+', stripped.lower())
    if words:
        from collections import Counter
        counts = Counter(words)
        most_common_word, most_common_count = counts.most_common(1)[0]
        if most_common_count >= 3 and most_common_count / len(words) > 0.6:
            raise ModerationError("内容看起来是重复内容，请写点有意义的内容哦～")

    # 违禁词
    lower_content = (title + " " + content).lower()
    for word in BANNED_WORDS:
        if word.lower() in lower_content:
            raise ModerationError(f"内容包含不适当词汇，请修改后再发布")


# 内容审核系统人设
MODERATION_SYSTEM_PROMPT = """你是一个社区内容审核助手。请判断提供的内容是否包含以下违规情况：
- 骚扰、霸凌或人身攻击
- 垃圾广告或恶意营销
- 明显违反社区规范的内容

只需回答 PASS（通过）或 REJECT:原因（拒绝并说明原因）。
"""


async def ai_moderate(title: str, content: str) -> Optional[str]:
    """
    AI 审核层（可选，异步）：
    调用 AI API 判断内容是否违规。
    返回 None 表示通过，返回字符串表示违规原因。
    如果未配置 API Key，直接返回 None（跳过 AI 审核）。
    """
    if not AI_API_KEY:
        return None

    try:
        from app.ai_service import call_ai
        user_prompt = f"标题：{title}\n内容：{content}"
        result = await call_ai(MODERATION_SYSTEM_PROMPT, user_prompt, max_tokens=200)
        if result and result.startswith("REJECT"):
            return result
        return None
    except Exception as e:
        logger.warning(f"AI 审核调用失败（不影响正常流程）: {e}")
        return None
