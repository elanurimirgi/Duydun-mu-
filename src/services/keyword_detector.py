"""
Haber Asistanı - Acil Durum Anahtar Kelime Dedektörü
=====================================================
S3 Priority Watcher senaryosunun kalbi.
Haber metinlerini tarayarak acil durum içerip
içermediğini ağırlıklı skor sistemiyle tespit eder.

Skorlama Mantığı:
- Her kategorinin anahtar kelimeleri farklı ağırlıkta
- Deprem > Terör > Sel > Yangın > Salgın (kritiklik sırası)
- Eşik değeri (threshold) aşılırsa haber kritik sayılır

Kullanım (tools.py içinden):
    from services.keyword_detector import KeywordDetector

    sonuc = KeywordDetector.analyze("İzmir'de 6.5 büyüklüğünde deprem!")
    if sonuc.is_critical:
        print(f"ACİL: {sonuc.category} (skor: {sonuc.score})")
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ==============================================================================
# ACİL DURUM EŞİK DEĞERİ
# ==============================================================================

# Bu değerin üzerindeki skorlar → is_critical = True
# 0.0 - 1.0 arasında. Düşürürsen daha hassas, yükseltirsen daha seçici olur.
CRITICAL_THRESHOLD = 0.9


# ==============================================================================
# ANAHTAR KELİME SÖZLÜĞÜ
# ==============================================================================
# Yapı: { kategori: { kelime: ağırlık } }
#
# Ağırlık rehberi:
#   1.0 → Kesin acil durum kelimesi    ("deprem oldu", "tsunami uyarısı")
#   0.7 → Güçlü işaret                 ("büyüklüğünde", "şiddetinde")
#   0.4 → Destekleyici kelime          ("hasar", "yaralı")
#   0.2 → Zayıf işaret                 ("endişe", "uyarı")
# ==============================================================================

KEYWORD_WEIGHTS: dict[str, dict[str, float]] = {

    # ------------------------------------------------------------------
    # DEPREM / TSUNAMİ — En yüksek öncelik
    # Türkiye'nin en kritik doğal afet riski
    # ------------------------------------------------------------------
    "deprem": {
        # Kesin deprem kelimeleri
        "deprem": 1.0,
        "earthquake": 1.0,
        "tsunami": 1.0,
        "sarsıntı": 0.9,
        "yer sarsıntısı": 0.9,

        # Büyüklük ifadeleri
        "büyüklüğünde deprem": 1.0,
        "şiddetinde deprem": 1.0,
        "richter": 0.8,
        "magnitude": 0.8,
        "büyüklüğünde": 0.7,
        "şiddetinde": 0.7,

        # Kurumlar (bunlar deprem haberinde geçer)
        "afad": 0.6,
        "kandilli": 0.6,
        "koeri": 0.6,

        # Hasar kelimeleri
        "yıkıldı": 0.8,
        "enkaz": 1.0,
        "göçük": 0.9,
        "artçı": 0.7,
        "fay hattı": 0.5,

        # Genel acil
        "tahliye": 0.5,
        "arama kurtarma": 0.8,
    },

    # ------------------------------------------------------------------
    # SEL / TAŞKIN
    # ------------------------------------------------------------------
    "sel": {
        "sel": 1.0,
        "taşkın": 1.0,
        "su baskını": 1.0,
        "heyelan": 0.9,
        "çığ": 0.9,
        "sürüklendi": 0.7,
        "boğuldu": 0.8,
        "mahsur kaldı": 0.7,
        "köprü yıkıldı": 0.8,
        "yol kapandı": 0.4,
        "tahliye": 0.5,
        "şiddetli yağış": 0.5,
        "meteoroloji uyarısı": 0.4,
    },

    # ------------------------------------------------------------------
    # YANGIN
    # ------------------------------------------------------------------
    "yangin": {
        "yangın": 1.0,
        "orman yangını": 1.0,
        "alevler": 0.8,
        "yanıyor": 0.7,
        "itfaiye": 0.6,
        "tahliye": 0.5,
        "kül oldu": 0.8,
        "yandı": 0.7,
        "duman": 0.4,
        "söndürme": 0.5,
        "hektar": 0.4,       # "X hektar alan yandı"
    },

    # ------------------------------------------------------------------
    # TERÖR / GÜVENLİK
    # ------------------------------------------------------------------
    "teror": {
        "bomba": 1.0,
        "patlama": 1.0,
        "saldırı": 0.9,
        "terör": 1.0,
        "terörist": 1.0,
        "füze": 0.9,
        "silahlı saldırı": 1.0,
        "çatışma": 0.8,
        "şehit": 0.8,
        "yaralı": 0.5,
        "ölü": 0.6,
        "operasyon": 0.5,
        "güvenlik güçleri": 0.4,
        "sokağa çıkma yasağı": 0.9,
        "olağanüstü hal": 1.0,
    },

    # ------------------------------------------------------------------
    # SALGIN / SAĞLIK
    # ------------------------------------------------------------------
    "salgin": {
        "salgın": 1.0,
        "pandemi": 1.0,
        "salgın hastalık": 1.0,
        "karantina": 0.9,
        "vaka": 0.6,
        "ölü sayısı": 0.8,
        "can kaybı": 0.8,
        "dünya sağlık örgütü": 0.5,
        "who": 0.4,
        "virüs": 0.7,
        "mutasyon": 0.6,
        "aşı zorunluluğu": 0.5,
        "acil durum ilan": 0.9,
        "zehirlenme": 0.8,
    },
}


# ==============================================================================
# VERİ TAŞIYICI
# ==============================================================================

@dataclass
class DetectionResult:
    """
    Analiz sonucunun yapılandırılmış hali.

    Örnek:
        DetectionResult(
            is_critical=True,
            score=0.85,
            category="deprem",
            matched_keywords=["deprem", "enkaz", "afad"],
            explanation="deprem kategorisinde 3 kritik kelime bulundu"
        )
    """
    is_critical: bool                       # Eşik aşıldı mı?
    score: float                            # 0.0 - 1.0 arası normalize skor
    category: Optional[str] = None         # En yüksek skoru alan kategori
    matched_keywords: list[str] = field(default_factory=list)  # Bulunan kelimeler
    category_scores: dict = field(default_factory=dict)        # Tüm kategorilerin skorları
    explanation: str = ""                  # İnsan okunabilir açıklama


# ==============================================================================
# DEDEKTÖR SINIFI
# ==============================================================================

class KeywordDetector:
    """
    Haber metinlerini acil durum anahtar kelimeleri için tarar.

    Skorlama adımları:
    1. Metni küçük harfe çevir, noktalama temizle
    2. Her kategori için eşleşen kelimelerin ağırlıklarını topla
    3. Kategorinin maksimum olası skoruna bölerek normalize et (0.0-1.0)
    4. En yüksek skoru alan kategoriyi seç
    5. CRITICAL_THRESHOLD'u aşıyorsa is_critical = True
    """

    @staticmethod
    def analyze(text: str, title: str = None) -> DetectionResult:
        """
        Verilen metni analiz eder ve acil durum skoru hesaplar.

        Args:
            text:  Analiz edilecek haber metni (temizlenmiş olmalı)
            title: Haber başlığı (varsa — başlıktaki kelimeler 2x ağırlık taşır)

        Döndürür: DetectionResult

        Kullanım:
            sonuc = KeywordDetector.analyze(
                text="İzmir açıklarında 6.5 büyüklüğünde deprem meydana geldi.",
                title="İzmir'de büyük deprem"
            )
            if sonuc.is_critical:
                print(f"KATEGORİ: {sonuc.category}, SKOR: {sonuc.score:.2f}")
        """
        if not text or not text.strip():
            return DetectionResult(is_critical=False, score=0.0,
                                   explanation="Boş metin")

        # Metni hazırla
        temiz_metin = _normalize_text(text)

        # Başlık varsa 2 kez ekle (başlıktaki kelimeler daha önemli)
        if title:
            temiz_metin = _normalize_text(title) + " " + \
                          _normalize_text(title) + " " + temiz_metin

        # Her kategori için skor hesapla
        category_scores = {}
        category_matches = {}

        for kategori, kelimeler in KEYWORD_WEIGHTS.items():
            skor, eslesen = _calculate_category_score(temiz_metin, kelimeler)
            category_scores[kategori] = skor
            category_matches[kategori] = eslesen

        # En yüksek skoru alan kategori
        en_yuksek_kategori = max(category_scores, key=category_scores.get)
        en_yuksek_skor = category_scores[en_yuksek_kategori]
        eslesen_kelimeler = category_matches[en_yuksek_kategori]

        is_critical = en_yuksek_skor >= CRITICAL_THRESHOLD

        # Açıklama oluştur
        if is_critical:
            aciklama = (
                f"'{en_yuksek_kategori}' kategorisinde "
                f"{len(eslesen_kelimeler)} anahtar kelime bulundu "
                f"(skor: {en_yuksek_skor:.2f} / eşik: {CRITICAL_THRESHOLD})"
            )
        else:
            aciklama = (
                f"Eşik değeri aşılmadı. "
                f"En yüksek skor: '{en_yuksek_kategori}' = {en_yuksek_skor:.2f} "
                f"(eşik: {CRITICAL_THRESHOLD})"
            )

        if is_critical:
            logger.warning(
                f"ACİL DURUM TESPİT EDİLDİ! "
                f"Kategori: {en_yuksek_kategori}, "
                f"Skor: {en_yuksek_skor:.2f}, "
                f"Kelimeler: {eslesen_kelimeler}"
            )

        return DetectionResult(
            is_critical=is_critical,
            score=round(en_yuksek_skor, 3),
            category=en_yuksek_kategori if is_critical else None,
            matched_keywords=eslesen_kelimeler,
            category_scores={k: round(v, 3) for k, v in category_scores.items()},
            explanation=aciklama
        )

    @staticmethod
    def analyze_batch(news_list: list[dict]) -> list[tuple[dict, DetectionResult]]:
        """
        Birden fazla haberi toplu analiz eder.
        S3'te RSS'ten gelen tüm haberler buradan geçer.

        Args:
            news_list: [{"title": "...", "text": "..."}, ...] formatında liste

        Döndürür: [(haber_dict, DetectionResult), ...]

        Kullanım:
            haberler = [
                {"title": "Deprem haberi", "text": "6.5 büyüklüğünde..."},
                {"title": "Spor haberi",   "text": "Galatasaray kazandı"},
            ]
            sonuclar = KeywordDetector.analyze_batch(haberler)
            kritikler = [(h, s) for h, s in sonuclar if s.is_critical]
        """
        sonuclar = []
        for haber in news_list:
            title = haber.get("title", "")
            text = haber.get("text", haber.get("summary", ""))
            result = KeywordDetector.analyze(text=text, title=title)
            sonuclar.append((haber, result))

        kritik_sayisi = sum(1 for _, s in sonuclar if s.is_critical)
        logger.info(
            f"Toplu analiz: {len(news_list)} haber, "
            f"{kritik_sayisi} kritik tespit edildi."
        )
        return sonuclar

    @staticmethod
    def get_threshold() -> float:
        """Mevcut eşik değerini döndürür."""
        return CRITICAL_THRESHOLD

    @staticmethod
    def set_threshold(value: float) -> None:
        """
        Eşik değerini günceller.
        Test sırasında veya hassasiyet ayarı için kullanılır.
        0.0 - 1.0 arasında olmalı.
        """
        global CRITICAL_THRESHOLD
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"Eşik değeri 0.0-1.0 arasında olmalı, verilen: {value}")
        CRITICAL_THRESHOLD = value
        logger.info(f"Eşik değeri güncellendi: {value}")


# ==============================================================================
# YARDIMCI FONKSİYONLAR (Özel)
# ==============================================================================

def _normalize_text(text: str) -> str:
    """
    Metni anahtar kelime araması için hazırlar.
    - Küçük harfe çevirir
    - Fazla boşlukları temizler
    - Noktalama işaretlerini kaldırır
    """
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)  # Noktalama → boşluk
    text = re.sub(r'\s+', ' ', text)       # Çoklu boşluk → tek boşluk
    return text.strip()


def _calculate_category_score(text: str,
                               keywords: dict[str, float]) -> tuple[float, list[str]]:
    """
    Bir kategori için normalize edilmiş skor hesaplar.

    Normalize etme:
    - Ham skor: eşleşen kelimelerin ağırlıkları toplamı
    - Max skor: kategorideki en yüksek 3 ağırlığın toplamı
    - Normalize = ham / max (0.0 - 1.0 aralığına sıkıştırır)

    Neden en yüksek 3?
    Bir haberde 3 kritik kelime geçiyorsa zaten kesin acil durum.
    Daha fazla kelime geçerse skor 1.0'ı aşmaz.
    """
    ham_skor = 0.0
    eslesen = []

    for kelime, agirlik in keywords.items():
    # (?<!\w) ve (?!\w) → Unicode-aware kelime sınırı
    # \b yerine bu kullanılır çünkü \w Türkçe harfleri tanır
        pattern = r'(?<!\w)' + re.escape(kelime) + r'(?!\w)'
        if re.search(pattern, text, re.UNICODE):
            ham_skor += agirlik
            eslesen.append(kelime)

    if not eslesen:
        return 0.0, []

    # Normalize: kategorinin max 3 ağırlığına böl
    max_agirliklar = sorted(keywords.values(), reverse=True)[:3]
    max_skor = sum(max_agirliklar)

    normalize_skor = min(ham_skor / max_skor, 1.0)  # 1.0'ı geçemez
    return normalize_skor, eslesen