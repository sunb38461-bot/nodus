"""数据库连接与建表逻辑"""
import aiosqlite
from contextlib import asynccontextmanager
from app.config import DATABASE_PATH

CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    account_type TEXT NOT NULL DEFAULT 'human',
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    failure_count INTEGER DEFAULT 0,
    locked_until TEXT,
    bio TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS web_sessions (
    session_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS agent_tokens (
    token TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    label TEXT,
    bio TEXT DEFAULT '',
    mbti TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER DEFAULT 0,
    last_active_at TEXT,
    first_used_at TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES users(id),
    author_name TEXT NOT NULL,
    forum_tag TEXT DEFAULT 'general',
    created_at TEXT NOT NULL,
    flagged INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    author_id INTEGER NOT NULL REFERENCES users(id),
    author_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER NOT NULL REFERENCES users(id),
    actor_label TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    ref_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    stats_snapshot TEXT,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'wiki',
    author_id INTEGER NOT NULL REFERENCES users(id),
    author_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    emoji TEXT DEFAULT '👍',
    created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS agent_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id),
    to_user_id INTEGER NOT NULL REFERENCES users(id),
    relation_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    UNIQUE(from_user_id, to_user_id, relation_type)
);

-- 关键查询字段索引
CREATE INDEX IF NOT EXISTS idx_web_sessions_session_id ON web_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expires_at ON web_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_token ON agent_tokens(token);
CREATE INDEX IF NOT EXISTS idx_agent_tokens_owner ON agent_tokens(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_posts_forum_tag ON posts(forum_tag);
CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_author_id ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_replies_post_id ON replies(post_id);
CREATE INDEX IF NOT EXISTS idx_replies_author_id ON replies(author_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_category ON knowledge_items(category);
CREATE INDEX IF NOT EXISTS idx_reports_report_date ON reports(report_date);
CREATE INDEX IF NOT EXISTS idx_post_likes_post_id ON post_likes(post_id);
CREATE INDEX IF NOT EXISTS idx_post_likes_user_id ON post_likes(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_relations_from ON agent_relations(from_user_id);
CREATE INDEX IF NOT EXISTS idx_agent_relations_to ON agent_relations(to_user_id);
CREATE INDEX IF NOT EXISTS idx_agent_relations_status ON agent_relations(status);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL REFERENCES users(id),
    to_user_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_user_id);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

CREATE TABLE IF NOT EXISTS tag_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, tag),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_tag_subs_user ON tag_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_tag_subs_tag ON tag_subscriptions(tag);

-- ── 游戏：秘境 ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS game_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    class TEXT NOT NULL DEFAULT 'warrior',
    level INTEGER NOT NULL DEFAULT 1,
    exp INTEGER NOT NULL DEFAULT 0,
    hp INTEGER NOT NULL DEFAULT 100,
    max_hp INTEGER NOT NULL DEFAULT 100,
    mp INTEGER NOT NULL DEFAULT 50,
    max_mp INTEGER NOT NULL DEFAULT 50,
    str INTEGER NOT NULL DEFAULT 5,
    wis INTEGER NOT NULL DEFAULT 5,
    cha INTEGER NOT NULL DEFAULT 5,
    luck INTEGER NOT NULL DEFAULT 3,
    gold INTEGER NOT NULL DEFAULT 100,
    current_area TEXT NOT NULL DEFAULT 'data_wasteland',
    status TEXT NOT NULL DEFAULT 'active',
    last_explore_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS game_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    rarity TEXT DEFAULT 'common',
    stats TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(character_id) REFERENCES game_characters(id)
);

CREATE TABLE IF NOT EXISTS game_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    area TEXT NOT NULL,
    summary TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(character_id) REFERENCES game_characters(id)
);

CREATE TABLE IF NOT EXISTS game_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    achievement_key TEXT NOT NULL,
    achieved_at TEXT NOT NULL,
    UNIQUE(character_id, achievement_key),
    FOREIGN KEY(character_id) REFERENCES game_characters(id)
);

CREATE INDEX IF NOT EXISTS idx_game_chars_user ON game_characters(user_id);
CREATE INDEX IF NOT EXISTS idx_game_chars_level ON game_characters(level);
CREATE INDEX IF NOT EXISTS idx_game_inv_char ON game_inventory(character_id);
CREATE INDEX IF NOT EXISTS idx_game_events_char ON game_events(character_id);
CREATE INDEX IF NOT EXISTS idx_game_events_created ON game_events(created_at);
CREATE INDEX IF NOT EXISTS idx_game_achieve_char ON game_achievements(character_id);
"""


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（每次请求新建连接，aiosqlite 本身轻量）"""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


@asynccontextmanager
async def db_transaction():
    """事务上下文管理器，自动 commit / rollback"""
    db = await get_db()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def init_db():
    """初始化数据库：建表 + 默认管理员 + 系统 Agent + 迁移"""
    db = await get_db()
    try:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()

        # 迁移：knowledge_items 添加 category 列
        try:
            await db.execute("ALTER TABLE knowledge_items ADD COLUMN category TEXT DEFAULT 'wiki'")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：users 添加 bio 列
        try:
            await db.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：post_likes 添加 emoji 列
        try:
            await db.execute("ALTER TABLE post_likes ADD COLUMN emoji TEXT DEFAULT '👍'")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：agent_tokens 添加 bio 列
        try:
            await db.execute("ALTER TABLE agent_tokens ADD COLUMN bio TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：agent_tokens 添加 mbti 列
        try:
            await db.execute("ALTER TABLE agent_tokens ADD COLUMN mbti TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：knowledge_items 添加 visibility 列
        try:
            await db.execute("ALTER TABLE knowledge_items ADD COLUMN visibility TEXT DEFAULT 'public'")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：knowledge_items 添加 source 列
        try:
            await db.execute("ALTER TABLE knowledge_items ADD COLUMN source TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：users 添加 invite_code 列（个人邀请码）
        try:
            await db.execute("ALTER TABLE users ADD COLUMN invite_code TEXT")
            await db.commit()
        except Exception:
            pass  # 列已存在

        # 迁移：users 添加 invited_by 列（邀请人 user_id）
        try:
            await db.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
            await db.commit()
        except Exception:
            pass  # 列已存在
    finally:
        await db.close()
