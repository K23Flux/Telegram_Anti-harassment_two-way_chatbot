import asyncio
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from services.verification import verify_answer
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
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("verify:"):
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
    
    if data == "panel_back":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        from .command_handler import build_panel_view
        message, keyboard = await build_panel_view()
        await query.edit_message_text(message, reply_markup=keyboard)

    elif data.startswith("panel_blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[3])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        
        if keyboard:
            keyboard_buttons = list(keyboard.inline_keyboard)
            keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard)
    
    elif data == "panel_stats":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        from services.blacklist import get_all_users_keyboard
        
        page = 1
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="返回主面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("panel_stats_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(
            page=page,
            callback_prefix="panel_stats_all_users_page_",
            back_callback="panel_back",
            back_text="返回主面板"
        )
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    
    elif data.startswith("panel_stats_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        
        if keyboard:
            keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
            for i, row in enumerate(keyboard_buttons):
                for j, button in enumerate(row):
                    if button.callback_data == "stats_back_to_menu":
                        keyboard_buttons[i][j] = InlineKeyboardButton("返回主面板", callback_data="panel_back")
                        break
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(text=message, reply_markup=back_keyboard, parse_mode='Markdown')
    
    elif data.startswith("usercard_block_"):
        from services.blacklist import block_user, unblock_user

        if not await db.is_admin(user_id):
            return

        try:
            user_id_to_block = int(data.split("_")[2])
        except (ValueError, IndexError):
            return

        is_blocked, _ = await db.is_blacklisted(user_id_to_block)
        if is_blocked:
            await unblock_user(user_id_to_block)
        else:
            await block_user(
                user_id_to_block,
                "用户信息卡片快捷封禁",
                user_id,
                permanent=True
            )

        await _refresh_usercard_keyboard(query, user_id_to_block)

    elif data.startswith("unblock_"):
        from services.blacklist import verify_unblock_answer
        try:
            option_index = int(data.split("_", 1)[1])
        except ValueError:
            return
        message, success = await verify_unblock_answer(user_id, option_index)
        
        await query.edit_message_text(text=message, reply_markup=None)
        
    elif data.startswith("admin_unblock_"):
        from services import blacklist
        
        user_id_to_unblock = int(data.split("_")[2])
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
            
        response = await blacklist.unblock_user(user_id_to_unblock)
        await query.answer(response, show_alert=True)

        current_page = 1
        message_text = query.message.text or ""
        reply_markup_str = str(query.message.reply_markup) if query.message.reply_markup else ""
        
        is_panel = "panel_blacklist" in reply_markup_str or "panel_stats_blacklist" in reply_markup_str
        is_stats_page = "黑名单用户列表" in message_text or "stats_list_blacklist" in reply_markup_str
        
        if "第" in message_text and "/" in message_text:
            try:
                match = re.search(r'第\s*(\d+)/', message_text)
                if match:
                    current_page = int(match.group(1))
            except:
                pass
        
        if is_panel:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                keyboard_buttons.append([InlineKeyboardButton("返回主面板", callback_data="panel_back")])
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        elif is_stats_page:
            message, keyboard = await blacklist.get_blacklist_keyboard_detailed(page=current_page)
            if keyboard:
                keyboard_buttons = [list(row) for row in keyboard.inline_keyboard]
                for i, row in enumerate(keyboard_buttons):
                    for j, button in enumerate(row):
                        if button.callback_data == "stats_back_to_menu":
                            keyboard_buttons[i][j] = InlineKeyboardButton("返回主面板", callback_data="panel_back")
                            break
                keyboard = InlineKeyboardMarkup(keyboard_buttons)
            else:
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("返回主面板", callback_data="panel_back")]])
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            message, keyboard = await blacklist.get_blacklist_keyboard(page=current_page)
            if keyboard:
                await query.edit_message_text(
                    text=message,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(text=message)
    
    elif data.startswith("blacklist_page_"):
        from services import blacklist
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[2])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await blacklist.get_blacklist_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message)
    
    elif data.startswith("stats_list_all_users_page_"):
        from services.blacklist import get_all_users_keyboard
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[5])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_all_users_keyboard(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data.startswith("stats_list_blacklist_page_"):
        from services.blacklist import get_blacklist_keyboard_detailed
        
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        try:
            page = int(data.split("_")[4])
        except (ValueError, IndexError):
            await query.answer("无效的页码。", show_alert=True)
            return
        
        message, keyboard = await get_blacklist_keyboard_detailed(page=page)
        if keyboard:
            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(text=message, parse_mode='Markdown')
    
    elif data == "stats_back_to_menu":
        if not await db.is_admin(user_id):
            await query.answer("抱歉，您没有权限执行此操作。", show_alert=True)
            return
        
        total_users = await db.get_total_users_count()
        blocked_users = await db.get_blocked_users_count()
        
        stats_message = (
            f"机器人统计数据\n"
            f"---------------------\n"
            f"总用户数: {total_users}\n"
            f"黑名单用户数: {blocked_users}\n\n"
            f"请选择要查看的列表："
        )
        
        keyboard = [
            [InlineKeyboardButton("所有用户列表", callback_data="stats_list_all_users_page_1")],
            [InlineKeyboardButton("黑名单用户列表", callback_data="stats_list_blacklist_page_1")]
        ]
        
        await query.edit_message_text(
            text=stats_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
