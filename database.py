"""
Haber Asistanı - Veritabanı Kurulum Modülü
==========================================
Bu modül, projenin tüm SQLite şemasını yönetir.
"""

import sqlite3
import logging
import os
from contextlib import contextmanager
from datetime import datetime

DB_NAME = os.getenv("DB_PATH", "haber_asistani.db")
SCHEMA_VERSION = 1 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


@contextmanager
def get_connection(db_path: str = DB_NAME):
    """
    Güvenli veritabanı bağlantısı sağlar.
    - Hata olursa otomatik ROLLBACK yapar
    - Başarılı olursa otomatik COMMIT yapar
    - Bağlantıyı her zaman kapatır (kaynak sızıntısı olmaz)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  
    try:
        # Temel güvenlik ve performans ayarları
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")   # Eş zamanlı okuma/yazma
        conn.execute("PRAGMA synchronous = NORMAL;") # Denge: hız + güvenlik
        conn.execute("PRAGMA cache_size = -8000;")   # 8MB önbellek
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        logger.error(f"Veritabanı hatası, işlem geri alındı: {e}")
        raise
    finally:
        conn.close()




SCHEMA_SQL = {

    "schema_version": """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );
    """,

    "rss_sources": """
        CREATE TABLE IF NOT EXISTS rss_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    UNIQUE NOT NULL,          -- 'BBC News', 'Reuters'
            url         TEXT    UNIQUE NOT NULL,          -- RSS feed URL
            category    TEXT    NOT NULL DEFAULT 'genel', -- 'teknoloji', 'ekonomi' vs.
            is_active   INTEGER NOT NULL DEFAULT 1        -- 1=aktif, 0=devre dışı
                        CHECK (is_active IN (0, 1)),
            last_fetched_at TIMESTAMP,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,

    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_chat_id TEXT   UNIQUE NOT NULL,
            username        TEXT,
            full_name       TEXT,
            language_code   TEXT    DEFAULT 'tr',
            is_active       INTEGER NOT NULL DEFAULT 1
                            CHECK (is_active IN (0, 1)),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,

    "news_logs": """
        CREATE TABLE IF NOT EXISTS news_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id    INTEGER REFERENCES rss_sources(id) ON DELETE SET NULL,
            title        TEXT    NOT NULL,
            url          TEXT    UNIQUE NOT NULL,
            summary      TEXT,                    -- LLM tarafından üretilen özet
            raw_text     TEXT,                    -- trafilatura çıktısı (ham)
            category     TEXT,                    -- Otomatik etiketleme
            is_critical  INTEGER NOT NULL DEFAULT 0
                         CHECK (is_critical IN (0, 1)),  -- Acil durum bayrağı
            keywords     TEXT,                    -- JSON liste: ["deprem","afad"]
            published_at TIMESTAMP,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,

    "user_preferences": """
        CREATE TABLE IF NOT EXISTS user_preferences (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            topic    TEXT    NOT NULL,
            weight   REAL    NOT NULL DEFAULT 1.0
                     CHECK (weight >= 0.0 AND weight <= 10.0),  -- Sınır kontrolü
            UNIQUE (user_id, topic)  -- Aynı konu iki kez girilemez
        );
    """,

    "interactions": """
        CREATE TABLE IF NOT EXISTS interactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            news_id          INTEGER NOT NULL REFERENCES news_logs(id) ON DELETE CASCADE,
            interaction_type TEXT    NOT NULL
                             CHECK (interaction_type IN (
                                 'read',           -- Haberi okudu
                                 'asked_question', -- Haber hakkında soru sordu
                                 'liked',          -- Beğendi
                                 'dismissed',      -- Geç/ilgilenmiyorum
                                 'shared'          -- Paylaştı
                             )),
            session_id       TEXT,   -- Aynı oturumu gruplamak için
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,

    "system_logs": """
        CREATE TABLE IF NOT EXISTS system_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            level      TEXT NOT NULL CHECK (level IN ('INFO','WARNING','ERROR','CRITICAL')),
            module     TEXT,          -- 'rss_fetcher', 'mcp_server' vs.
            message    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
}

# Sık sorgulanan alanlar için index
INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_news_published    ON news_logs(published_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_news_critical     ON news_logs(is_critical) WHERE is_critical = 1;",
    "CREATE INDEX IF NOT EXISTS idx_news_category     ON news_logs(category);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_prefs_user        ON user_preferences(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_syslog_level      ON system_logs(level, created_at DESC);",
]


def _get_current_schema_version(conn: sqlite3.Connection) -> int:
    """Veritabanındaki mevcut şema versiyonunu döndürür."""
    try:
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row["v"] if row["v"] is not None else 0
    except sqlite3.OperationalError:
        return 0  # Tablo henüz yok


def create_tables(conn: sqlite3.Connection) -> None:
    for table_name, sql in SCHEMA_SQL.items():
        conn.execute(sql)


def create_indexes(conn: sqlite3.Connection) -> None:
    for sql in INDEXES_SQL:
        conn.execute(sql)


def record_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, description) VALUES (?, ?)",
        (version, f"İlk kurulum - {datetime.now().strftime('%Y-%m-%d')}")
    )

# ANA KURULUM FONKSİYONU

def initialize_database(
    db_path: str = DB_NAME,
    with_seed_data: bool = False
) -> None:
    """
    Veritabanını başlatır veya mevcut durumu doğrular.

    Args:
        db_path:        Veritabanı dosya yolu
        with_seed_data: True ise örnek RSS kaynakları eklenir
    """
    with get_connection(db_path) as conn:
        current_version = _get_current_schema_version(conn)

        if current_version >= SCHEMA_VERSION:
            return

        create_tables(conn)
        
        create_indexes(conn)

        record_schema_version(conn, SCHEMA_VERSION)

def show_schema_info(db_path: str = DB_NAME) -> None:
    """Mevcut veritabanının tablo ve satır sayılarını gösterir."""
    with get_connection(db_path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

        for table in tables:
            name = table["name"]
            count = conn.execute(f"SELECT COUNT(*) as c FROM {name}").fetchone()["c"]
            print(f"   {name:<25} {count:>12,}")
        print()

if __name__ == "__main__":

    initialize_database(with_seed_data=True)

    show_schema_info()