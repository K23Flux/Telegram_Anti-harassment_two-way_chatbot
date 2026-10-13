"""机器人运行出错时，在管理群的 General 话题里发一条简短提示。

- 网络抖动、超时、频率限制这类会自己恢复的错误只写日志，不打扰
- 同一类错误 10 分钟内只提醒一次，避免刷屏
"""

import html
import logging
import time

from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from config import config

logger = logging.getLogger(__name__)

NOTIFY_INTERVAL_SECONDS = 600
_last_notified: dict[str, float] = {}


def _is_transient(error: Exception) -> bool:
    if isinstance(error, (TimedOut, RetryAfter)):
        return True
    # BadRequest 继承自 NetworkError，但它是真正的请求错误，需要提醒
    return isinstance(error, NetworkError) and not isinstance(error, BadRequest)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("处理更新时出错", exc_info=error)

    if error is None or _is_transient(error) or not config.FORUM_GROUP_ID:
        return

    key = f"{type(error).__name__}:{error}"[:200]
    now = time.monotonic()
    if now - _last_notified.get(key, -NOTIFY_INTERVAL_SECONDS) < NOTIFY_INTERVAL_SECONDS:
        return
    _last_notified[key] = now

    detail = html.escape(str(error))[:500] or "（无详细信息）"
    text = (
        "⚠️ <b>机器人运行出错</b>\n\n"
        f"类型：<code>{html.escape(type(error).__name__)}</code>\n"
        f"信息：{detail}\n\n"
        "同类错误 10 分钟内不再重复提醒，完整信息见容器日志。"
    )
    try:
        await context.bot.send_message(
            chat_id=config.FORUM_GROUP_ID,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("发送出错提醒失败")
