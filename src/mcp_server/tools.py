"""
Haber Asistanı - MCP Server Tool Tanımları (v2)
=================================================

Görev:
    LLM (Claude) ile servislerimizi (RSS, Sanitizer, KeywordDetector)
    ve veritabanı modellerimizi birbirine bağlar.

    LLM bir tool'u çağırdığında → bu dosyadaki fonksiyon çalışır →
    servisler/modeller işi yapar → sonuç JSON olarak LLM'e döner.

Mimari Akış:
    ┌──────────┐     MCP Protocol     ┌──────────────┐     Python     ┌──────────────┐
    │  Claude   │ ◄──────────────────► │  tools.py    │ ◄────────────► │  services/   │
    │  (LLM)   │    JSON-RPC          │  (Bu dosya)  │                │  database/   │
    └──────────┘                      └──────────────┘                └──────────────┘
                                            ▲
                                            │ HTTP Webhook
                                            ▼
                                      ┌──────────┐
                                      │   n8n    │
                                      └──────────┘

Mimari Kurallar:
    1. tools.py İÇİNDE doğrudan SQL YAZILMAZ.
       Tüm DB işlemleri models.py üzerinden yapılır (Separation of Concerns).
    2. Toplu işlemler (batch) n8n'in sorumluluğundadır.
       tools.py sadece TEKİL işlemler sunar, n8n bunları döngüyle çağırır.
    3. "Kime gönderilecek?" kararını n8n verir, LLM değil.
       Bu yüzden get_active_users gibi bir tool YOKTUR.
    4. Circular import yoktur.
       server.py → register_tools(app) çağırır, tools.py server.py'yi import etmez.

Senaryo → Tool Eşleşmesi:
    S1 Günlük Bülten    → fetch_news, get_latest_news, get_user_preferences, get_news_detail
    S1.2 Haftalık Seçki  → get_latest_news, get_user_preferences
    S2 Reaktif Sohbet    → search_news, get_news_detail, record_interaction
    S3 Acil Durum        → fetch_news, check_emergency, get_critical_news
    S4 Davranış Analizi  → get_user_history, update_user_preference

Kullanım:
    Bu dosya doğrudan çalıştırılmaz.
    server.py tarafından register_tools(app) ile yüklenir.
"""

import logging
from datetime import datetime
from typing import Optional, List

import httpx

