# src/config/settings.py
"""
Haber Asistanı - Merkezi Ayarlar
================================
Tüm çevresel değişkenler (environment variables) buradan okunur.
.env dosyası load_dotenv() ile yüklenir.

Kullanım (diğer modüllerden):
    from src.config.settings import DB_PATH, ANTHROPIC_API_KEY
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env dosyasını yükle (proje kök dizinindeki)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

# ==============================================================================
# VERİTABANI
# ==============================================================================
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "haber_asistani.db"))

# ==============================================================================
# API ANAHTARLARI
# ==============================================================================
# Anthropic SDK varsayılan olarak ANTHROPIC_API_KEY'i okur.
# Burada açıkça tanımlıyoruz ki diğer modüller de erişebilsin.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# Telegram Bot Token — n8n tarafından kullanılır.
# MCP server doğrudan kullanmaz ama validasyon için burada tutuyoruz.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ==============================================================================
# MCP SERVER AYARLARI
# ==============================================================================
# MCP sunucusunun dinleyeceği host ve port.
# n8n bu adrese HTTP isteği gönderir.
MCP_HOST = os.getenv("MCP_HOST", "localhost")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

# ==============================================================================
# LOGLAMA
# ==============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO") 

