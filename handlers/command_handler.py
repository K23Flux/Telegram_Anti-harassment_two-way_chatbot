"""斜杠命令：/start、/help、/getid、/block、/unblock、/blacklist、/stats。

/del（撤回消息）在 admin_handler.py 里。
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import models as db
from services.blacklist import (
    block_user,
    build_blacklist_view,
    build_stats_view,
    unblock_user,
)
from services.telegram_commands import register_admin_private_commands
from utils.decorators import admin_only

HELP_TEXT = """<b>管理员命令</b>

<b>在用户的话题里使用</b>
/block [原因] — 永久拉黑这个话题对应的用户
/unblock — 解封这个话题对应的用户
/del — 回复你发出的某条消息再发送，把对方那边的这条消息也删掉（48 小时内有效）

<b>在任何地方使用</b>
/block &lt;用户ID&gt; [原因] — 按 ID 永久拉黑
/unblock &lt;用户ID&gt; — 按 ID 解封
/blacklist — 查看黑名单，可直接点按钮解封
/stats — 用户总数、黑名单人数，可翻看所有用户
/getid — 查看当前聊天和你自己的 ID
/help — 显示这条帮助

<b>日常使用</b>
在话题里直接发消息就是回复对方，文字、图片、视频、相册、文件、语音等都可以；编辑自己发出的消息会同步给对方。发送失败会在话题里提示原因。"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start：新用户的欢迎语；管理员额外提示 /help。"""
    user = update.effective_user
    if not await db.get_user(user.id):
        await db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )

    text = (
        f"你好，{user.first_name}！\n\n"
        "直接在这里发消息就可以，我会尽快回复你。"
    )
    if await db.is_admin(user.id):
        # 机器人启动时管理员可能还没私聊过它，那时设置菜单会失败，这里补一次
        await register_admin_private_commands(context.bot, user.id)
        text += "\n\n（你是管理员，发送 /help 查看管理命令。）"
    await update.message.reply_text(text)


@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help：管理员命令说明，普通用户看不到。"""
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/getid：私聊显示自己的 ID；群里同时显示群 ID（部署时填 FORUM_GROUP_ID 用）。"""
    user_id = update.effective_user.id
    if update.effective_chat.type == "private":
        text = f"你的用户 ID：<code>{user_id}</code>"
    else:
        text = (
            f"群组 ID：<code>{update.effective_chat.id}</code>\n"
            f"你的用户 ID：<code>{user_id}</code>"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _target_from_topic(update: Update):
    """在用户话题里发命令时，返回该话题对应的用户 ID；不在话题里返回 None。"""
    message = update.message
    if not message.is_topic_message:
        return None
    user = await db.get_user_by_thread_id(message.message_thread_id)
    return user["user_id"] if user else None


@admin_only
async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/block：话题里直接拉黑该用户，或 /block <用户ID> [原因]。"""
    args = list(context.args or [])
    target = await _target_from_topic(update)

    if target is None:
        if not args or not args[0].lstrip("-").isdigit():
            await update.message.reply_text(
                "用法：在用户话题里发送 /block [原因]，或 /block <用户ID> [原因]"
            )
            return
        target = int(args.pop(0))

    reason = " ".join(args) or "无"
    response = await block_user(target, reason, update.effective_user.id, permanent=True)
    await update.message.reply_text(response)


@admin_only
async def unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unblock：话题里直接解封该用户，或 /unblock <用户ID>。"""
    args = list(context.args or [])
    if args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        target = await _target_from_topic(update)

    if target is None:
        await update.message.reply_text("用法：在用户话题里发送 /unblock，或 /unblock <用户ID>")
        return

    is_blocked, _ = await db.is_blacklisted(target)
    if not is_blocked:
        await update.message.reply_text(f"用户 {target} 不在黑名单里。")
        return
    await update.message.reply_text(await unblock_user(target))


@admin_only
async def blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/blacklist：分页查看黑名单，每人后面有解封按钮。"""
    text, keyboard = await build_blacklist_view(page=1)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats：用户数和黑名单人数，可翻看用户列表和黑名单。"""
    text, keyboard = await build_stats_view()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
