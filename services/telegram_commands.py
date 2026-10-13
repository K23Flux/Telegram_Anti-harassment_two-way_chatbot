"""同步 Telegram 输入框里的「/」命令菜单（每条命令带中文说明）。

- 所有私聊：只有 /start，普通用户看不到管理命令
- 每个管理员自己的私聊：完整的管理命令
- 管理群：话题里常用的管理命令
- 其他群：只有 /getid（部署时查群 ID 用）
"""

import logging

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
from telegram.error import TelegramError
from telegram.ext import Application

from config import config

logger = logging.getLogger(__name__)

USER_PRIVATE_COMMANDS = [
    BotCommand("start", "开始聊天"),
]

ADMIN_PRIVATE_COMMANDS = [
    BotCommand("help", "管理命令说明"),
    BotCommand("stats", "统计：用户数、用户列表"),
    BotCommand("blacklist", "黑名单（可点按钮解封）"),
    BotCommand("block", "拉黑：/block 用户ID 原因"),
    BotCommand("unblock", "解封：/unblock 用户ID"),
    BotCommand("getid", "查看我的用户 ID"),
    BotCommand("start", "开始聊天"),
]

ADMIN_GROUP_COMMANDS = [
    BotCommand("del", "撤回：回复自己的消息后发送"),
    BotCommand("block", "拉黑当前话题的用户"),
    BotCommand("unblock", "解封当前话题的用户"),
    BotCommand("stats", "统计：用户数、用户列表"),
    BotCommand("blacklist", "黑名单（可点按钮解封）"),
    BotCommand("help", "管理命令说明"),
    BotCommand("getid", "查看群组 ID"),
]

OTHER_GROUP_COMMANDS = [
    BotCommand("getid", "查看群组 ID"),
]


def _has_admin_features() -> bool:
    return bool(config.FORUM_GROUP_ID and config.ADMIN_IDS)


async def _set(bot, commands, scope, label: str) -> None:
    try:
        await bot.set_my_commands(commands, scope=scope)
    except TelegramError as error:
        # 管理员还没私聊过机器人时，按聊天设置菜单会失败，不影响运行
        logger.warning("设置 %s 的命令菜单失败: %s", label, error)


async def register_admin_private_commands(bot, admin_id: int) -> None:
    """给某个管理员的私聊设置完整菜单。管理员第一次 /start 时也会调用一次。"""
    await _set(bot, ADMIN_PRIVATE_COMMANDS, BotCommandScopeChat(admin_id), f"管理员 {admin_id}")


async def register_bot_commands(app: Application) -> None:
    await _set(app.bot, USER_PRIVATE_COMMANDS, BotCommandScopeAllPrivateChats(), "所有私聊")
    await _set(app.bot, OTHER_GROUP_COMMANDS, BotCommandScopeAllGroupChats(), "所有群组")

    if not _has_admin_features():
        return

    await _set(app.bot, ADMIN_GROUP_COMMANDS, BotCommandScopeChat(config.FORUM_GROUP_ID), "管理群")
    for admin_id in config.ADMIN_IDS:
        await register_admin_private_commands(app.bot, admin_id)

    logger.info("已同步 Telegram 命令菜单")