from src.services.rss_reader import RSSReader
from src.services.sanitizer import Sanitizer
from src.services.keyword_detector import KeywordDetector, DetectionResult
from src.database.models import (
    UserModel,
    NewsModel,
    PreferenceModel,
    InteractionModel,
    User,
    News,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# YARDIMCI FONKSİYONLAR (İç Kullanım)
# ==============================================================================
# Tool fonksiyonları JSON-serializable dict döndürmeli.
# Tüm tool'lar tutarlı format kullanır → LLM her zaman ne olduğunu anlar.

def _success(data: dict, message: str = "Başarılı") -> dict:
    """Başarılı sonuç wrapper'ı."""
    return {
        "status": "success",
        "message": message,
        "data": data,
        "timestamp": datetime.now().isoformat()
    }


def _error(message: str, code: str = "GENERAL_ERROR") -> dict:
    """Hata sonuç wrapper'ı."""
    return {
        "status": "error",
        "error_code": code,
        "message": message,
        "timestamp": datetime.now().isoformat()
    }


def _news_to_dict(news: News) -> dict:
    """News dataclass → JSON-serializable dict."""
    return {
        "id": news.id,
        "title": news.title,
        "url": news.url,
        "summary": news.summary,
        "category": news.category,
        "is_critical": news.is_critical,
        "keywords": news.keywords or [],
        "published_at": news.published_at,
        "created_at": news.created_at,
    }


def _user_to_dict(user: User) -> dict:
    """User dataclass → JSON-serializable dict."""
    return {
        "id": user.id,
        "telegram_chat_id": user.telegram_chat_id,
        "username": user.username,
        "full_name": user.full_name,
        "language_code": user.language_code,
        "is_active": user.is_active,
    }


# ==============================================================================
# TOOL KAYIT FONKSİYONU
# ==============================================================================
# server.py bu fonksiyonu çağırarak tüm tool'ları app'e kaydeder.
# Circular import oluşmaz çünkü tools.py, server.py'yi import ETMEZ.

def register_tools(app):
    """
    Tüm MCP tool'larını verilen FastMCP app nesnesine kaydeder.

    Çağrılma şekli (server.py içinde):
        from src.mcp_server.tools import register_tools
        register_tools(app)
    """

    # ==================================================================
    # TOOL 1: HABER ÇEKME (RSS)
    # ==================================================================
    # Senaryolar: S1 Günlük Bülten, S3 Acil Durum
    # n8n tetikler → LLM bu tool'u çağırır → RSS'ten çekilir → DB'ye yazılır

    @app.tool()
    async def fetch_news(category: Optional[str] = None) -> dict:
        """
        RSS kaynaklarından haberleri çeker ve veritabanına kaydeder.

        Bu tool haber toplama sürecinin ilk adımıdır. Tüm aktif RSS
        kaynaklarından (TRT, BBC Türkçe, AA, Bloomberg HT, NTV vb.)
        haberleri eş zamanlı olarak indirir ve veritabanına yazar.

        Args:
            category: Opsiyonel kategori filtresi.
                      Geçerli değerler: "genel", "ekonomi", "acil"
                      Boş bırakılırsa TÜM kategorilerden çeker.

        Returns:
            Çekilen, kaydedilen ve atlanan haber sayıları.

        Kullanım senaryoları:
            - S1: Sabah bülteni için tüm haberleri çek → fetch_news()
            - S3: Acil durum taraması → fetch_news(category="acil")
        """
        try:
            reader = RSSReader()
            result = await reader.fetch_and_save(category=category)

            return _success(
                data={
                    "fetched": result["fetched"],
                    "saved": result["saved"],
                    "skipped": result["skipped"],
                    "category_filter": category or "tümü",
                },
                message=f"{result['fetched']} haber çekildi, "
                        f"{result['saved']} tanesi yeni olarak kaydedildi."
            )

        except Exception as e:
            logger.error(f"fetch_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Haber çekme sırasında hata oluştu: {str(e)}",
                code="RSS_FETCH_ERROR"
            )

    # ==================================================================
    # TOOL 2: SON HABERLERİ GETİR
    # ==================================================================
    # Senaryolar: S1 Günlük Bülten, S1.2 Haftalık Seçki, S2 Reaktif Sohbet

    @app.tool()
    async def get_latest_news(
        limit: int = 20,
        category: Optional[str] = None
    ) -> dict:
        """
        Veritabanındaki en güncel haberleri getirir.

        Haberleri yayınlanma tarihine göre en yeniden eskiye sıralar.
        LLM bu haberleri kullanarak bülten, özet veya yanıt oluşturur.

        Args:
            limit: Kaç haber getirileceği (varsayılan: 20, maks: 100).
            category: Opsiyonel kategori filtresi.
                    Geçerli değerler: "genel", "ekonomi", "acil", "son_dakika"
                    Boş bırakılırsa tüm kategorilerden getirir.

        Returns:
            Haber listesi (id, title, url, summary, category, published_at vb.)
        """
        try:
            limit = max(1, min(limit, 100))
            if category == "son_dakika":
                news_list = NewsModel.get_son_dakika(limit=limit)
            else:
                news_list = NewsModel.get_latest(limit=limit, category=category)

            if not news_list:
                return _success(
                    data={"news": [], "count": 0},
                    message="Belirtilen kriterlere uygun haber bulunamadı."
                )

            news_dicts = [_news_to_dict(n) for n in news_list]

            return _success(
                data={
                    "news": news_dicts,
                    "count": len(news_dicts),
                    "category_filter": category or "tümü",
                },
                message=f"{len(news_dicts)} haber getirildi."
            )

        except Exception as e:
            logger.error(f"get_latest_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Haber getirme sırasında hata oluştu: {str(e)}",
                code="NEWS_FETCH_ERROR"
            )
    # ==================================================================
    # TOOL 3: ACİL DURUM TESPİTİ (Tekil Haber)
    # ==================================================================
    # Senaryo: S3 Priority Watcher
    # n8n haberleri tek tek gönderir → LLM bu tool ile kontrol eder

    @app.tool()
    async def check_emergency(
        text: str,
        title: Optional[str] = None,
        news_id: Optional[int] = None
    ) -> dict:
        """
        Bir haber metnini acil durum anahtar kelimeleri için analiz eder.

        Deprem, sel, yangın, terör, salgın gibi kritik olayları tespit etmek
        için ağırlıklı anahtar kelime skoru hesaplar. Skor eşik değerini
        (0.9) aşarsa haber kritik olarak işaretlenir.

        Args:
            text: Analiz edilecek haber metni.
            title: Haber başlığı. Başlıktaki kelimeler 2x ağırlıkla değerlendirilir.
            news_id: Haberin veritabanı ID'si. Verilirse kritik tespit durumunda
                     DB'deki is_critical alanı otomatik güncellenir.

        Returns:
            Acil durum analiz sonucu: is_critical, score, category, matched_keywords.
        """
        try:
            if not text or not text.strip():
                return _error(
                    message="Analiz edilecek metin boş olamaz.",
                    code="EMPTY_TEXT"
                )

            result: DetectionResult = KeywordDetector.analyze(
                text=text, title=title
            )

            # Kritik tespit + news_id varsa → models.py üzerinden DB güncelle
            # ✅ Doğrudan SQL YOK — Separation of Concerns korunuyor
            if result.is_critical and news_id:
                try:
                    NewsModel.mark_as_critical(
                        news_id=news_id,
                        category=result.category,
                        keywords=result.matched_keywords
                    )
                except Exception as db_err:
                    logger.error(
                        f"DB güncelleme hatası (news_id={news_id}): {db_err}"
                    )

            return _success(
                data={
                    "is_critical": result.is_critical,
                    "score": result.score,
                    "category": result.category,
                    "matched_keywords": result.matched_keywords,
                    "category_scores": result.category_scores,
                    "explanation": result.explanation,
                    "threshold": KeywordDetector.get_threshold(),
                    "news_id": news_id,
                },
                message=(
                    f"ACİL DURUM TESPİT EDİLDİ! "
                    f"Kategori: {result.category}, Skor: {result.score}"
                    if result.is_critical
                    else f"Acil durum tespit edilmedi. "
                         f"En yüksek skor: {result.score}"
                )
            )

        except Exception as e:
            logger.error(f"check_emergency hatası: {e}", exc_info=True)
            return _error(
                message=f"Acil durum analizi sırasında hata: {str(e)}",
                code="EMERGENCY_CHECK_ERROR"
            )

    # ==================================================================
    # TOOL 4: KRİTİK HABERLERİ GETİR
    # ==================================================================
    # Senaryolar: S3 bildirim sonrası, S2 Reaktif Sohbet

    @app.tool()
    async def get_critical_news(limit: int = 10) -> dict:
        """
        Veritabanındaki acil/kritik işaretli haberleri getirir.

        is_critical=1 olan haberler, KeywordDetector tarafından deprem,
        sel, yangın, terör veya salgın olarak tespit edilmiş haberlerdir.

        Args:
            limit: Kaç kritik haber getirileceği (varsayılan: 10, maks: 50).

        Returns:
            Kritik haber listesi.
        """
        try:
            limit = max(1, min(limit, 50))
            critical_list = NewsModel.get_critical(limit=limit)

            if not critical_list:
                return _success(
                    data={"news": [], "count": 0},
                    message="Şu anda kritik/acil haber bulunmuyor."
                )

            news_dicts = [_news_to_dict(n) for n in critical_list]

            return _success(
                data={"news": news_dicts, "count": len(news_dicts)},
                message=f"{len(news_dicts)} kritik haber bulundu."
            )

        except Exception as e:
            logger.error(f"get_critical_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Kritik haber getirme sırasında hata: {str(e)}",
                code="CRITICAL_NEWS_ERROR"
            )

    # ==================================================================
    # TOOL 5: HABER DETAYI GETİR (URL → Temiz Metin)
    # ==================================================================
    # Senaryolar: S2 Reaktif Sohbet, S1 Bülten hazırlama
    # n8n tek tek URL gönderir → LLM bu tool ile tam metni çeker

    @app.tool()
    async def get_news_detail(
        url: str,
        save_to_db: bool = True
    ) -> dict:
        """
        Bir haber URL'sinden tam içeriği çeker ve temizler.

        Trafilatura ile sayfayı indirir, reklam/menü/footer'ı temizler ve
        sadece saf haber metnini döndürür. LLM bu metni kullanarak
        kullanıcıya detaylı özet ve analiz sunabilir.

        Args:
            url: Haber URL'si (https:// ile başlamalı).
            save_to_db: True ise temizlenmiş metin veritabanına da kaydedilir.

        Returns:
            Temizlenmiş haber metni, başlık, yazar, kelime sayısı.
        """
        try:
            if not url or not url.startswith(("http://", "https://")):
                return _error(
                    message="Geçerli bir URL giriniz "
                            "(http:// veya https:// ile başlamalı).",
                    code="INVALID_URL"
                )

            content = await Sanitizer.clean_url(url)

            if not content:
                return _error(
                    message=f"URL'den içerik çıkarılamadı: {url}",
                    code="CONTENT_EXTRACTION_FAILED"
                )

            is_sufficient = Sanitizer.is_content_sufficient(
                content, min_words=50
            )

            # DB güncelleme — models.py üzerinden
            if save_to_db:
                existing = NewsModel.get_by_url(url)
                if existing and content.text:
                    NewsModel.update_summary(
                        existing.id, content.text[:500]
                    )

            return _success(
                data={
                    "url": url,
                    "text": content.text,
                    "title": content.title,
                    "author": content.author,
                    "date": content.date,
                    "language": content.language,
                    "word_count": content.word_count,
                    "is_sufficient": is_sufficient,
                },
                message=f"İçerik başarıyla çıkarıldı "
                        f"({content.word_count} kelime)."
            )

        except Exception as e:
            logger.error(f"get_news_detail hatası ({url}): {e}", exc_info=True)
            return _error(
                message=f"Haber detayı alınırken hata: {str(e)}",
                code="DETAIL_FETCH_ERROR"
            )

    # ==================================================================
    # TOOL 6: HABER ARA
    # ==================================================================
    # Senaryo: S2 Reaktif Sohbet

    @app.tool()
    async def search_news(
        query: str,
        limit: int = 10
    ) -> dict:
        """
        Veritabanında haber araması yapar.

        Başlık, özet ve anahtar kelimeler üzerinde metin araması yapar.
        Kullanıcının sohbet sırasında belirli konulardaki haberlere
        ulaşmasını sağlar.

        Args:
            query: Aranacak kelime veya ifade (örn: "deprem", "dolar kuru").
            limit: Maksimum sonuç sayısı (varsayılan: 10, maks: 50).

        Returns:
            Eşleşen haberlerin listesi.
        """
        try:
            if not query or not query.strip():
                return _error(
                    message="Arama sorgusu boş olamaz.",
                    code="EMPTY_QUERY"
                )

            limit = max(1, min(limit, 50))

            # ✅ Doğrudan SQL YOK — NewsModel.search() kullanılıyor
            news_list = NewsModel.search(query=query, limit=limit)

            if not news_list:
                return _success(
                    data={"news": [], "count": 0, "query": query},
                    message=f"'{query}' ile eşleşen haber bulunamadı."
                )

            news_dicts = [_news_to_dict(n) for n in news_list]

            return _success(
                data={
                    "news": news_dicts,
                    "count": len(news_dicts),
                    "query": query,
                },
                message=f"'{query}' araması için "
                        f"{len(news_dicts)} sonuç bulundu."
            )

        except Exception as e:
            logger.error(f"search_news hatası ({query}): {e}", exc_info=True)
            return _error(
                message=f"Haber araması sırasında hata: {str(e)}",
                code="SEARCH_ERROR"
            )

    # ==================================================================
    # TOOL 7: KULLANICI YÖNETİMİ
    # ==================================================================
    # Senaryolar: Tümü (n8n her Telegram mesajında chat_id gönderir)

    @app.tool()
    async def get_or_create_user(
        telegram_chat_id: str,
        username: Optional[str] = None,
        full_name: Optional[str] = None
    ) -> dict:
        """
        Telegram kullanıcısını veritabanında bulur veya yeni kayıt oluşturur.

        n8n'den gelen her Telegram mesajında çağrılır. Kullanıcı daha önce
        kayıtlıysa mevcut bilgileri döner, değilse yeni kayıt oluşturur.

        Args:
            telegram_chat_id: Telegram chat ID (zorunlu, benzersiz).
            username: Telegram kullanıcı adı (opsiyonel).
            full_name: Kullanıcının tam adı (opsiyonel).

        Returns:
            Kullanıcı bilgileri.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            user = UserModel.get_or_create(
                telegram_chat_id=telegram_chat_id.strip(),
                username=username,
                full_name=full_name
            )

            try:
                created = datetime.fromisoformat(user.created_at.replace(' ', 'T'))
                last_seen = datetime.fromisoformat(user.last_seen_at.replace(' ', 'T'))
                diff_seconds = (last_seen - created).total_seconds()
                is_new_user = diff_seconds < 10
            except Exception:
                is_new_user = False

            return _success(
                data={
                    "user": _user_to_dict(user),
                    "is_new_user": is_new_user
                },
                message=f"Kullanıcı hazır: "
                        f"{user.full_name or user.username or user.telegram_chat_id}"
            )

        except Exception as e:
            logger.error(f"get_or_create_user hatası: {e}", exc_info=True)
            return _error(
                message=f"Kullanıcı işlemi sırasında hata: {str(e)}",
                code="USER_ERROR"
            )

    # ==================================================================
    # TOOL 7.5: AKTİF KULLANICILARI GETİR
    # ==================================================================
    # Senaryo: S1 Günlük Bülten, S1.2 Haftalık Seçki
    # n8n zamanlayıcı tetikler → tüm aktif kullanıcılara bülten gönderilir

    @app.tool()
    async def get_active_users() -> dict:
        """
        Sistemdeki tüm aktif kullanıcıları getirir.

        S1 Günlük Bülten ve S1.2 Haftalık Seçki senaryolarında
        n8n bu tool'u çağırarak bülten gönderilecek kullanıcı
        listesini alır.

        Returns:
            Aktif kullanıcı listesi (id, telegram_chat_id, username, full_name)
        """
        try:
            users = UserModel.get_all_active()

            if not users:
                return _success(
                    data={"users": [], "count": 0},
                    message="Aktif kullanıcı bulunamadı."
                )

            users_list = [_user_to_dict(u) for u in users]

            return _success(
                data={
                    "users": users_list,
                    "count": len(users_list)
                },
                message=f"{len(users_list)} aktif kullanıcı bulundu."
            )

        except Exception as e:
            logger.error(f"get_active_users hatası: {e}", exc_info=True)
            return _error(
                message=f"Kullanıcı listesi alınırken hata: {str(e)}",
                code="GET_USERS_ERROR"
            )

    # ==================================================================
    # TOOL 8: KULLANICI TERCİHLERİ GETİR
    # ==================================================================
    # Senaryolar: S1 Günlük Bülten, S1.2 Haftalık Seçki

    @app.tool()
    async def get_user_preferences(telegram_chat_id: str) -> dict:
        """
        Kullanıcının kişisel ilgi alanı profilini getirir.

        Her kullanıcının konulara verdiği ağırlıklar (0.0-10.0) döner.
        LLM bu bilgiyi kullanarak haberleri kişiselleştirir:
        yüksek ağırlıklı konular öne çıkar, düşükler filtrelenir.

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.

        Returns:
            İlgi alanı listesi: [{topic, weight}, ...] ağırlık sırasıyla.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            preferences = PreferenceModel.get_user_profile(user_id=user.id)
            prefs_list = [
                {"topic": p.topic, "weight": p.weight}
                for p in preferences
            ]

            return _success(
                data={
                    "user_id": user.id,
                    "telegram_chat_id": telegram_chat_id,
                    "full_name": user.full_name,
                    "created_at": user.created_at[:10] if user.created_at else None,
                    "preferences": prefs_list,
                    "preference_count": len(prefs_list),
                },
                message=(
                    f"{len(prefs_list)} ilgi alanı bulundu."
                    if prefs_list
                    else "Henüz ilgi alanı profili oluşturulmamış. "
                        "Etkileşimlerle otomatik oluşacak."
                )
)

        except Exception as e:
            logger.error(f"get_user_preferences hatası: {e}", exc_info=True)
            return _error(
                message=f"Tercih bilgisi alınırken hata: {str(e)}",
                code="PREFERENCE_ERROR"
            )
        # ==================================================================
    # TOOL 8.5: KULLANICI TAM PROFİLİ (Profilim Ekranı için)
    # ==================================================================
    # Senaryo: S2 Profilim Butonu
    # Tek çağrıda tüm profil verisini döner: tercihler, istatistikler,
    # son etkileşimler, trendler.

    @app.tool()
    async def get_user_full_profile(telegram_chat_id: str) -> dict:
        """
        Kullanıcının zenginleştirilmiş profil verisini tek seferde getirir.

        Tercihler, etkileşim istatistikleri, son etkileşimler ve trend
        analizi içerir. "Profilim" ekranı için özel hazırlanmıştır.

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.

        Returns:
            Tam profil: kullanıcı bilgisi, tercihler, istatistikler,
            son etkileşimler, trend analizi, üyelik süresi.
        """
        try:
            from src.database.setup import get_connection
            from src.config.settings import DB_PATH
            from datetime import datetime, timedelta

            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            # Tercihler (ağırlığa göre sıralı)
            preferences = PreferenceModel.get_user_profile(user_id=user.id)
            prefs_list = [
                {"topic": p.topic, "weight": p.weight}
                for p in preferences
            ]

            with get_connection(DB_PATH) as conn:
                # Toplam etkileşim
                total_interactions = conn.execute(
                    "SELECT COUNT(*) as c FROM interactions WHERE user_id = ?",
                    (user.id,)
                ).fetchone()["c"]

                # Bu haftaki etkileşim (son 7 gün)
                week_ago = (datetime.now() - timedelta(days=7)).isoformat()
                weekly_interactions = conn.execute(
                    """SELECT COUNT(*) as c FROM interactions
                       WHERE user_id = ? AND created_at >= ?""",
                    (user.id, week_ago)
                ).fetchone()["c"]

                # En aktif gün (haftanın günü)
                day_rows = conn.execute(
                    """SELECT strftime('%w', created_at) as day, COUNT(*) as c
                       FROM interactions WHERE user_id = ?
                       GROUP BY day ORDER BY c DESC LIMIT 1""",
                    (user.id,)
                ).fetchone()
                day_names = ["Pazar", "Pazartesi", "Salı", "Çarşamba",
                             "Perşembe", "Cuma", "Cumartesi"]
                most_active_day = (
                    day_names[int(day_rows["day"])] if day_rows else None
                )

                # Son 3 etkileşim (haber başlığı + topic + zamanı)
                recent_rows = conn.execute(
                    """SELECT i.created_at, i.topic, n.title
                       FROM interactions i
                       LEFT JOIN news_logs n ON i.news_id = n.id
                       WHERE i.user_id = ?
                       ORDER BY i.created_at DESC LIMIT 3""",
                    (user.id,)
                ).fetchall()
                recent_interactions = [
                    {
                        "title": r["title"],
                        "topic": r["topic"],
                        "created_at": r["created_at"]
                    }
                    for r in recent_rows
                ]

                # Son 7 günde en çok etkileşilen topic (trend)
                trending_row = conn.execute(
                    """SELECT topic, COUNT(*) as c FROM interactions
                       WHERE user_id = ? AND created_at >= ?
                         AND topic IS NOT NULL
                       GROUP BY topic ORDER BY c DESC LIMIT 1""",
                    (user.id, week_ago)
                ).fetchone()
                trending_topic = trending_row["topic"] if trending_row else None

            # Üyelik süresi (gün)
            membership_days = None
            if user.created_at:
                created = datetime.fromisoformat(user.created_at.replace(' ', 'T'))
                membership_days = (datetime.now() - created).days

            return _success(
                data={
                    "user": {
                        "id": user.id,
                        "full_name": user.full_name,
                        "username": user.username,
                        "telegram_chat_id": user.telegram_chat_id,
                        "created_at": user.created_at,
                        "membership_days": membership_days,
                    },
                    "preferences": prefs_list,
                    "stats": {
                        "total_interactions": total_interactions,
                        "weekly_interactions": weekly_interactions,
                        "most_active_day": most_active_day,
                    },
                    "recent_interactions": recent_interactions,
                    "trending_topic": trending_topic,
                },
                message=f"Profil bilgileri hazırlandı."
            )

        except Exception as e:
            logger.error(f"get_user_full_profile hatası: {e}", exc_info=True)
            return _error(
                message=f"Profil alınırken hata: {str(e)}",
                code="PROFILE_ERROR"
            )

    # ==================================================================
    # TOOL 9: KULLANICI TERCİH GÜNCELLE
    # ==================================================================
    # Senaryo: S4 Davranışsal Analiz

    @app.tool()
    async def update_user_preference(
        telegram_chat_id: str,
        topic: str,
        delta: float,
        dismissed: bool = False
    ) -> dict:
        """
        Kullanıcının bir konudaki ilgi ağırlığını günceller.

        Beğeni (dismissed=False):
            - Kategori DB'de yoksa → 0.5 ile ekle
            - Kategori DB'de varsa → mevcut ağırlık + delta (max 10.0)

        Beğenmeme (dismissed=True):
            - Kategori DB'de yoksa → 0.0 ile ekle
            - Kategori DB'de varsa → direkt -1.0 yap

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.
            topic: Güncellenecek konu (örn: "teknoloji", "ekonomi").
            delta: Beğeni için +0.5, beğenmeme için kullanılmaz (dismissed=True ile -1 uygulanır).
            dismissed: True ise beğenmeme mantığı uygulanır.

        Returns:
            Güncellenmiş tercih bilgisi.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )
            if not topic or not topic.strip():
                return _error(
                    message="topic zorunludur.",
                    code="MISSING_TOPIC"
                )
            if not isinstance(delta, (int, float)):
                return _error(
                    message="delta sayısal bir değer olmalıdır.",
                    code="INVALID_DELTA"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            updated = PreferenceModel.update_weight(
                user_id=user.id,
                topic=topic.strip().lower(),
                delta=float(delta),
                dismissed=dismissed
            )

            return _success(
                data={
                    "user_id": user.id,
                    "topic": updated.topic,
                    "new_weight": updated.weight,
                    "delta_applied": delta,
                    "dismissed": dismissed,
                },
                message=f"'{updated.topic}' konusu güncellendi: "
                        f"ağırlık = {updated.weight:.1f}"
            )

        except Exception as e:
            logger.error(f"update_user_preference hatası: {e}", exc_info=True)
            return _error(
                message=f"Tercih güncelleme sırasında hata: {str(e)}",
                code="PREFERENCE_UPDATE_ERROR"
            )

    # ==================================================================
    # TOOL 9.5: İLK TERCİHLERİ TOPLU KAYDET
    # ==================================================================
    # Senaryo: S0 Onboarding
    # Kullanıcı inline butonlarla kategori seçer → n8n bu tool'u çağırır
    # Seçilen kategoriler 5.0, seçilmeyenler 0.0 ağırlıkla kaydedilir
    # S4 Davranışsal Analiz zamanla bu ağırlıkları ayarlar

    VALID_TOPICS = {
        "siyaset", "magazin", "ekonomi", "spor",
        "bilim", "teknoloji", "sağlık", "yemek",
        "otomotiv", "müzik", "dizi_film", "ai"
    }

    @app.tool()
    async def set_initial_preferences(
        telegram_chat_id: str,
        selected_topics: list
    ) -> dict:
        """
        Kullanıcının onboarding sırasında seçtiği ilk tercihleri toplu kaydeder.

        Sadece S0 Onboarding senaryosunda kullanılır. Telegram inline
        butonlarıyla seçilen kategoriler 5.0 başlangıç ağırlığıyla,
        seçilmeyen tüm kategoriler ise 0.0 ağırlıkla veritabanına yazılır.
        Böylece S4 Davranışsal Analiz tüm kategoriler üzerinde delta
        uygulayabilir — kullanıcının ilgi alanı zamanla değişebilir.

        Daha önce tercih kaydı varsa üzerine yazar (upsert).

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.
            selected_topics: Kullanıcının seçtiği konu listesi.
                Geçerli değerler: "siyaset", "magazin", "ekonomi", "spor",
                "bilim", "teknoloji", "sağlık", "yemek", 
                "otomotiv", "müzik", "dizi_film", "ai"

        Returns:
            Kaydedilen tüm tercihler, seçilen ve seçilmeyen sayıları.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            if not isinstance(selected_topics, list):
                return _error(
                    message="selected_topics bir liste olmalıdır.",
                    code="INVALID_TOPICS_FORMAT"
                )

            # Geçersiz kategori kontrolü
            normalized = [t.strip().lower() for t in selected_topics]
            invalid = [t for t in normalized if t not in VALID_TOPICS]
            if invalid:
                return _error(
                    message=f"Geçersiz kategori(ler): {', '.join(invalid)}. "
                            f"Geçerli değerler: {', '.join(sorted(VALID_TOPICS))}",
                    code="INVALID_TOPIC"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            saved = []
            for topic in VALID_TOPICS:
                if topic in normalized:
                    # Seçilen kategori → +0.5 delta uygula (yoksa oluşturur)
                    updated = PreferenceModel.update_weight(
                        user_id=user.id,
                        topic=topic,
                        delta=0.5
                    )
                    saved.append({
                        "topic": topic,
                        "weight": updated.weight,
                        "selected": True
                    })
                else:
                    # Seçilmeyen kategori → DB'den sil (varsa)
                    prefs = PreferenceModel.get_user_profile(user_id=user.id)
                    exists = next((p for p in prefs if p.topic == topic), None)
                    if exists:
                        PreferenceModel.delete(user_id=user.id, topic=topic)
                        logger.info(f"Kullanıcı {user.id} için '{topic}' tercihi silindi.")

            selected_count = len(normalized)
            unselected_count = len(VALID_TOPICS) - selected_count

            return _success(
                data={
                    "user_id": user.id,
                    "preferences": saved,
                    "selected_count": selected_count,
                    "unselected_count": unselected_count,
                    "selected_topics": normalized,
                },
                message=f"Tercihler güncellendi: {selected_count} kategori +0.5 artırıldı."
            )
        except Exception as e:
            logger.error(f"set_initial_preferences hatası: {e}", exc_info=True)
            return _error(
                message=f"İlk tercih kayıt sırasında hata: {str(e)}",
                code="INITIAL_PREFERENCE_ERROR"
            )

    # ==================================================================
    # TOOL 10: ETKİLEŞİM KAYDET
    # ==================================================================
    # Senaryolar: S2 Reaktif Sohbet, S4 Davranışsal Analiz (feedback loop)

    @app.tool()
    async def record_interaction(
        telegram_chat_id: str,
        news_id: int,
        interaction_type: str,
        session_id: Optional[str] = None,
        topic: Optional[str] = None,
        sentiment: Optional[str] = None
    ) -> dict:
        """
        Kullanıcının bir haberle olan etkileşimini kaydeder.

        Her etkileşim S4 Davranışsal Analiz için veri oluşturur.
        Zamanla bu veriler kullanıcının ilgi profilini şekillendirir.

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.
            news_id: Etkileşim yapılan haberin veritabanı ID'si.
            interaction_type: Etkileşim türü. Geçerli değerler:
                "read", "liked", "dismissed", "asked_question", "shared"
            session_id: Opsiyonel oturum ID'si.
            topic: Etkileşimin konusu (örn: "ekonomi", "spor").
            sentiment: Duygu analizi ("positive", "negative", "neutral").

        Returns:
            Kaydedilen etkileşim bilgisi.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            valid_types = {
                "read", "liked", "dismissed",
                "asked_question", "shared"
            }
            if interaction_type not in valid_types:
                return _error(
                    message=f"Geçersiz etkileşim türü: '{interaction_type}'. "
                            f"Geçerli: {', '.join(sorted(valid_types))}",
                    code="INVALID_INTERACTION_TYPE"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            # Geçerli kategoriler listesi
            VALID_CATEGORIES = {
                "siyaset", "magazin", "ekonomi", "spor",
                "bilim", "teknoloji", "sağlık", "yemek",
                "otomotiv", "müzik", "dizi_film", "ai"
            }

            # topic geçerli kategorilerden değilse "genel" yap
            normalized_topic = topic.strip().lower() if topic else None
            if normalized_topic and normalized_topic not in VALID_CATEGORIES:
                logger.info(
                    f"topic '{normalized_topic}' geçerli kategori değil, "
                    f"'genel' olarak kaydediliyor."
                )
                normalized_topic = "genel"

            interaction_id = InteractionModel.save(
                user_id=user.id,
                news_id=news_id,
                interaction_type=interaction_type,
                session_id=session_id,
                topic=normalized_topic,
                sentiment=sentiment
            )

            return _success(
                data={
                    "interaction_id": interaction_id,
                    "user_id": user.id,
                    "news_id": news_id,
                    "interaction_type": interaction_type,
                    "session_id": session_id,
                    "topic": topic,
                    "sentiment": sentiment,
                },
                message=f"Etkileşim kaydedildi: "
                        f"{interaction_type} (haber #{news_id})"
            )

        except Exception as e:
            logger.error(f"record_interaction hatası: {e}", exc_info=True)
            return _error(
                message=f"Etkileşim kaydetme sırasında hata: {str(e)}",
                code="INTERACTION_ERROR"
            )

    # ==================================================================
    # TOOL 11: KULLANICI ETKİLEŞİM GEÇMİŞİ
    # ==================================================================
    # Senaryo: S4 Davranışsal Analiz

    @app.tool()
    async def get_user_history(
        telegram_chat_id: str,
        limit: int = 50
    ) -> dict:
        """
        Kullanıcının haber etkileşim geçmişini getirir.

        S4 Davranışsal Analiz senaryosunda LLM bu veriyi okur ve
        kullanıcının hangi konulara ilgi gösterdiğini analiz eder.

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.
            limit: Kaç etkileşim getirileceği (varsayılan: 50, maks: 200).

        Returns:
            Etkileşim listesi ve istatistik özeti.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            limit = max(1, min(limit, 200))

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            interactions = InteractionModel.get_user_history(
                user_id=user.id, limit=limit
            )

            history = [
                {
                    "id": i.id,
                    "news_id": i.news_id,
                    "interaction_type": i.interaction_type,
                    "session_id": i.session_id,
                    "created_at": i.created_at,
                }
                for i in interactions
            ]

            # LLM'in analizini kolaylaştıracak istatistik özeti
            type_counts = {}
            for h in history:
                t = h["interaction_type"]
                type_counts[t] = type_counts.get(t, 0) + 1

            return _success(
                data={
                    "user_id": user.id,
                    "history": history,
                    "total_interactions": len(history),
                    "interaction_summary": type_counts,
                },
                message=f"{len(history)} etkileşim getirildi."
            )

        except Exception as e:
            logger.error(f"get_user_history hatası: {e}", exc_info=True)
            return _error(
                message=f"Etkileşim geçmişi alınırken hata: {str(e)}",
                code="HISTORY_ERROR"
            )

    # ==================================================================
    # TOOL 12: KİŞİSELLEŞTİRİLMİŞ HABER GETİR
    # ==================================================================
    # Senaryolar: S1.1 Günlük Bülten
    # Kullanıcının seçtiği kategorilerden haberler getirir.
    # Tercih yoksa tüm kategorilerden karışık haber döner.

    @app.tool()
    async def get_personalized_news(
        telegram_chat_id: str,
        limit_per_category: int = 5
    ) -> dict:
        """
        Kullanıcının tercihlerine göre kişiselleştirilmiş haberler getirir.
        
        Eğer kullanıcının hiç tercihi yoksa (yeni kullanıcı), 
        tüm kategorilerden karışık haber döner.

        Args:
            telegram_chat_id: Kullanıcının Telegram chat ID'si.
            limit_per_category: Her kategoriden kaç haber getirileceği (varsayılan: 5)

        Returns:
            Kişiselleştirilmiş haber listesi.
        """
        try:
            if not telegram_chat_id or not telegram_chat_id.strip():
                return _error(
                    message="telegram_chat_id zorunludur.",
                    code="MISSING_CHAT_ID"
                )

            user = UserModel.get_by_chat_id(telegram_chat_id.strip())
            if not user:
                return _error(
                    message=f"Kullanıcı bulunamadı: {telegram_chat_id}",
                    code="USER_NOT_FOUND"
                )

            # Kullanıcı tercihlerini al
            preferences = PreferenceModel.get_user_profile(user_id=user.id)

            all_news = []
            
            if not preferences or len(preferences) == 0:
                # Tercih YOK → Tüm kategorilerden karışık haber
                all_news = NewsModel.get_latest(limit=20, category=None)
                has_preferences = False
                logger.info(f"Kullanıcı {user.id} tercihi yok, karışık haber gönderiliyor.")
            else:
                # Tercihler VAR → Her kategoriden limit_per_category kadar
                for pref in preferences:
                    category_news = NewsModel.get_latest(
                        limit=limit_per_category,
                        category=pref.topic
                    )
                    all_news.extend(category_news)
                has_preferences = True
                logger.info(
                    f"Kullanıcı {user.id} için {len(preferences)} kategoriden "
                    f"toplam {len(all_news)} haber getirildi."
                )

            news_dicts = [_news_to_dict(n) for n in all_news]

            return _success(
                data={
                    "news": news_dicts,
                    "count": len(news_dicts),
                    "has_preferences": has_preferences,
                    "categories_count": len(preferences) if has_preferences else 0
                },
                message=f"{len(news_dicts)} kişiselleştirilmiş haber getirildi."
            )

        except Exception as e:
            logger.error(f"get_personalized_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Kişiselleştirilmiş haber getirme sırasında hata: {str(e)}",
                code="PERSONALIZED_NEWS_ERROR"
            )

    # ==================================================================
    # TOOL 13: SİSTEM SAĞLIK KONTROLÜ
    # ==================================================================
    # Kullanım: n8n health check, hata ayıklama

    @app.tool()
    async def system_health_check() -> dict:
        """
        Sistemin genel sağlık durumunu raporlar.

        Veritabanı bağlantısı, tablo durumları, RSS kaynak sayısı
        ve son işlem zamanları gibi bilgileri döndürür.

        Returns:
            Sistem durumu raporu.
        """
        try:
            from src.database.setup import get_connection
            from src.config.settings import DB_PATH

            health = {
                "database": "unknown",
                "tables": {},
                "rss_sources": 0,
                "last_news_at": None,
            }

            # DB bağlantı testi
            try:
                with get_connection(DB_PATH) as conn:
                    health["database"] = "connected"

                    for table in [
                        "users", "news_logs",
                        "user_preferences", "interactions"
                    ]:
                        try:
                            count = conn.execute(
                                f"SELECT COUNT(*) as c FROM {table}"
                            ).fetchone()["c"]
                            health["tables"][table] = count
                        except Exception:
                            health["tables"][table] = "error"

                    try:
                        row = conn.execute(
                            "SELECT MAX(created_at) as last FROM news_logs"
                        ).fetchone()
                        health["last_news_at"] = (
                            row["last"] if row else None
                        )
                    except Exception:
                        pass

            except Exception as db_err:
                health["database"] = f"error: {str(db_err)}"

            # RSS kaynak sayısı
            try:
                reader = RSSReader()
                health["rss_sources"] = len(reader.sources)
            except Exception:
                health["rss_sources"] = "error"

            health["server_time"] = datetime.now().isoformat()
            health["keyword_threshold"] = KeywordDetector.get_threshold()

            return _success(
                data=health,
                message="Sistem sağlık raporu hazır."
            )

        except Exception as e:
            logger.error(f"system_health_check hatası: {e}", exc_info=True)
            return _error(
                message=f"Sistem kontrolü sırasında hata: {str(e)}",
                code="HEALTH_CHECK_ERROR"
            )
        # ==================================================================
# TOOL 14: BÜLTEN HABERLERİNİ KAYDET
# ==================================================================
    @app.tool()
    async def save_bulletin_news(
        user_chat_id: str,
        news_list: list
    ) -> dict:
        """
        Bülten haberlerini DB'ye kaydeder.
        S1.1 workflow'u bülten gönderdikten sonra bu tool'u çağırır.
        """
        try:
            from src.database.setup import get_connection
            from src.config.settings import DB_PATH

            with get_connection(DB_PATH) as conn:
                # Önce bu kullanıcının eski bülten verilerini sil
                conn.execute(
                    "DELETE FROM bulletin_news WHERE user_chat_id = ?",
                    (user_chat_id,)
                )
                # Yeni haberleri kaydet
                for i, news in enumerate(news_list, 1):
                    conn.execute(
                        """INSERT INTO bulletin_news 
                        (user_chat_id, news_index, news_id, title, category, url)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            user_chat_id,
                            i,
                            news.get("id"),
                            news.get("title"),
                            news.get("category"),
                            news.get("url")
                        )
                    )

            return _success(
                data={"saved": len(news_list)},
                message=f"{len(news_list)} bülten haberi kaydedildi."
            )

        except Exception as e:
            logger.error(f"save_bulletin_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Bülten haberleri kaydedilirken hata: {str(e)}",
                code="BULLETIN_SAVE_ERROR"
            )

    # ==================================================================
    # TOOL 15: BÜLTENDEKİ HABERİ GETİR
    # ==================================================================
    @app.tool()
    async def get_bulletin_news(
        user_chat_id: str,
        news_index: int
    ) -> dict:
        """
        Kullanıcının son bültenindeki belirli sıradaki haberi getirir.
        S2 AI Agent numara ile haber istendiğinde bu tool'u çağırır.
        """
        try:
            from src.database.setup import get_connection
            from src.config.settings import DB_PATH

            with get_connection(DB_PATH) as conn:
                row = conn.execute(
                    """SELECT * FROM bulletin_news 
                    WHERE user_chat_id = ? AND news_index = ?
                    ORDER BY created_at DESC LIMIT 1""",
                    (user_chat_id, news_index)
                ).fetchone()

                if not row:
                    return _error(
                        message=f"{news_index}. sırada bülten haberi bulunamadı.",
                        code="BULLETIN_NEWS_NOT_FOUND"
                    )

                return _success(
                    data={
                        "news_index": row["news_index"],
                        "news_id": row["news_id"],
                        "title": row["title"],
                        "category": row["category"],
                        "url": row["url"]
                    },
                    message=f"{news_index}. bülten haberi getirildi."
                )

        except Exception as e:
            logger.error(f"get_bulletin_news hatası: {e}", exc_info=True)
            return _error(
                message=f"Bülten haberi getirilirken hata: {str(e)}",
                code="BULLETIN_GET_ERROR"
            )
        # ==================================================================
    # TOOL 16: KULLANICININ TÜM BÜLTEN HABERLERİNİ GETİR
    # ==================================================================
    @app.tool()
    async def get_user_bulletin(user_chat_id: str) -> dict:
        """
        Kullanıcının en son bülten haberlerini sırayla getirir.
        Günün Haberleri butonu için kullanılır. AI Agent kullanmaz.
        """
        try:
            from src.database.setup import get_connection
            from src.config.settings import DB_PATH

            with get_connection(DB_PATH) as conn:
                rows = conn.execute(
                    """SELECT news_index, news_id, title, category, url 
                       FROM bulletin_news 
                       WHERE user_chat_id = ?
                       ORDER BY created_at DESC, news_index ASC
                       LIMIT 6""",
                    (user_chat_id,)
                ).fetchall()

                if not rows:
                    return _error(
                        message="Henüz günlük bülten oluşturulmamış.",
                        code="NO_BULLETIN"
                    )

                news_list = [
                    {
                        "news_index": row["news_index"],
                        "news_id": row["news_id"],
                        "title": row["title"],
                        "category": row["category"],
                        "url": row["url"]
                    }
                    for row in rows
                ]

                return _success(
                    data={"news": news_list, "count": len(news_list)},
                    message=f"{len(news_list)} bülten haberi getirildi."
                )

        except Exception as e:
            logger.error(f"get_user_bulletin hatası: {e}", exc_info=True)
            return _error(
                message=f"Bülten haberleri getirilirken hata: {str(e)}",
                code="BULLETIN_FETCH_ERROR"
            )
    # ==================================================================
    # TOOL 17: WEB ARAMASI
    # ==================================================================
    @app.tool()
    async def search_web(query: str, max_results: int = 5) -> dict:
        """
        Veritabanında bulunamayan konular için Google'da web araması yapar.
        Kullanıcı güncel spor sonuçları, hava durumu, fiyat gibi
        DB'de olmayan bilgiler sorduğunda kullanılır.

        Args:
            query: Aranacak kelime veya cümle (Türkçe olabilir).
            max_results: Maksimum sonuç sayısı (varsayılan: 5).

        Returns:
            Arama sonuçları listesi (başlık, link, özet).
        """
        try:
            from src.config.settings import SERPER_API_KEY

            if not SERPER_API_KEY:
                return _error(
                    message="Serper API key bulunamadı.",
                    code="SERPER_KEY_MISSING"
                )

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": SERPER_API_KEY,
                        "Content-Type": "application/json"
                    },
                    json={
                        "q": query,
                        "gl": "tr",
                        "hl": "tr",
                        "num": max_results
                    }
                )
                response.raise_for_status()
                data = response.json()

                results = []
                for item in data.get("organic", [])[:max_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "link": item.get("link", ""),
                        "snippet": item.get("snippet", "")
                    })

                # Answer box varsa ekle (direkt cevap)
                answer = data.get("answerBox", {}).get("answer", "")
                snippet = data.get("answerBox", {}).get("snippet", "")

                return _success(
                    data={
                        "results": results,
                        "count": len(results),
                        "query": query,
                        "direct_answer": answer or snippet or None
                    },
                    message=f"'{query}' için {len(results)} sonuç bulundu."
                )

        except Exception as e:
            logger.error(f"search_web hatası: {e}", exc_info=True)
            return _error(
                message=f"Web araması sırasında hata: {str(e)}",
                code="WEB_SEARCH_ERROR"
            )

    # Tool kayıt tamamlandı
    logger.info(
        "MCP Tool'ları başarıyla kaydedildi: "
        "fetch_news, get_latest_news, check_emergency, "
        "get_critical_news, get_news_detail, search_news, "
        "get_or_create_user, get_active_users, get_user_preferences, "
        "update_user_preference, set_initial_preferences, "
        "get_personalized_news, record_interaction, get_user_history, "
        "system_health_check"
        "record_interaction, get_user_history, system_health_check"
    )