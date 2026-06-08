# MCP Server
"""
Haber Asistanı - MCP Sunucu Giriş Noktası
==========================================
Sistemi başlatan ve LLM bağlantısını açan ana dosyadır.

Transport: Streamable HTTP (n8n ile uyumlu, production önerisi)
Endpoint:  http://localhost:8000/mcp

Çalıştırma:
    python -m src.mcp_server.server
"""
import logging
from mcp.server.fastmcp import FastMCP
from src.config.settings import MCP_HOST, MCP_PORT, LOG_LEVEL

# Loglama ayarları
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 1. MCP Sunucusunu Oluştur (host ve port burada tanımlanır)
mcp = FastMCP(
    "Haber Asistani",
    host=MCP_HOST,
    port=MCP_PORT
)

# 2. Tool'ları Kaydet
try:
    logger.info("Araçlar (Tools) yükleniyor...")
    from src.mcp_server.tools import register_tools
    register_tools(mcp)
    logger.info("Tüm araçlar başarıyla kaydedildi.")
except Exception as e:
    logger.error(f"Sunucu başlatılırken kritik hata: {e}")
    raise

# 3. Çalıştır — Streamable HTTP transport
#    n8n bu adrese bağlanacak: http://{MCP_HOST}:{MCP_PORT}/mcp
if __name__ == "__main__":
    logger.info(f"MCP Sunucusu başlatılıyor: http://{MCP_HOST}:{MCP_PORT}/mcp")
    mcp.run(transport="streamable-http")