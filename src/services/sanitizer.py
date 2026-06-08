"""
Haber Asistanı - İçerik Temizleme Servisi
==========================================
RSS'ten gelen ham HTML içeriğini trafilatura ile
temiz, saf metne dönüştürür.

Görev: Reklam, menü, footer, cookie banner gibi
gürültüyü atıp sadece haber metnini bırakmak.

Kullanım (tools.py içinden):
    from services.sanitizer import Sanitizer

    temiz_metin = await Sanitizer.clean_url("https://...")
    temiz_metin = Sanitizer.clean_html("<html>...</html>")
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import trafilatura
from trafilatura.settings import use_config

logger = logging.getLogger(__name__)


# ==============================================================================
# TRAFILATURA YAPILANDIRMASI
# ==============================================================================

# trafilatura'nın varsayılan ayarlarını özelleştiriyoruz
_config = use_config()
_config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")  # 30 saniye zaman aşımı


# ==============================================================================
# VERİ TAŞIYICI
# ==============================================================================

@dataclass
class CleanContent:
    """
    Temizlenmiş içeriğin yapılandırılmış hali.
    Sadece metin değil, trafilatura'nın çıkardığı
    meta verileri de taşır.
    """
    text: str                        # Ana temiz metin — LLM bunu okuyacak
    title: Optional[str] = None      # Sayfa başlığı (varsa)
    author: Optional[str] = None     # Yazar (varsa)
    date: Optional[str] = None       # Yayın tarihi (varsa)
    language: Optional[str] = None   # Dil kodu: 'tr', 'en' vb.
    word_count: int = 0              # Kelime sayısı


# ==============================================================================
# SANİTİZER SINIFI
# ==============================================================================

class Sanitizer:
    """
    Ham HTML veya URL'den temiz metin çıkaran servis.

    İki kullanım modu:
    1. URL ver → indir + temizle  (async)
    2. HTML ver → sadece temizle  (sync)
    """

    # --------------------------------------------------------------------------
    # ANA FONKSİYONLAR
    # --------------------------------------------------------------------------

    @staticmethod
    async def clean_url(url: str) -> Optional[CleanContent]:
        """
        Bir URL'yi indirir ve içeriğini temizler.
        S2 Reaktif Sohbet: kullanıcı haber detayı istediğinde çağrılır.

        trafilatura.fetch_url senkron olduğu için thread'e taşınır.

        Kullanım:
            icerik = await Sanitizer.clean_url("https://www.bbc.com/turkce/...")
            if icerik:
                print(icerik.text)
                print(f"Kelime sayısı: {icerik.word_count}")
        """
        if not url or not url.startswith(("http://", "https://")):
            logger.warning(f"Geçersiz URL atlandı: {url}")
            return None

        try:
            logger.debug(f"URL indiriliyor: {url}")

            # fetch_url senkron — asyncio.to_thread ile async yapıyoruz
            html = await asyncio.to_thread(trafilatura.fetch_url, url)

            if not html:
                logger.warning(f"URL'den içerik alınamadı: {url}")
                return None

            return Sanitizer.clean_html(html, url=url)

        except Exception as e:
            logger.error(f"URL temizlenirken hata ({url}): {e}")
            return None

    @staticmethod
    def clean_html(html: str, url: str = None) -> Optional[CleanContent]:
        """
        Ham HTML string'ini temiz metne dönüştürür.
        RSS feed'indeki summary_html alanını temizlemek için kullanılır.

        Kullanım:
            icerik = Sanitizer.clean_html("<html><body>...</body></html>")
            if icerik:
                print(icerik.text)
        """
        if not html or not html.strip():
            logger.debug("Boş HTML, atlandı.")
            return None

        try:
            # trafilatura ile tam çıkarım — meta verilerle birlikte
            result = trafilatura.extract(
                html,
                url=url,
                config=_config,
                include_comments=False,    # Yorum bölümlerini alma
                include_tables=True,       # Tablo içeriklerini al (ekonomi haberleri)
                no_fallback=False,         # Fallback yöntemlerini dene
                favor_precision=True,      # Hız yerine doğruluğu tercih et
            )

            if not result or not result.strip():
                logger.debug(f"trafilatura içerik çıkaramadı: {url or 'html'}")
                return None

            # Meta verileri ayrıca çıkar
            metadata = trafilatura.extract_metadata(html, default_url=url)

            temiz_metin = result.strip()

            return CleanContent(
                text=temiz_metin,
                title=metadata.title if metadata else None,
                author=metadata.author if metadata else None,
                date=metadata.date if metadata else None,
                language=metadata.language if metadata else None,
                word_count=len(temiz_metin.split())
            )

        except Exception as e:
            logger.error(f"HTML temizlenirken hata: {e}")
            return None

    # --------------------------------------------------------------------------
    # TOPLU TEMİZLEME
    # --------------------------------------------------------------------------

    @staticmethod
    async def clean_urls_batch(urls: list[str],
                               max_concurrent: int = 5) -> dict[str, Optional[CleanContent]]:
        """
        Birden fazla URL'yi eş zamanlı temizler.
        Günlük bültende 20+ haber URL'si temizlenirken kullanılır.

        max_concurrent: Aynı anda kaç URL işlensin (sunucuları bunaltmamak için)

        Döndürür: {url: CleanContent veya None}

        Kullanım:
            urls = ["https://...", "https://...", "https://..."]
            sonuclar = await Sanitizer.clean_urls_batch(urls)
            for url, icerik in sonuclar.items():
                if icerik:
                    print(f"{url}: {icerik.word_count} kelime")
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited_clean(url: str):
            async with semaphore:
                return url, await Sanitizer.clean_url(url)

        tasks = [_limited_clean(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Toplu temizleme hatası: {result}")
            else:
                url, content = result
                output[url] = content

        basarili = sum(1 for v in output.values() if v is not None)
        logger.info(f"Toplu temizleme: {basarili}/{len(urls)} URL başarılı.")
        return output

    # --------------------------------------------------------------------------
    # YARDIMCI
    # --------------------------------------------------------------------------

    @staticmethod
    def is_content_sufficient(content: CleanContent,
                              min_words: int = 50) -> bool:
        """
        Çıkarılan içeriğin LLM'e göndermek için yeterli mi kontrol eder.
        50 kelimeden az olan içerikler genellikle hatalı çıkarım demektir.

        Kullanım:
            if Sanitizer.is_content_sufficient(icerik):
                # LLM'e gönder
            else:
                # Sadece başlıkla devam et
        """
        if not content or not content.text:
            return False
        return content.word_count >= min_words