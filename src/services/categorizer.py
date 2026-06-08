"""
Haber Asistanı - DeepSeek Kategorizasyon Servisi
=================================================
Haberleri 10'ar gruplar halinde PARALEL olarak DeepSeek API'ye gönderir.
Timeout sorununu önler.
"""

import json
import logging
import asyncio
import httpx
from src.config.settings import DEEPSEEK_API_KEY

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "siyaset", "magazin", "ekonomi", "spor",
    "bilim", "teknoloji", "sağlık", "yemek",
    "otomotiv", "müzik", "dizi_film", "ai"
}

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
BATCH_SIZE = 10


class Categorizer:

    @staticmethod
    async def categorize_batch(news_list: list[dict]) -> list[str]:
        """
        Haberleri 10'ar gruplar halinde PARALEL kategorize eder.
        """
        if not news_list:
            return []

        # Grupları oluştur
        batches = []
        for i in range(0, len(news_list), BATCH_SIZE):
            batches.append(news_list[i:i + BATCH_SIZE])

        # Tüm grupları paralel çalıştır
        results = await asyncio.gather(*[
            Categorizer._categorize_group(batch)
            for batch in batches
        ])

        # Sonuçları sırayla birleştir
        all_categories = []
        for i, cats in enumerate(results):
            all_categories.extend(cats)
            logger.info(f"Grup {i+1} kategorize edildi: {len(cats)} haber")

        logger.info(f"Toplam {len(all_categories)} haber kategorize edildi.")
        return all_categories

    @staticmethod
    async def _categorize_group(news_list: list[dict]) -> list[str]:
        """
        Tek bir grup haberi kategorize eder (max 10 haber).
        """
        news_text = ""
        for i, news in enumerate(news_list, 1):
            news_text += f"{i}. {news['title']}\n"

        prompt = f"""Aşağıdaki Türkçe haberleri kategorize et.
Kategoriler: siyaset, magazin, ekonomi, spor, bilim, teknoloji, sağlık, yemek, otomotiv, müzik, dizi_film, ai

Kategori açıklamaları:
- siyaset: hükümet, seçim, parti, diplomasi, askeri, savaş, terör
- ekonomi: borsa, döviz, faiz, enflasyon, ticaret, enerji
- spor: futbol, basketbol, voleybol, tenis, olimpiyat, transfer, maç
- teknoloji: telefon, bilgisayar, yazılım, internet, oyun, donanım
- ai: yapay zeka, makine öğrenmesi, ChatGPT, LLM
- sağlık: hastalık, tedavi, ilaç, hastane, salgın
- yemek: tarif, mutfak, restoran, pişirme
- otomotiv: araba, otomobil, kampanya, test sürüşü
- müzik: şarkı, albüm, konser, sanatçı
- dizi_film: dizi, film, sinema, Netflix, yönetmen
- bilim: araştırma, uzay, keşif, fizik, biyoloji
- magazin: ünlü hayatı, evlilik, boşanma, sosyal medya

SADECE JSON döndür:
{{"categories": ["kategori1", "kategori2", ...]}}

Haberler:
{news_text}"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 512,
                        "temperature": 0,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Sadece JSON formatında yanıt ver, başka hiçbir şey yazma."
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ]
                    }
                )
                response.raise_for_status()
                data = response.json()

                text = data["choices"][0]["message"]["content"].strip()
                text = text.replace("```json", "").replace("```", "").strip()
                result = json.loads(text)
                categories = result["categories"]

                validated = []
                for cat in categories:
                    cat = cat.strip().lower()
                    validated.append(cat if cat in VALID_CATEGORIES else "siyaset")

                while len(validated) < len(news_list):
                    validated.append("siyaset")

                return validated[:len(news_list)]

        except Exception as e:
            logger.error(f"Grup kategorizasyon hatası: {type(e).__name__}: {e}")
            try:
                logger.error(f"API response: {response.status_code} - {response.text[:300]}")
            except:
                pass
            return ["siyaset"] * len(news_list)

    @staticmethod
    async def categorize(title: str, summary: str = "", source_name: str = "") -> str:
        result = await Categorizer.categorize_batch([
            {"title": title, "summary": summary, "source_name": source_name}
        ])
        return result[0]