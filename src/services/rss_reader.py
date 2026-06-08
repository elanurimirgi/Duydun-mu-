"""
Haber Asistanı - RSS Okuyucu Servisi
=====================================
rss_sources.yaml'dan kaynak listesini okur ve
tüm kaynaklardan haberleri eş zamanlı (async) olarak çeker.

Kullanım (tools.py içinden):
    from services.rss_reader import RSSReader

    reader = RSSReader()
    await reader.fetch_all()          # Tüm kaynaklar
    await reader.fetch_by_category("acil")  # Sadece acil kaynaklar
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import feedparser
import yaml

from src.database.models import NewsModel
from src.services.categorizer import Categorizer

logger = logging.getLogger(__name__)

# rss_sources.yaml'ın konumu
SOURCES_FILE = Path(__file__).resolve().parent.parent / "config" / "rss_sources.yaml"


# ==============================================================================
# VERİ TAŞIYICI
# ==============================================================================

@dataclass
class RawNewsItem:
    """
    feedparser'dan gelen ham haber verisi.
    Veritabanına kaydedilmeden önceki saf hali.
    """
    title: str
    url: str
    source_name: str
    source_category: str
    published_at: Optional[str] = None
    summary_html: Optional[str] = None


# ==============================================================================
# RSS OKUYUCU
# ==============================================================================

class RSSReader:
    """
    Tüm RSS kaynaklarını async olarak çeken servis.
    """

    def __init__(self):
        self.sources = self._load_sources()

    def _load_sources(self) -> list[dict]:
        """
        rss_sources.yaml dosyasını okur.
        Sadece is_active: true olan kaynakları döndürür.
        """
        try:
            with open(SOURCES_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            active = [s for s in data["sources"] if s.get("is_active", True)]
            logger.info(f"{len(active)} aktif RSS kaynağı yüklendi.")
            return active
        except FileNotFoundError:
            logger.error(f"rss_sources.yaml bulunamadı: {SOURCES_FILE}")
            return []
        except yaml.YAMLError as e:
            logger.error(f"rss_sources.yaml okunamadı: {e}")
            return []

    # --------------------------------------------------------------------------
    # ANA ÇEKME FONKSİYONLARI
    # --------------------------------------------------------------------------

    async def fetch_all(self) -> list[RawNewsItem]:
        """
        Tüm aktif kaynaklardan haberleri eş zamanlı çeker.
        """
        tasks = [self._fetch_source(source) for source in self.sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    f"Kaynak hata verdi ({self.sources[i]['name']}): {result}"
                )
            else:
                all_items.extend(result)

        logger.info(f"Toplam {len(all_items)} haber çekildi.")
        return all_items

    async def fetch_by_category(self, category: str) -> list[RawNewsItem]:
        """
        Sadece belirli kategorideki kaynaklardan çeker.
        S3 Priority Watcher için fetch_by_category("acil") kullanılır.
        """
        filtered = [s for s in self.sources if s["category"] == category]
        if not filtered:
            logger.warning(f"'{category}' kategorisinde aktif kaynak bulunamadı.")
            return []

        tasks = [self._fetch_source(source) for source in filtered]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    f"Kaynak hata verdi ({filtered[i]['name']}): {result}"
                )
            else:
                all_items.extend(result)

        return all_items

    # --------------------------------------------------------------------------
    # TEK KAYNAK ÇEKME (İç kullanım)
    # --------------------------------------------------------------------------

    async def _fetch_source(self, source: dict) -> list[RawNewsItem]:
        """
        Tek bir RSS kaynağından haberleri çeker.
        """
        name = source["name"]
        url = source["url"]
        category = source["category"]

        try:
            logger.debug(f"Çekiliyor: {name}")

            feed = await asyncio.to_thread(feedparser.parse, url)

            if feed.bozo and not feed.entries:
                logger.warning(f"Feed okunamadı: {name} ({url})")
                return []

            items = []
            for entry in feed.entries[:5]:
                item = RawNewsItem(
                    title=entry.get("title", "Başlıksız").strip(),
                    url=entry.get("link", ""),
                    source_name=name,
                    source_category=category,
                    published_at=_parse_date(entry),
                    summary_html=entry.get("summary", None)
                )

                if not item.url:
                    logger.debug(f"URL'siz haber atlandı: {item.title}")
                    continue

                items.append(item)

            logger.info(f"{name}: {len(items)} haber çekildi.")
            return items

        except Exception as e:
            logger.error(f"Kaynak çekilemedi ({name}): {e}")
            raise

    # --------------------------------------------------------------------------
    # VERİTABANINA KAYDET
    # --------------------------------------------------------------------------

    async def fetch_and_save(self, category: str = None) -> dict:
        """
        Haberleri çekip doğrudan veritabanına kaydeder.
        Tüm haberler Claude ile tek seferde kategorize edilir.
        """
        if category:
            items = await self.fetch_by_category(category)
        else:
            items = await self.fetch_all()

        saved = 0
        skipped = 0

        if items:
            # Tüm haberleri Claude ile tek seferde kategorize et
            news_list = [
                {"title": i.title, "summary": i.summary_html or "", "source_name": i.source_name}
                for i in items
            ]
            categories = await Categorizer.categorize_batch(news_list)
        else:
            categories = []

        for item, cat in zip(items, categories):
            news_id = NewsModel.save(
                title=item.title,
                url=item.url,
                category=cat,
                raw_text=item.summary_html,
                published_at=item.published_at
            )
            if news_id:
                saved += 1
            else:
                skipped += 1

        result = {"fetched": len(items), "saved": saved, "skipped": skipped}
        logger.info(f"Kayıt sonucu: {result}")
        return result


# ==============================================================================
# YARDIMCI FONKSİYON
# ==============================================================================

def _parse_date(entry) -> Optional[str]:
    """
    feedparser'ın farklı tarih formatlarını ISO 8601 string'e çevirir.
    """
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return dt.isoformat()
    except Exception:
        pass
    return None