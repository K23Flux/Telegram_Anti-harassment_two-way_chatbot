import aiosqlite
import os
import logging
from contextlib import asynccontextmanager

class DatabaseManager:
    _instance = None

    def __new__(cls, db_path=None):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance.db_path = './data/bot.db'

        if db_path is not None:
            cls._instance.db_path = os.fspath(db_path)

        if db_path is not None or not getattr(cls._instance, '_data_directory_ready', False):
            cls._instance.ensure_data_directory()
        return cls._instance

    def ensure_data_directory(self):
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._data_directory_ready = True

    @asynccontextmanager
    async def get_connection(self):
        db = await aiosqlite.connect(self.db_path)
        try:
            await db.execute('PRAGMA foreign_keys = ON')
            await db.execute('PRAGMA busy_timeout = 5000')
            yield db
        finally:
            await db.close()

    async def initialize(self):
        async with self.get_connection() as db:
            await self.create_users_table(db)
            await self.create_message_mappings_table(db)
            await self.create_blacklist_table(db)
            await self.migrate_database(db)
            await db.commit()
        logging.info("数据库初始化完成。")

    async def create_users_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL,
                last_name TEXT,
                language_code TEXT,
                is_verified INTEGER DEFAULT 0,
                is_blacklisted INTEGER DEFAULT 0,
                blacklist_strikes INTEGER DEFAULT 0 NOT NULL,
                thread_id INTEGER,
                verification_attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_verified ON users(is_verified)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_blacklisted ON users(is_blacklisted)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_users_thread ON users(thread_id)')

    async def create_message_mappings_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS message_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                user_message_id INTEGER NOT NULL,
                admin_chat_id INTEGER NOT NULL,
                admin_message_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                origin_side TEXT NOT NULL CHECK (origin_side IN ('user', 'admin')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_chat_id, user_message_id),
                UNIQUE (admin_chat_id, admin_message_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute(
            'CREATE INDEX IF NOT EXISTS idx_message_mappings_user ON message_mappings(user_id)'
        )
        await db.execute(
            'CREATE INDEX IF NOT EXISTS idx_message_mappings_thread ON message_mappings(thread_id)'
        )

    async def create_blacklist_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL,
                blocked_by INTEGER NOT NULL,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                permanent INTEGER DEFAULT 0,
                unblock_question TEXT,
                unblock_answer TEXT,
                unblock_attempts INTEGER DEFAULT 0,
                last_unblock_attempt TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_blacklist_blocked_at ON blacklist(blocked_at)')

    async def migrate_database(self, db):
        try:
            await db.execute('ALTER TABLE users ADD COLUMN blacklist_strikes INTEGER DEFAULT 0 NOT NULL')
            logging.info("数据库迁移：成功为 'users' 表添加 'blacklist_strikes' 列。")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise e

        try:
            await db.execute('ALTER TABLE blacklist ADD COLUMN permanent INTEGER DEFAULT 0')
            logging.info("数据库迁移：成功为 'blacklist' 表添加 'permanent' 列。")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise e

        # 精简后不再使用的旧表：没用过的统计/管理员/消息/设置表，以及已删除的 AI 功能留下的表
        for table in (
            "messages", "admins", "verification_sessions", "settings", "statistics",
            "filtered_messages", "knowledge_base", "exemptions",
        ):
            await db.execute(f"DROP TABLE IF EXISTS {table}")

db_manager = DatabaseManager()
