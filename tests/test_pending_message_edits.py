from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import (
    Chat,
    Message,
    PhotoSize,
    Update,
    User,
)
from telegram.error import BadRequest


def _stub_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    monkeypatch.setitem(sys.modules, name, module)


def _load_user_handler(monkeypatch):
    _stub_module(
        monkeypatch,
        "services.verification",
        create_verification=AsyncMock(),
        is_verification_pending=lambda user_id: (False, False),
        get_pending_verification_message=lambda user_id: None,
    )
    _stub_module(
        monkeypatch,
        "services.thread_manager",
        get_or_create_thread=AsyncMock(),
    )
    _stub_module(
        monkeypatch,
        "services.rate_limiter",
        rate_limiter=SimpleNamespace(),
    )

    module_path = Path(__file__).resolve().parents[1] / "handlers" / "user_handler.py"
    spec = importlib.util.spec_from_file_location(
        "tests.loaded_user_handler",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _album_update(message_id, caption, *, edited=False):
    message = Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type=Chat.PRIVATE),
        from_user=User(id=42, first_name="Sender", is_bot=False),
        photo=(PhotoSize(f"photo-{message_id}", f"unique-{message_id}", 10, 10),),
        caption=caption,
        media_group_id="pending-album",
    )
    if edited:
        return Update(update_id=1000 + message_id, edited_message=message)
    return Update(update_id=message_id, message=message)


def _pending_update(message_id, user, *, reply_text=None):
    message = SimpleNamespace(
        message_id=message_id,
        chat_id=user.id,
        reply_text=reply_text or AsyncMock(),
    )
    return SimpleNamespace(
        effective_message=message,
        effective_user=user,
    )


def _configure_unverified_user(monkeypatch, user_handler):
    monkeypatch.setattr(user_handler.config, "VERIFICATION_ENABLED", True)
    monkeypatch.setattr(
        user_handler.rate_limiter,
        "check_user_rate_limit",
        AsyncMock(return_value=(False, False)),
        raising=False,
    )
    monkeypatch.setattr(
        user_handler.db,
        "is_blacklisted",
        AsyncMock(return_value=(False, False)),
    )
    monkeypatch.setattr(
        user_handler.db,
        "get_user",
        AsyncMock(return_value={"is_verified": False}),
    )
    monkeypatch.setattr(
        user_handler.db,
        "update_user_profile",
        AsyncMock(),
    )
    monkeypatch.setattr(
        user_handler,
        "is_verification_pending",
        Mock(return_value=(True, False)),
    )
    monkeypatch.setattr(
        user_handler,
        "get_pending_verification_message",
        Mock(return_value=("verification question", object())),
    )


def test_missing_thread_error_does_not_match_missing_source_message(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)

    assert user_handler._is_missing_thread_error(
        BadRequest("Message thread not found")
    ) is True
    closed_error = BadRequest("Topic was closed")
    assert user_handler._is_missing_thread_error(closed_error) is False
    assert user_handler._is_closed_thread_error(closed_error) is True
    assert user_handler._is_missing_thread_error(
        BadRequest("Message to forward not found")
    ) is False
    assert user_handler._is_missing_thread_error(
        BadRequest("Message not found")
    ) is False


@pytest.mark.asyncio
async def test_unverified_messages_keep_every_arrival_as_an_ordered_batch(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)
    _configure_unverified_user(monkeypatch, user_handler)
    user = SimpleNamespace(
        id=42,
        username="sender",
        first_name="Sender",
        last_name=None,
        language_code="zh-hans",
    )
    first = _pending_update(101, user)
    second = _pending_update(102, user)
    third = _pending_update(201, user)
    fourth = _pending_update(202, user)
    first_batch = [second, first]
    second_batch = [fourth, third]
    context = SimpleNamespace(user_data={}, bot=AsyncMock())

    await user_handler._handle_new_messages(first_batch, context)
    await user_handler._handle_new_messages(second_batch, context)

    assert context.user_data["pending_updates"] == [
        [first, second],
        [third, fourth],
    ]


