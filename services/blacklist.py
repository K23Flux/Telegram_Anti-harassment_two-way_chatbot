"""黑名单：拉黑 / 解封、被临时封禁用户的答题自助解封，以及管理员查看的列表。"""

import html
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from database import models as db
from services.challenges import random_challenge

PER_PAGE = 5

# 正在答解封题的用户：{user_id: {answer, question, options, created_at}}，只存在内存里
pending_unblocks = {}


async def block_user(user_id: int, reason: str, admin_id: int, permanent: bool = False):
    await db.add_to_blacklist(user_id, reason, admin_id, permanent)
    return f"用户 {user_id} 已被{'永久' if permanent else ''}拉黑。\n原因：{reason}"


async def unblock_user(user_id: int):
    await db.remove_from_blacklist(user_id)
    await db.set_user_blacklist_strikes(user_id, 0)
    pending_unblocks.pop(user_id, None)
    return f"用户 {user_id} 已解封。"


# ---------------------------------------------------------------------------
# 用户自助解封
# ---------------------------------------------------------------------------

def _unblock_keyboard(options):
    # 回调里只传选项序号，避免中文选项超出 64 字节的 callback_data 上限
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(option, callback_data=f"unblock_{index}")
            for index, option in enumerate(options)
        ]
    ])


def _pending_session(user_id: int):
    session = pending_unblocks.get(user_id)
    if session and time.time() - session['created_at'] > config.VERIFICATION_TIMEOUT:
        del pending_unblocks[user_id]
        return None
    return session


async def start_unblock_process(user_id: int):
    """被拉黑的用户发消息时调用，返回 (提示文字, 按钮或 None)。"""
    _, is_permanent = await db.is_blacklisted(user_id)
    if is_permanent:
        return "您已被永久封禁，无法自助解封。", None

    intro = "您已被暂时封禁。\n\n如果您认为这是误操作，请回答以下问题以自动解封：\n\n"
    session = _pending_session(user_id)
    if session:
        return (
            "您还有未完成的解封问题，请先作答。\n\n" + intro + session['question'],
            _unblock_keyboard(session['options']),
        )

    challenge = random_challenge()
    pending_unblocks[user_id] = {
        'answer': challenge['correct_answer'],
        'question': challenge['question'],
        'options': challenge['options'],
        'created_at': time.time(),
    }
    return intro + challenge['question'], _unblock_keyboard(challenge['options'])


async def verify_unblock_answer(user_id: int, option_index: int):
    """返回 (提示文字, 是否解封成功)。答错一次直接转为永久封禁。"""
    session = _pending_session(user_id)
    if not session:
        return "解封问题已过期，请重新发送一条消息获取新问题。", False

    del pending_unblocks[user_id]
    options = session['options']
    user_answer = options[option_index] if 0 <= option_index < len(options) else None
    if user_answer == session['answer']:
        await db.remove_from_blacklist(user_id)
        await db.set_user_blacklist_strikes(user_id, 0)
        return "解封成功！您现在可以正常发送消息了。", True

    await db.add_to_blacklist(user_id, reason="解封验证失败", blocked_by=config.BOT_ID, permanent=True)
    return "答案错误，解封失败。您已被永久封禁。", False


# ---------------------------------------------------------------------------
# 管理员查看的列表（HTML 格式）
# ---------------------------------------------------------------------------

def _display_name(first_name, last_name=None, username=None) -> str:
    name = " ".join(part for part in (first_name, last_name) if part) or "（无名字）"
    text = html.escape(name)
    if username:
        text += f" (@{html.escape(username)})"
    return text


def _page_buttons(prefix: str, page: int, total_pages: int):
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("« 上一页", callback_data=f"{prefix}{page - 1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton("下一页 »", callback_data=f"{prefix}{page + 1}"))
    return row


def _clamp_page(page: int, total_count: int) -> tuple[int, int]:
    total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)
    return min(max(page, 1), total_pages), total_pages


async def build_stats_view():
    """/stats 的主菜单。"""
    total_users = await db.get_total_users_count()
    blocked_users = await db.get_blocked_users_count()
    text = (
        "<b>统计</b>\n\n"
        f"用户总数：{total_users}\n"
        f"黑名单：{blocked_users}"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("所有用户", callback_data="users_page_1"),
            InlineKeyboardButton("黑名单", callback_data="bl_page_1"),
        ]
    ])
    return text, keyboard


async def build_users_view(page: int = 1):
    total_count = await db.get_total_users_count()
    page, total_pages = _clamp_page(page, total_count)
    users = await db.get_all_users_paginated(limit=PER_PAGE, offset=(page - 1) * PER_PAGE)

    if not users:
        text = "还没有用户。"
    else:
        lines = [f"<b>所有用户</b>（第 {page}/{total_pages} 页）"]
        for user in users:
            status = "🚫 已拉黑" if user.get('is_blacklisted') else "正常"
            lines.append(
                f"• {_display_name(user.get('first_name'), username=user.get('username'))}\n"
                f"  ID：<code>{user['user_id']}</code>　{status}"
            )
        text = "\n\n".join(lines)

    rows = []
    nav = _page_buttons("users_page_", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("返回统计", callback_data="stats_menu")])
    return text, InlineKeyboardMarkup(rows)


async def build_blacklist_view(page: int = 1):
    total_count = await db.get_blacklist_count()
    page, total_pages = _clamp_page(page, total_count)
    entries = await db.get_blacklist_paginated(limit=PER_PAGE, offset=(page - 1) * PER_PAGE)

    rows = []
    if not entries:
        text = "黑名单是空的。"
    else:
        lines = [f"<b>黑名单</b>（第 {page}/{total_pages} 页）"]
        for entry in entries:
            user_id = entry['user_id']
            details = entry
            kind = "永久" if details.get('permanent') else "临时（可答题自助解封）"
            line = (
                f"• {_display_name(details.get('first_name'), details.get('last_name'), details.get('username'))}\n"
                f"  ID：<code>{user_id}</code>\n"
                f"  类型：{kind}\n"
                f"  原因：{html.escape(details.get('reason') or '无')}"
            )
            if details.get('blocked_at'):
                line += f"\n  时间：{html.escape(str(details['blocked_at']))}"
            lines.append(line)
            label = details.get('first_name') or str(user_id)
            rows.append([
                InlineKeyboardButton(f"解封 {label}", callback_data=f"admin_unblock_{user_id}_{page}")
            ])
        text = "\n\n".join(lines)

    nav = _page_buttons("bl_page_", page, total_pages)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("返回统计", callback_data="stats_menu")])
    return text, InlineKeyboardMarkup(rows)
