"""管理群这一侧：管理员在话题里回复、编辑、撤回消息。"""

import logging

from telegram import ReactionTypeEmoji, ReplyParameters, Update
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes
from database import models as db
from services.media_group_buffer import enqueue_message, replace_buffered_update
from services.message_relay import relay_media_group, relay_message, sync_edited_message

logger = logging.getLogger(__name__)

DELIVERED_REACTION = "👍"

async def _send_reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    return await relay_message(
        bot=context.bot,
        message=update.effective_message,
        destination_chat_id=user_id,
        user_id=user_id,
        source_side="admin",
    )

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.is_topic_message:
        return

    if not update.effective_user or not await db.is_admin(update.effective_user.id):
        return

    thread_id = update.message.message_thread_id
    user = await db.get_user_by_thread_id(thread_id)
    if not user:
        return

    accepted = await enqueue_message(
        update,
        context,
        scope="admin",
        callback=_handle_admin_messages,
        conversation_id=user["user_id"],
    )
    if not accepted:
        await update.message.reply_text(
            "当前消息队列繁忙，这条消息尚未传递，请稍后重新发送。"
        )


async def _handle_admin_messages(updates, context: ContextTypes.DEFAULT_TYPE):
    """把管理员在话题里发的消息（或一组相册）转给用户；成功点 👍，失败在话题里说明原因。"""
    messages = [update.effective_message for update in updates]
    first_message = messages[0]
    user = await db.get_user_by_thread_id(first_message.message_thread_id)
    if not user:
        return

    try:
        if len(updates) == 1:
            await _send_reply_to_user(updates[0], context, user["user_id"])
        else:
            await relay_media_group(
                bot=context.bot,
                messages=messages,
                destination_chat_id=user["user_id"],
                user_id=user["user_id"],
                source_side="admin",
            )
    except Exception as error:
        logger.warning("回复用户 %s 失败: %s", user["user_id"], error)
        await _notify_in_topic(context, first_message, _describe_send_error(error))
        return

    await _mark_delivered(context, first_message)


def _describe_send_error(error: Exception) -> str:
    if isinstance(error, Forbidden):
        return "⚠️ 发送失败：对方已停用或拉黑了机器人，消息没有送达。"
    if isinstance(error, RetryAfter):
        wait = error.retry_after
        seconds = int(wait.total_seconds()) if hasattr(wait, "total_seconds") else int(wait)
        return f"⚠️ 发送失败：触发 Telegram 频率限制，请 {seconds} 秒后重发。"
    if isinstance(error, TelegramError):
        return f"⚠️ 发送失败：{error.message}"
    return "⚠️ 发送失败：机器人内部错误，详情见日志。"


async def _notify_in_topic(context, message, text: str):
    try:
        await context.bot.send_message(
            chat_id=message.chat_id,
            message_thread_id=message.message_thread_id,
            text=text,
            reply_parameters=ReplyParameters(
                message_id=message.message_id,
                allow_sending_without_reply=True,
            ),
        )
    except Exception:
        logger.exception("在话题里发送失败提示时出错")


async def _mark_delivered(context, message):
    """送达标记。群里禁用了这个表情等情况会失败，忽略即可。"""
    try:
        await context.bot.set_message_reaction(
            chat_id=message.chat_id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(DELIVERED_REACTION)],
        )
    except Exception as error:
        logger.debug("设置送达标记失败: %s", error)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/del：回复自己发给用户的某条消息再发送，删除用户那边的这条消息。"""
    message = update.message
    if not message or not message.is_topic_message:
        return
    if not update.effective_user or not await db.is_admin(update.effective_user.id):
        return

    target = message.reply_to_message
    # 话题里不回复任何消息时，reply_to_message 是话题的创建消息
    if not target or target.message_id == message.message_thread_id:
        await message.reply_text("请先回复你要撤回的那条消息，再发送 /del。")
        return

    mapping = await db.get_message_mapping_by_admin_endpoint(message.chat_id, target.message_id)
    if not mapping:
        await message.reply_text("这条消息没有对应的用户侧消息，无法撤回。")
        return
    if mapping["origin_side"] != "admin":
        await message.reply_text("只能撤回你自己发出的消息。")
        return

    try:
        await context.bot.delete_message(
            chat_id=mapping["user_chat_id"],
            message_id=mapping["user_message_id"],
        )
    except TelegramError as error:
        await message.reply_text(
            f"撤回失败：{error.message}\n（超过 48 小时的消息，或对方已经删掉的消息无法撤回。）"
        )
        return

    # 对方那边已删除；把群里的原消息和这条 /del 也清掉，删不掉就留一句说明
    for message_id in (target.message_id, message.message_id):
        try:
            await context.bot.delete_message(chat_id=message.chat_id, message_id=message_id)
        except TelegramError:
            await message.reply_text("已撤回：对方那边的消息已删除。")
            break


async def handle_admin_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_message
    if not message or not message.is_topic_message:
        return

    if not update.effective_user or not await db.is_admin(update.effective_user.id):
        return

    user = await db.get_user_by_thread_id(message.message_thread_id)
    if not user:
        return

    if replace_buffered_update(
        update,
        context,
        scope="admin",
        conversation_id=user["user_id"],
    ):
        return

    accepted = await enqueue_message(
        update,
        context,
        scope="admin-edit",
        callback=_handle_admin_edits,
        conversation_id=user["user_id"],
    )
    if not accepted:
        await context.bot.send_message(
            chat_id=message.chat_id,
            message_thread_id=message.message_thread_id,
            text="当前消息队列繁忙，这次编辑尚未同步，请稍后再次编辑。",
            reply_parameters=ReplyParameters(
                message_id=message.message_id,
                allow_sending_without_reply=True,
            ),
        )


async def _handle_admin_edits(updates, context: ContextTypes.DEFAULT_TYPE):
    message = updates[-1].edited_message
    if not message:
        return

    user = await db.get_user_by_thread_id(message.message_thread_id)
    if not user:
        return

    synced = await sync_edited_message(
        bot=context.bot,
        message=message,
        user_id=user["user_id"],
        source_side="admin",
    )
    if not synced:
        await context.bot.send_message(
            chat_id=message.chat_id,
            message_thread_id=message.message_thread_id,
            text="这次编辑未能同步到用户侧，请稍后再次编辑或重新发送。",
            reply_parameters=ReplyParameters(
                message_id=message.message_id,
                allow_sending_without_reply=True,
            ),
        )
