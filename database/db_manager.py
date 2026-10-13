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
            await self.create_messages_table(db)
            await self.create_message_mappings_table(db)
            await self.create_blacklist_table(db)
            await self.create_admins_table(db)
            await self.create_verification_sessions_table(db)
            await self.create_settings_table(db)
            await self.create_statistics_table(db)
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

    async def create_messages_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                thread_id INTEGER,
                content TEXT,
                media_type TEXT,
                media_file_id TEXT,
                direction TEXT NOT NULL,
                is_forwarded INTEGER DEFAULT 0,
                reply_to_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)')

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

    async def create_admins_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_by INTEGER,
                is_active INTEGER DEFAULT 1,
                permissions TEXT DEFAULT 'all'
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_admins_active ON admins(is_active)')

    async def create_verification_sessions_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS verification_sessions (
                user_id INTEGER PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_verification_expires ON verification_sessions(expires_at)')

    async def create_settings_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        default_settings = [
            ('bot_version', '1.0.0', '机器人当前版本'),
            ('welcome_message', '欢迎使用本机器人！', '新用户收到的欢迎消息'),
            ('verification_enabled', '1', '是否启用新用户验证 (1=是, 0=否)'),
            ('max_message_length', '4096', '允许接收的最大消息长度'),
            ('queue_max_size', '1000', '内部消息处理队列的最大容量'),
        ]
        for key, value, description in default_settings:
            await db.execute(
                'INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)',
                (key, value, description)
            )

    async def create_statistics_table(self, db):
        await db.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stat_date DATE NOT NULL,
                total_users INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                messages_sent INTEGER DEFAULT 0,
                messages_received INTEGER DEFAULT 0,
                verifications_passed INTEGER DEFAULT 0,
                verifications_failed INTEGER DEFAULT 0,
                users_blocked INTEGER DEFAULT 0,
                users_unblocked INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stat_date)
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_statistics_date ON statistics(stat_date)')

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

        # AI 功能已移除：清掉旧版本写入的 AI 相关设置项。
        # filtered_messages / knowledge_base / exemptions 三张旧表不再使用，保留不删，避免丢数据。
        await db.execute(
            "DELETE FROM settings WHERE key IN ("
            "'ai_filter_enabled', 'ai_provider', 'autoreply_enabled', "
            "'gemini_model', 'gemini_model_filter', 'gemini_model_verification', 'gemini_model_autoreply', "
            "'openai_model', 'openai_model_filter', 'openai_model_verification', 'openai_model_autoreply')"
        )

db_manager = DatabaseManager()
