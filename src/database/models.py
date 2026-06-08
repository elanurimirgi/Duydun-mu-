"""
Haber Asistanı - Veritabanı Model Katmanı
==========================================
Her tablo için bir sınıf. Her sınıf kendi tablosunun
okuma/yazma işlemlerinden sorumludur.

Kullanım (tools.py içinden):
    from database.models import UserModel, NewsModel

    user = UserModel.get_or_create(telegram_chat_id="123456")
    NewsModel.save(title="...", url="...", source_id=1)
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.config.settings import DB_PATH
from src.database.setup import get_connection

logger = logging.getLogger(__name__)

DB_NAME = DB_PATH


# VERİ TAŞIYICI SINIFLAR (Dataclass)
# Veritabanından gelen satırları Python nesnesine çevirir.
# tools.py'da conn.row["title"] yerine news.title yazabilirsin.
@dataclass
class User:
    id: int
    telegram_chat_id: str
    username: Optional[str]
    full_name: Optional[str]
    language_code: str
    is_active: bool
    created_at: str
    last_seen_at: str


@dataclass
class News:
    id: int
    source_id: Optional[int]
    title: str
    url: str
    summary: Optional[str]
    raw_text: Optional[str]
    category: Optional[str]
    is_critical: bool
    keywords: Optional[list]   
    published_at: Optional[str]
    created_at: str


@dataclass
class UserPreference:
    id: int
    user_id: int
    topic: str
    weight: float


@dataclass
class Interaction:
    id: int
    user_id: int
    news_id: int
    interaction_type: str
    session_id: Optional[str]
    topic: Optional[str]
    sentiment: Optional[str]
    created_at: str



# MODEL SINIFLARI


class UserModel:
    """
    'users' tablosu için tüm veritabanı işlemleri.
    n8n'den gelen Telegram chat_id ile kullanıcı kaydı ve sorgusu yapılır.
    """

    @staticmethod
    def get_or_create(telegram_chat_id: str,
                      username: str = None,
                      full_name: str = None) -> User:
        """
        Kullanıcı varsa getirir, yoksa oluşturur.
        n8n'deki her Telegram mesajında bu fonksiyon çağrılır.

        Kullanım:
            user = UserModel.get_or_create(telegram_chat_id="123456789")
        """
        with get_connection(DB_NAME) as conn:
            # Önce mevcut kullanıcıyı ara
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_chat_id = ?",
                (telegram_chat_id,)
            ).fetchone()

            if row:
                # Bulunduysa son görülme zamanını güncelle
                conn.execute(
                    "UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["id"],)
                )
                logger.info(f"Mevcut kullanıcı bulundu: {telegram_chat_id}")
                return _row_to_user(row)

            # Yoksa yeni kayıt oluştur
            cursor = conn.execute(
                """INSERT INTO users (telegram_chat_id, username, full_name)
                   VALUES (?, ?, ?)""",
                (telegram_chat_id, username, full_name)
            )
            logger.info(f"Yeni kullanıcı oluşturuldu: {telegram_chat_id}")

            # Oluşturulan kaydı geri döndür
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (cursor.lastrowid,)
            ).fetchone()
            return _row_to_user(row)

    @staticmethod
    def get_by_chat_id(telegram_chat_id: str) -> Optional[User]:
        """
        Chat ID ile kullanıcı getirir. Bulamazsa None döner.

        Kullanım:
            user = UserModel.get_by_chat_id("123456789")
            if not user:
                print("Kullanıcı bulunamadı")
        """
        with get_connection(DB_NAME) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_chat_id = ?",
                (telegram_chat_id,)
            ).fetchone()
            return _row_to_user(row) if row else None

    @staticmethod
    def get_all_active() -> list[User]:
        """
        Sabah bülteni için tüm aktif kullanıcıları getirir.
        n8n S1 (Rutin Akış) senaryosunda kullanılır.

        Kullanım:
            users = UserModel.get_all_active()
            for user in users:
                # Her kullanıcıya bülten gönder
        """
        with get_connection(DB_NAME) as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE is_active = 1 ORDER BY created_at"
            ).fetchall()
            return [_row_to_user(r) for r in rows]

    @staticmethod
    def deactivate(telegram_chat_id: str) -> bool:
        """
        Kullanıcıyı pasif yapar. /stop komutu için.
        Veriyi silmez, sadece is_active = 0 yapar.
        """
        with get_connection(DB_NAME) as conn:
            cursor = conn.execute(
                "UPDATE users SET is_active = 0 WHERE telegram_chat_id = ?",
                (telegram_chat_id,)
            )
            return cursor.rowcount > 0


# ------------------------------------------------------------------------------

class NewsModel:
    """
    'news_logs' tablosu için tüm veritabanı işlemleri.
    RSS'ten çekilen ve LLM tarafından işlenen haberler burada tutulur.
    """

    @staticmethod
    def save(title: str,
             url: str,
             source_id: int = None,
             summary: str = None,
             raw_text: str = None,
             category: str = None,
             is_critical: bool = False,
             keywords: list = None,
             published_at: str = None) -> Optional[int]:
        """
        Yeni haber kaydeder. URL zaten varsa atlar (UNIQUE kısıtı).
        Aynı haberi iki kez kaydetme sorununu önler.

        Döndürür: Yeni kaydın ID'si, haber zaten varsa None

        Kullanım:
            news_id = NewsModel.save(
                title="Deprem haberi",
                url="https://...",
                is_critical=True,
                keywords=["deprem", "afad"]
            )
        """
        with get_connection(DB_NAME) as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO news_logs
                       (source_id, title, url, summary, raw_text,
                        category, is_critical, keywords, published_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        title,
                        url,
                        summary,
                        raw_text,
                        category,
                        1 if is_critical else 0,
                        json.dumps(keywords, ensure_ascii=False) if keywords else None,
                        published_at
                    )
                )
                logger.info(f"Haber kaydedildi: {title[:50]}...")
                return cursor.lastrowid

            except sqlite3.IntegrityError:
              # Sadece URL zaten varsa (UNIQUE ihlali) sessizce None döner
              logger.debug(f"Haber zaten mevcut, atlandı: {url}")
              return None
            except Exception as e:
               # Disk dolması gibi GERÇEK bir hata varsa logla ve fırlat
               logger.error(f"Haber kaydedilirken kritik hata: {e}")
            raise

    @staticmethod
    def get_latest(limit: int = 20, category: str = None) -> list[News]:
        """
        En son haberleri getirir. S1 Günlük Bülten için kullanılır.

        Kullanım:
            haberler = NewsModel.get_latest(limit=10, category="teknoloji")
        """
        with get_connection(DB_NAME) as conn:
            if category:
                rows = conn.execute(
                    """SELECT * FROM news_logs
                       WHERE category = ?
                       ORDER BY published_at DESC LIMIT ?""",
                    (category, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM news_logs
                       ORDER BY published_at DESC LIMIT ?""",
                    (limit,)
                ).fetchall()
            return [_row_to_news(r) for r in rows]

    @staticmethod
    def get_critical(limit: int = 10) -> list[News]:
        """
        Acil/kritik haberleri getirir.
        S3 Priority Watcher senaryosunda kullanılır.
        İndeks (idx_news_critical) sayesinde çok hızlı çalışır.

        Kullanım:
            acil_haberler = NewsModel.get_critical()
        """
        with get_connection(DB_NAME) as conn:
            rows = conn.execute(
                """SELECT * FROM news_logs
                   WHERE is_critical = 1
                   ORDER BY published_at DESC LIMIT ?""",
                (limit,)
            ).fetchall()
            return [_row_to_news(r) for r in rows]

    @staticmethod
    def get_son_dakika(limit: int = 10) -> list:
        """
        Son dakika haberlerini getirir.
        Son 24 saat içinde DB'ye kaydedilen haberleri getirir.
        is_critical=1 olanlar önce gelir, sonra en yeni kaydedilenler.
        S3 Son Dakika senaryosunda kullanılır.

        Kullanım:
            son_dakika = NewsModel.get_son_dakika(limit=5)
        """
        with get_connection(DB_NAME) as conn:
            # created_at = DB'ye kaydedilme zamanı (published_at değil)
            rows = conn.execute(
                """SELECT * FROM news_logs
                   WHERE created_at >= datetime('now', '-24 hours')
                   ORDER BY is_critical DESC, created_at DESC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            # Son 24 saatte haber yoksa en son 10 haberi getir
            if not rows:
                rows = conn.execute(
                    """SELECT * FROM news_logs
                       ORDER BY is_critical DESC, created_at DESC
                       LIMIT ?""",
                    (limit,)
                ).fetchall()
            return [_row_to_news(r) for r in rows]

    @staticmethod
    def get_by_url(url: str) -> Optional[News]:
        """
        URL ile haber getirir. S2 Reaktif Sohbet'te
        kullanıcının sorduğu haberi bulmak için kullanılır.
        """
        with get_connection(DB_NAME) as conn:
            row = conn.execute(
                "SELECT * FROM news_logs WHERE url = ?", (url,)
            ).fetchone()
            return _row_to_news(row) if row else None

    @staticmethod
    def update_summary(news_id: int, summary: str) -> bool:
        """
        LLM özet ürettikten sonra haberi günceller.
        MCP tool'u özeti üretip buraya yazar.
        """
        with get_connection(DB_NAME) as conn:
            cursor = conn.execute(
                "UPDATE news_logs SET summary = ? WHERE id = ?",
                (summary, news_id)
            )
            return cursor.rowcount > 0

    @staticmethod
    def mark_as_critical(news_id: int,
                         category: str,
                         keywords: list) -> bool:
        """
        Bir haberi acil/kritik olarak işaretler.
        check_emergency tool'u tarafından çağrılır.
        KeywordDetector kritik tespit ettiğinde bu fonksiyon ile
        DB'deki is_critical, keywords ve category alanları güncellenir.

        Args:
            news_id:  Güncellenecek haberin ID'si.
            category: Acil durum kategorisi (deprem, sel, yangin, teror, salgin).
            keywords: Eşleşen anahtar kelimeler listesi.

        Döndürür: True ise güncelleme başarılı, False ise haber bulunamadı.

        Kullanım:
            NewsModel.mark_as_critical(
                news_id=42,
                category="deprem",
                keywords=["deprem", "enkaz", "afad"]
            )
        """
        with get_connection(DB_NAME) as conn:
            cursor = conn.execute(
                """UPDATE news_logs
                   SET is_critical = 1,
                       keywords = ?,
                       category = ?
                   WHERE id = ?""",
                (
                    json.dumps(keywords, ensure_ascii=False),
                    category,
                    news_id
                )
            )
            if cursor.rowcount > 0:
                logger.info(f"Haber #{news_id} kritik olarak işaretlendi: {category}")
                return True
            return False

    @staticmethod
    def search(query: str, limit: int = 10) -> list:
        """
        Başlık, özet ve anahtar kelimeler üzerinde metin araması yapar.
        S2 Reaktif Sohbet senaryosunda kullanıcının belirli konulardaki
        haberlere ulaşmasını sağlar.

        Args:
            query: Aranacak kelime veya ifade (örn: "deprem", "dolar").
            limit: Maksimum sonuç sayısı (varsayılan: 10).

        Döndürür: News listesi (yayın tarihine göre sıralı).

        Kullanım:
            sonuclar = NewsModel.search("deprem", limit=5)
            for haber in sonuclar:
                print(haber.title)
        """
        with get_connection(DB_NAME) as conn:
            # Sorguyu kelimelere böl (2 karakterden kısa kelimeleri atla)
            words = [w.strip() for w in query.strip().split() if len(w.strip()) > 2]

            if not words:
                # Çok kısa sorgu → eski davranış
                words = [query.strip()]

            # Her kelime için OR koşulu: title LIKE ? OR summary LIKE ? OR keywords LIKE ?
            conditions = []
            params = []
            for word in words:
                term = f"%{word}%"
                conditions.append(
                    "(title LIKE ? OR summary LIKE ? OR keywords LIKE ?)"
                )
                params.extend([term, term, term])

            # Kelimelerden herhangi biri eşleşirse getir (OR mantığı)
            where_clause = " OR ".join(conditions)
            params.append(limit)

            rows = conn.execute(
                f"""SELECT * FROM news_logs
                   WHERE {where_clause}
                   ORDER BY published_at DESC
                   LIMIT ?""",
                params
            ).fetchall()
            return [_row_to_news(r) for r in rows]


# ------------------------------------------------------------------------------

class PreferenceModel:
    """
    'user_preferences' tablosu için işlemler.
    S4 Davranışsal Analiz: etkileşimlere göre ağırlıkları günceller.
    """

    @staticmethod
    def get_user_profile(user_id: int) -> list[UserPreference]:
        """
        Kullanıcının tüm ilgi alanlarını ağırlıklı olarak getirir.
        S1.2 Haftalık Kişisel Seçki'de kişiselleştirme için kullanılır.

        Kullanım:
            profil = PreferenceModel.get_user_profile(user_id=1)
            # [UserPreference(topic="teknoloji", weight=4.5), ...]
        """
        with get_connection(DB_NAME) as conn:
            rows = conn.execute(
                """SELECT * FROM user_preferences
                   WHERE user_id = ?
                   ORDER BY weight DESC""",
                (user_id,)
            ).fetchall()
            return [_row_to_preference(r) for r in rows]

    @staticmethod
    def update_weight(user_id: int, topic: str, delta: float,
                      dismissed: bool = False) -> UserPreference:
        """
        Bir konunun ağırlığını günceller.

        Beğeni (dismissed=False):
            - Kategori DB'de yoksa → 0.5 ile ekle
            - Kategori DB'de varsa → mevcut ağırlık + delta (max 10.0)

        Beğenmeme (dismissed=True):
            - Kategori DB'de yoksa → 0.0 ile ekle
            - Kategori DB'de varsa → direkt -1.0 yap

        Kullanım:
            PreferenceModel.update_weight(user_id=1, topic="spor", delta=0.5)
            PreferenceModel.update_weight(user_id=1, topic="spor", delta=-1.0, dismissed=True)
        """
        MAX_WEIGHT = 10.0
        MIN_WEIGHT = -5.0

        with get_connection(DB_NAME) as conn:
            row = conn.execute(
                "SELECT * FROM user_preferences WHERE user_id = ? AND topic = ?",
                (user_id, topic)
            ).fetchone()

            if dismissed:
                # Beğenmeme: varsa -1.0 yap, yoksa 0.0 ile ekle
                if row:
                    new_weight = -1.0
                    conn.execute(
                        """UPDATE user_preferences SET weight = ?
                        WHERE user_id = ? AND topic = ?""",
                        (new_weight, user_id, topic)
                    )
                else:
                    new_weight = 0.0
                    conn.execute(
                        "INSERT INTO user_preferences (user_id, topic, weight) VALUES (?, ?, ?)",
                        (user_id, topic, new_weight)
                    )
            else:
                # Beğeni: varsa delta ekle, yoksa 0.5 ile başla
                if row:
                    new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, row["weight"] + delta))
                    conn.execute(
                        """UPDATE user_preferences SET weight = ?
                        WHERE user_id = ? AND topic = ?""",
                        (new_weight, user_id, topic)
                    )
                else:
                    new_weight = 0.5
                    conn.execute(
                        "INSERT INTO user_preferences (user_id, topic, weight) VALUES (?, ?, ?)",
                        (user_id, topic, new_weight)
                    )

            row = conn.execute(
                "SELECT * FROM user_preferences WHERE user_id = ? AND topic = ?",
                (user_id, topic)
            ).fetchone()
            return _row_to_preference(row)

    @staticmethod
    def delete(user_id: int, topic: str) -> bool:
        """
        Kullanıcının belirli bir ilgi alanını DB'den siler.
        Kullanıcı ilgi alanı seçimini kaldırdığında çağrılır.
        """
        with get_connection(DB_NAME) as conn:
            cursor = conn.execute(
                "DELETE FROM user_preferences WHERE user_id = ? AND topic = ?",
                (user_id, topic)
            )
            logger.info(f"Tercih silindi: user={user_id}, topic={topic}")
            return cursor.rowcount > 0
# ------------------------------------------------------------------------------

class InteractionModel:
    """
    'interactions' tablosu için işlemler.
    Feedback loop: kullanıcının her haberle olan etkileşimi kaydedilir.
    """

    @staticmethod
    def save(user_id: int,
             news_id: int,
             interaction_type: str,
             session_id: str = None,
             topic: str = None,
             sentiment: str = None) -> int:
        """
        Kullanıcı etkileşimini kaydeder.
        interaction_type: 'read', 'liked', 'dismissed', 'asked_question', 'shared'
        topic: Etkileşimin konusu (örn: 'ekonomi', 'spor')
        sentiment: Duygu analizi ('positive', 'negative', 'neutral')
        """
        with get_connection(DB_NAME) as conn:
            cursor = conn.execute(
                """INSERT INTO interactions
                   (user_id, news_id, interaction_type, session_id, topic, sentiment)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, news_id, interaction_type, session_id, topic, sentiment)
            )
            logger.info(
                f"Etkileşim kaydedildi: user={user_id}, news={news_id}, "
                f"type={interaction_type}, topic={topic}, sentiment={sentiment}"
            )
            return cursor.lastrowid

    @staticmethod
    def get_user_history(user_id: int, limit: int = 50) -> list[Interaction]:
        """
        Kullanıcının son etkileşimlerini getirir.
        S4 Davranışsal Analiz için LLM bu veriyi okur.

        Kullanım:
            gecmis = InteractionModel.get_user_history(user_id=1, limit=20)
        """
        with get_connection(DB_NAME) as conn:
            rows = conn.execute(
                """SELECT * FROM interactions
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit)
            ).fetchall()
            return [_row_to_interaction(r) for r in rows]


# ==============================================================================
# YARDIMCI FONKSİYONLAR (Özel — dışarıdan çağrılmaz)
# sqlite3.Row → Dataclass dönüşümü
# ==============================================================================

def _row_to_user(row) -> User:
    return User(
        id=row["id"],
        telegram_chat_id=row["telegram_chat_id"],
        username=row["username"],
        full_name=row["full_name"],
        language_code=row["language_code"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"]
    )


def _row_to_news(row) -> News:
    return News(
        id=row["id"],
        source_id=row["source_id"],
        title=row["title"],
        url=row["url"],
        summary=row["summary"],
        raw_text=row["raw_text"],
        category=row["category"],
        is_critical=bool(row["is_critical"]),
        keywords=json.loads(row["keywords"]) if row["keywords"] else [],
        published_at=row["published_at"],
        created_at=row["created_at"]
    )


def _row_to_preference(row) -> UserPreference:
    return UserPreference(
        id=row["id"],
        user_id=row["user_id"],
        topic=row["topic"],
        weight=row["weight"]
    )


def _row_to_interaction(row) -> Interaction:
    return Interaction(
        id=row["id"],
        user_id=row["user_id"],
        news_id=row["news_id"],
        interaction_type=row["interaction_type"],
        session_id=row["session_id"],
        topic=row["topic"] if "topic" in row.keys() else None,
        sentiment=row["sentiment"] if "sentiment" in row.keys() else None,
        created_at=row["created_at"]
    )