@pytest.mark.asyncio
async def test_pre_verification_edit_replaces_matching_pending_album_update(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)
    pending_first = _album_update(101, "first")
    pending_second = _album_update(102, "old caption")
    edited_second = _album_update(102, "new caption", edited=True)
    context = SimpleNamespace(
        user_data={"pending_updates": [[pending_first, pending_second]]},
        bot=AsyncMock(),
    )
    mapping_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        user_handler.db,
        "get_message_mapping_by_user_endpoint",
        mapping_lookup,
    )
    sync_edit = AsyncMock()
    monkeypatch.setattr(user_handler, "sync_edited_message", sync_edit)

    await user_handler.handle_edited_message(edited_second, context)

    pending = context.user_data["pending_updates"]
    assert pending == [[pending_first, edited_second]]
    assert pending[0][1].effective_message.caption == "new caption"
    mapping_lookup.assert_not_awaited()
    sync_edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_verification_edits_replace_only_their_exact_batch_items(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)
    first = _album_update(101, "first")
    second = _album_update(102, "second old")
    third = _album_update(201, "third old")
    fourth = _album_update(202, "fourth")
    edited_second = _album_update(102, "second new", edited=True)
    edited_third = _album_update(201, "third new", edited=True)
    context = SimpleNamespace(
        user_data={"pending_updates": [[first, second], [third, fourth]]},
        bot=AsyncMock(),
    )
    mapping_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        user_handler.db,
        "get_message_mapping_by_user_endpoint",
        mapping_lookup,
    )
    sync_edit = AsyncMock()
    monkeypatch.setattr(user_handler, "sync_edited_message", sync_edit)

    await user_handler.handle_edited_message(edited_third, context)
    await user_handler.handle_edited_message(edited_second, context)

    assert context.user_data["pending_updates"] == [
        [first, edited_second],
        [edited_third, fourth],
    ]
    mapping_lookup.assert_not_awaited()
    sync_edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_topic_is_reopened_and_relay_retried(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)
    user = SimpleNamespace(id=42)
    message = SimpleNamespace(
        message_id=101,
        chat_id=42,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(effective_message=message, effective_user=user)
    context = SimpleNamespace(
        bot=SimpleNamespace(reopen_forum_topic=AsyncMock()),
        user_data={},
    )
    monkeypatch.setattr(
        user_handler,
        "get_or_create_thread",
        AsyncMock(return_value=(77, False)),
    )
    resend = AsyncMock(
        side_effect=[
            BadRequest("TOPIC_CLOSED"),
            SimpleNamespace(message_id=501),
        ]
    )
    monkeypatch.setattr(user_handler, "_resend_message", resend)
    invalid_thread = AsyncMock()
    monkeypatch.setattr(user_handler, "handle_invalid_thread", invalid_thread)

    forwarded = await user_handler._forward_verified_messages(
        [update],
        context,
        42,
    )

    assert forwarded is True
    assert resend.await_count == 2
    context.bot.reopen_forum_topic.assert_awaited_once_with(
        chat_id=user_handler.config.FORUM_GROUP_ID,
        message_thread_id=77,
    )
    invalid_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_failed_batch_is_retained_for_next_attempt(monkeypatch):
    user_handler = _load_user_handler(monkeypatch)
    user = SimpleNamespace(
        id=42,
        username="sender",
        first_name="Sender",
        last_name=None,
        language_code="zh-hans",
    )
    update = _pending_update(101, user)
    context = SimpleNamespace(user_data={}, bot=AsyncMock())
    monkeypatch.setattr(
        user_handler.rate_limiter,
        "check_user_rate_limit",
        AsyncMock(return_value=(False, False)),
        raising=False,
    )
    monkeypatch.setattr(
        user_handler.db,
        "is_blacklisted",
        AsyncMock(return_value=(False, False)),
    )
    monkeypatch.setattr(
        user_handler.db,
        "get_user",
        AsyncMock(return_value={"is_verified": True}),
    )
    monkeypatch.setattr(
        user_handler.db,
        "update_user_profile",
        AsyncMock(),
    )
    forward = AsyncMock(return_value=False)
    monkeypatch.setattr(user_handler, "_forward_verified_messages", forward)

    await user_handler._handle_new_messages([update], context)

    forward.assert_awaited_once_with([update], context, 42)
    assert context.user_data["pending_updates"] == [[update]]
