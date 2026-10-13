"""精简版新增 / 调整行为的测试：送达标记与失败提示、/del 撤回、速率限制不封禁、
出错提醒节流、管理员按钮只 answer 一次、旧表清理、列表 HTML 转义。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio
from telegram.error import BadRequest, Forbidden, TimedOut

from config import config
from database import models as db
from database.db_manager import DatabaseManager
from handlers import admin_handler, callback_handler, command_handler
from services import blacklist, error_notifier

GROUP_ID = -100900
ADMIN_ID = 9001


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(config, "FORUM_GROUP_ID", GROUP_ID)
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])


@pytest_asyncio.fixture
async def database(tmp_path):
    manager = DatabaseManager(tmp_path / "bot.sqlite3")
    await manager.initialize()
    await db.add_user(user_id=42, username="friend", first_name="Friend")
    await db.update_user_thread_id(42, 77)
    return manager


def _topic_message(message_id=500, reply_to=None):
    return SimpleNamespace(
        message_id=message_id,
        chat_id=GROUP_ID,
        message_thread_id=77,
        is_topic_message=True,
        reply_to_message=reply_to,
        reply_text=AsyncMock(),
    )


def _bot():
    return SimpleNamespace(
        send_message=AsyncMock(),
        set_message_reaction=AsyncMock(),
        delete_message=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# 管理员回复：送达标记 / 失败提示
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_reply_gets_delivered_reaction(monkeypatch, database):
    message = _topic_message()
    context = SimpleNamespace(bot=_bot())
    monkeypatch.setattr(admin_handler, "relay_message", AsyncMock())

    await admin_handler._handle_admin_messages([SimpleNamespace(effective_message=message)], context)

    context.bot.set_message_reaction.assert_awaited_once()
    assert context.bot.set_message_reaction.await_args.kwargs["message_id"] == 500
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_bot_reply_reports_failure_in_topic(monkeypatch, database):
    message = _topic_message()
    context = SimpleNamespace(bot=_bot())
    monkeypatch.setattr(
        admin_handler, "relay_message", AsyncMock(side_effect=Forbidden("bot was blocked by the user"))
    )

    await admin_handler._handle_admin_messages([SimpleNamespace(effective_message=message)], context)

    context.bot.set_message_reaction.assert_not_awaited()
    sent = context.bot.send_message.await_args.kwargs
    assert sent["message_thread_id"] == 77
    assert "停用或拉黑" in sent["text"]


@pytest.mark.asyncio
async def test_reaction_failure_is_ignored(monkeypatch, database):
    message = _topic_message()
    bot = _bot()
    bot.set_message_reaction.side_effect = BadRequest("REACTION_INVALID")
    monkeypatch.setattr(admin_handler, "relay_message", AsyncMock())

    await admin_handler._handle_admin_messages(
        [SimpleNamespace(effective_message=message)], SimpleNamespace(bot=bot)
    )

    bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /del
# ---------------------------------------------------------------------------

def _del_update(reply_to):
    message = _topic_message(message_id=600, reply_to=reply_to)
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=ADMIN_ID)), message


async def _save_mapping(origin_side, admin_message_id=510, user_message_id=310):
    await db.save_message_mapping(
        user_id=42,
        user_chat_id=42,
        user_message_id=user_message_id,
        admin_chat_id=GROUP_ID,
        admin_message_id=admin_message_id,
        thread_id=77,
        origin_side=origin_side,
    )


@pytest.mark.asyncio
async def test_del_deletes_user_copy_then_cleans_up_topic(database):
    await _save_mapping("admin")
    update, message = _del_update(SimpleNamespace(message_id=510))
    context = SimpleNamespace(bot=_bot())

    await admin_handler.delete_command(update, context)

    calls = [call.kwargs for call in context.bot.delete_message.await_args_list]
    assert calls == [
        {"chat_id": 42, "message_id": 310},
        {"chat_id": GROUP_ID, "message_id": 510},
        {"chat_id": GROUP_ID, "message_id": 600},
    ]
    message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_del_refuses_user_originated_messages(database):
    await _save_mapping("user")
    update, message = _del_update(SimpleNamespace(message_id=510))
    context = SimpleNamespace(bot=_bot())

    await admin_handler.delete_command(update, context)

    context.bot.delete_message.assert_not_awaited()
    assert "只能撤回你自己" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_del_without_reply_explains_usage(database):
    # 话题里没回复任何消息时，reply_to_message 指向话题创建消息（id == thread_id）
    update, message = _del_update(SimpleNamespace(message_id=77))
    context = SimpleNamespace(bot=_bot())

    await admin_handler.delete_command(update, context)

    context.bot.delete_message.assert_not_awaited()
    assert "/del" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_del_reports_telegram_failure(database):
    await _save_mapping("admin")
    update, message = _del_update(SimpleNamespace(message_id=510))
    bot = _bot()
    bot.delete_message.side_effect = BadRequest("Message can't be deleted")

    await admin_handler.delete_command(update, SimpleNamespace(bot=bot))

    assert bot.delete_message.await_count == 1
    assert "撤回失败" in message.reply_text.await_args.args[0]


# ---------------------------------------------------------------------------
# 速率限制：只提醒，不封禁
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_drops_and_warns_once_without_banning(monkeypatch, database):
    from handlers import user_handler

    monkeypatch.setattr(config, "MAX_MESSAGES_PER_MINUTE", 2)
    limiter = user_handler.rate_limiter
    monkeypatch.setattr(limiter, "max_messages_per_minute", 2)
    await limiter.clear_user_warning(42)
    forward = AsyncMock(return_value=True)
    monkeypatch.setattr(user_handler, "_forward_verified_messages", forward)
    await db.update_user_verification(42, True)

    replies = []
    for message_id in range(1, 6):
        message = SimpleNamespace(
            message_id=message_id,
            reply_text=AsyncMock(side_effect=lambda text, **_: replies.append(text)),
        )
        user = SimpleNamespace(
            id=42, username="friend", first_name="Friend", last_name=None, language_code=None
        )
        update = SimpleNamespace(effective_message=message, effective_user=user)
        await user_handler._handle_new_messages([update], SimpleNamespace(user_data={}, bot=_bot()))

    assert forward.await_count == 2
    assert len(replies) == 1 and "太快" in replies[0]
    assert await db.is_blacklisted(42) == (False, False)
    await limiter.clear_user_warning(42)


# ---------------------------------------------------------------------------
# 出错提醒
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_notifier_skips_transient_and_throttles(monkeypatch):
    monkeypatch.setattr(error_notifier, "_last_notified", {})
    bot = _bot()

    await error_notifier.error_handler(None, SimpleNamespace(error=TimedOut(), bot=bot))
    bot.send_message.assert_not_awaited()

    for _ in range(3):
        await error_notifier.error_handler(
            None, SimpleNamespace(error=RuntimeError("boom <x>"), bot=bot)
        )
    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs["text"]
    assert "RuntimeError" in text and "&lt;x&gt;" in text

    # BadRequest 继承自 NetworkError，但不算“自己会恢复”的错误
    await error_notifier.error_handler(None, SimpleNamespace(error=BadRequest("bad"), bot=bot))
    assert bot.send_message.await_count == 2


# ---------------------------------------------------------------------------
# 管理员按钮
# ---------------------------------------------------------------------------

def _query(data, user_id=ADMIN_ID):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_admin_unblock_button_answers_once_and_refreshes_list(database):
    await db.add_to_blacklist(42, "test", ADMIN_ID, permanent=True)
    query = _query("admin_unblock_42_1")

    await callback_handler.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace())

    query.answer.assert_awaited_once()
    assert "已解封" in query.answer.await_args.args[0]
    assert await db.is_blacklisted(42) == (False, False)
    assert "黑名单是空的" in query.edit_message_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_non_admin_cannot_use_admin_buttons(database):
    await db.add_to_blacklist(42, "test", ADMIN_ID, permanent=True)
    query = _query("admin_unblock_42_1", user_id=12345)

    await callback_handler.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace())

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    assert await db.is_blacklisted(42) == (True, True)


@pytest.mark.asyncio
async def test_stats_pages_navigate(database):
    for page_data, expected in (("users_page_1", "所有用户"), ("bl_page_1", "黑名单"), ("stats_menu", "用户总数")):
        query = _query(page_data)
        await callback_handler.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace())
        query.answer.assert_awaited_once()
        assert expected in query.edit_message_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_lists_escape_user_supplied_text(database):
    await db.add_user(user_id=43, username="a_b", first_name="<b>evil</b>")
    await db.add_to_blacklist(43, "reason <script>", ADMIN_ID, permanent=False)

    users_text, _ = await blacklist.build_users_view(1)
    blacklist_text, keyboard = await blacklist.build_blacklist_view(1)

    assert "&lt;b&gt;evil&lt;/b&gt;" in users_text
    assert "&lt;script&gt;" in blacklist_text
    assert "临时" in blacklist_text
    callback_data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "admin_unblock_43_1" in callback_data


# ---------------------------------------------------------------------------
# /block、/unblock 在话题里不用写 ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_block_and_unblock_inside_topic(database):
    message = _topic_message()
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=ADMIN_ID))

    await command_handler.block(update, SimpleNamespace(args=["刷广告"]))
    assert await db.is_blacklisted(42) == (True, True)

    await command_handler.unblock(update, SimpleNamespace(args=[]))
    assert await db.is_blacklisted(42) == (False, False)


# ---------------------------------------------------------------------------
# 旧表清理
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_drops_unused_legacy_tables(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    async with aiosqlite.connect(path) as connection:
        for table in ("settings", "statistics", "exemptions", "knowledge_base"):
            await connection.execute(f"CREATE TABLE {table} (id INTEGER)")
        await connection.commit()

    manager = DatabaseManager(path)
    await manager.initialize()

    async with manager.get_connection() as connection:
        async with connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ) as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
    assert tables == {"users", "message_mappings", "blacklist"}


# ---------------------------------------------------------------------------
# 命令菜单
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_command_menus_hide_admin_commands_from_users():
    from services import telegram_commands

    bot = SimpleNamespace(set_my_commands=AsyncMock())
    await telegram_commands.register_bot_commands(SimpleNamespace(bot=bot))

    menus = {
        type(call.kwargs["scope"]).__name__ + str(getattr(call.kwargs["scope"], "chat_id", "")):
        [command.command for command in call.args[0]]
        for call in bot.set_my_commands.await_args_list
    }
    assert menus["BotCommandScopeAllPrivateChats"] == ["start"]
    assert "del" in menus[f"BotCommandScopeChat{GROUP_ID}"]
    assert "help" in menus[f"BotCommandScopeChat{ADMIN_ID}"]
    for call in bot.set_my_commands.await_args_list:
        assert all(command.description for command in call.args[0])
