from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from .command_handler import start, help_command, block, unblock, blacklist, stats, getid
from .user_handler import handle_edited_message, handle_message
from .callback_handler import handle_callback
from .admin_handler import delete_command, handle_admin_edit, handle_admin_reply
from config import config

def register_handlers(app: Application):
    """注册命令和消息处理器。命令必须排在转发处理器前面，否则会被当成普通消息转走。"""
    new_message = filters.UpdateType.MESSAGE
    app.add_handler(CommandHandler("getid", getid, filters=new_message))
    app.add_handler(CommandHandler(
        "start",
        start,
        filters=new_message & filters.ChatType.PRIVATE,
    ))
    
    if config.FORUM_GROUP_ID and config.ADMIN_IDS:
        relayable_content = (
            filters.TEXT
            | filters.ANIMATION
            | filters.AUDIO
            | filters.CONTACT
            | filters.Dice.ALL
            | filters.Document.ALL
            | filters.GAME
            | filters.LOCATION
            | filters.PHOTO
            | filters.POLL
            | filters.Sticker.ALL
            | filters.STORY
            | filters.VENUE
            | filters.VIDEO
            | filters.VIDEO_NOTE
            | filters.VOICE
        )
        admin_chat = filters.Chat(chat_id=config.FORUM_GROUP_ID)
        configured_admin = filters.User(user_id=config.ADMIN_IDS)

        app.add_handler(CommandHandler("help", help_command, filters=new_message))
        app.add_handler(CommandHandler("block", block, filters=new_message))
        app.add_handler(CommandHandler("unblock", unblock, filters=new_message))
        app.add_handler(CommandHandler(
            "del",
            delete_command,
            filters=new_message & admin_chat & configured_admin,
        ))
        app.add_handler(CommandHandler("blacklist", blacklist, filters=new_message))
        app.add_handler(CommandHandler("stats", stats, filters=new_message))
        
        app.add_handler(MessageHandler(
            filters.UpdateType.MESSAGE & admin_chat & configured_admin &
            relayable_content,
            handle_admin_reply
        ))

        app.add_handler(MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & admin_chat & configured_admin &
            relayable_content,
            handle_admin_edit
        ))
        
        app.add_handler(MessageHandler(
            filters.UpdateType.MESSAGE & relayable_content &
            filters.ChatType.PRIVATE,
            handle_message
        ))

        app.add_handler(MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & relayable_content &
            filters.ChatType.PRIVATE,
            handle_edited_message
        ))
        
        app.add_handler(CallbackQueryHandler(handle_callback))
    else:
        print("警告: FORUM_GROUP_ID 或 ADMIN_IDS 未设置。已禁用大部分功能。")
