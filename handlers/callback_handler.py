import asyncio
import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from services.verification import verify_answer
from services.blacklist import (
    block_user,
    build_blacklist_view,
    build_stats_view,
    build_users_view,
    unblock_user,
    verify_unblock_answer,
)
from database import models as db
from services.thread_manager import build_user_info_card_keyboard
from services.media_group_buffer import enqueue_serialized
from .user_handler import _forward_verified_messages

logger = logging.getLogger(__name__)


async def _refresh_usercard_keyboard(query, target_user_id: int):
    keyboard = await build_user_info_card_keyboard(target_user_id)

    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise


def _copy_pending_batches(context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_updates")
    legacy_pending = context.user_data.get("pending_update")
    if not pending:
        return [[legacy_pending]] if legacy_pending else []
    if isinstance(pending[0], (list, tuple)):
        batches = [list(batch) for batch in pending]
    else:
        batches = [list(pending)]
    if legacy_pending:
        batches.insert(0, [legacy_pending])
    return batches


async def _forward_pending_batches(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
) -> bool:
    batches = _copy_pending_batches(context)
    if not batches:
        return False

    context.user_data.pop("pending_update", None)
    context.user_data["pending_updates"] = batches
    while batches:
        active_batches = batches
        outcome = await _forward_verified_messages(
            batches[0],
            context,
            user_id,
        )
        if outcome is False:
            if context.user_data.get("pending_updates") is not active_batches:
                replacement = _copy_pending_batches(context)
                context.user_data["pending_updates"] = replacement + batches[1:]
            return False
        batches = batches[1:]
        if batches:
            context.user_data["pending_updates"] = batches
        else:
            context.user_data.pop("pending_updates", None)
    return True


async def _process_verification_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    challenge_id: str,
    option_index: int,
):
    result = await verify_answer(user_id, challenge_id, option_index)
    success, message, is_banned, new_question = result[:4]
    ignored = len(result) > 4 and result[4]

    if ignored:
        return

    if is_banned:
        await query.edit_message_text(text=message, reply_markup=None)
        return

    if new_question:
        new_question_text, new_keyboard = new_question
        await query.edit_message_text(
            text=f"{message}\n\n{new_question_text}",
            reply_markup=new_keyboard,
        )
        return

    try:
        await query.edit_message_text(text=message)
    except TelegramError:
        logger.exception("无法更新验证结果消息，继续处理待转发消息")
    if success:
        had_pending = bool(_copy_pending_batches(context))
        forwarded = await _forward_pending_batches(context, user_id)
        if not had_pending and not forwarded:
            await query.message.reply_text("现在您可以发送消息了！")
        elif had_pending and not forwarded:
            user_data = await db.get_user(user_id)
            if user_data and user_data.get("is_verified"):
                await query.message.reply_text(
                    "验证已完成，但待发送消息暂时传递失败；消息已保留，"
                    "您下次发送消息时会自动重试。"
                )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """所有内联按钮的入口。每个回调只 answer 一次（重复 answer 会报错，导致后续逻辑不执行）。"""
    query = update.callback_query
    data = query.data or ""
    user_id = query.from_user.id

    # ---- 用户按钮：人机验证 ----
    if data.startswith("verify:"):
        await query.answer()
        try:
            _, challenge_id, option_index_text = data.split(":", 2)
            option_index = int(option_index_text)
        except (TypeError, ValueError):
            return

        async def process_verification():
            try:
                return await _process_verification_callback(
                    query,
                    context,
                    user_id,
                    challenge_id,
                    option_index,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("后台处理用户 %s 的验证回调失败", user_id)
                try:
                    await query.message.reply_text(
                        "验证处理暂时失败，请重新点击当前验证按钮。"
                    )
                except TelegramError:
                    logger.exception("无法通知用户验证回调处理失败")
                return None

        if hasattr(context, "application") and hasattr(context, "bot_data"):
            accepted = enqueue_serialized(
                context,
                user_id,
                process_verification,
            )
            if not accepted:
                await query.message.reply_text(
                    "当前消息队列繁忙，请稍后重新点击验证按钮。"
                )
        else:
            await process_verification()
        return
    
    # ---- 用户按钮：自助解封答题 ----
    if data.startswith("unblock_"):
        await query.answer()
        try:
            option_index = int(data.split("_", 1)[1])
        except ValueError:
            return
        message, _ = await verify_unblock_answer(user_id, option_index)
        await query.edit_message_text(text=message, reply_markup=None)
        return

    # ---- 以下都是管理员按钮 ----
    if not await db.is_admin(user_id):
        await query.answer("只有管理员可以操作。", show_alert=True)
        return

    if data.startswith("usercard_block_"):
        target = _int_suffix(data, 2)
        if target is None:
            await query.answer()
            return
        is_blocked, _ = await db.is_blacklisted(target)
        if is_blocked:
            response = await unblock_user(target)
        else:
            response = await block_user(target, "用户信息卡片快捷封禁", user_id, permanent=True)
        await query.answer(response, show_alert=True)
        await _refresh_usercard_keyboard(query, target)
        return

    if data.startswith("admin_unblock_"):
        parts = data.split("_")
        target = _int_suffix(data, 2)
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        if target is None:
            await query.answer()
            return
        await query.answer(await unblock_user(target), show_alert=True)
        text, keyboard = await build_blacklist_view(page)
        await _edit_html(query, text, keyboard)
        return

    await query.answer()
    if data == "stats_menu":
        text, keyboard = await build_stats_view()
    elif data.startswith("users_page_"):
        text, keyboard = await build_users_view(_int_suffix(data, 2) or 1)
    elif data.startswith("bl_page_"):
        text, keyboard = await build_blacklist_view(_int_suffix(data, 2) or 1)
    else:
        return
    await _edit_html(query, text, keyboard)


def _int_suffix(data: str, index: int):
    try:
        return int(data.split("_")[index])
    except (IndexError, ValueError):
        return None


async def _edit_html(query, text, keyboard):
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "message is not modified" not in exc.message.lower():
            raise
