# 📰 Duydun mu? — Akıllı Kişiselleştirilmiş Haber Asistanı

**Duydun mu?**, kullanıcıların bilgi taşması (*information overload*) problemini çözmek amacıyla geliştirilen, Telegram üzerinden çalışan, kullanıcının ilgi alanlarına ve etkileşim geçmişine göre kendini güncelleyen yapay zeka destekli bir kişisel haber asistanıdır.

Proje; gereksinim analizinden sistem mimarisi tasarımına, yapay zeka entegrasyonundan kullanıcı davranış analizine kadar uçtan uca ekip sorumluluğuyla geliştirilmiştir.

---

## 🎥 Demo Videosu

Botun uçtan uca kullanımını gösteren demo videosunu izlemek için aşağıdaki görsele tıklayın:

[![Demo Videosu](https://img.youtube.com/vi/9zVWDfZiqhc/maxresdefault.jpg)](https://youtu.be/9zVWDfZiqhc)

---

## 🧩 Problem ve Çözüm

Günümüzde kullanıcılar onlarca farklı kaynaktan gelen, kişisel ilgi alanlarıyla örtüşmeyen, gereksiz bilgi yığınıyla karşı karşıya kalıyor. **Duydun mu?**, bu problemi şu şekilde çözüyor:

- Kullanıcının seçtiği ilgi alanlarına göre haberleri filtreler ve kategorize eder
- Düzenli günlük/haftalık bültenler ile bilgiyi özetleyerek sunar
- Önemli gelişmeleri "Son Dakika" modülüyle anlık olarak iletir
- Kullanıcı etkileşimlerinden (okuma, beğenme) öğrenerek zamanla kişiselleşir
- Serbest sohbet modunda, AI Agent aracılığıyla anlık web araması yaparak soruları yanıtlar

---

## 🛠️ Teknik Mimari

| Katman | Teknoloji |
|---|---|
| Otomasyon / Workflow Orkestrasyon | **n8n** |
| Yapay Zeka Araç Sunucusu | **Python FastMCP Server** (18 özel araç) |
| Dil Modeli (LLM) | **DeepSeek-V3** |
| Veritabanı | **SQLite** |
| Kullanıcı Arayüzü | **Telegram Bot API** |
| Tünelleme (Yerel Geliştirme) | **Cloudflare** |

**Öne çıkan teknik tasarım kararları:**
- AI Agent'ın çoklu araç çağrılarını (multi-step tool calling) güvenilir şekilde zincirleyebilmesi için özel **birleşik MCP araçları** geliştirildi.
- Kullanıcı tercihleri, basit bir filtre değil **ağırlıklı bir ilgi profili** (yıldız bazlı skorlama) olarak modellendi ve davranışsal geri bildirimle güncellendi.
- Bültenler arası tekrarı önlemek için `bulletin_news` tablosuyla **çapraz iş akışı hafızası (cross-workflow memory)** kuruldu.
- İçerik güvenilirliğini artırmak amacıyla bir **veri temizleme (sanitization) modülü** geliştirildi.

---

## ⚙️ Kurulum

1. Gereksinimleri yükleyin:
   ```bash
   pip install -r requirements.txt
   ```
2. `.env` dosyasını `.env.example`'dan kopyalayın ve kendi API anahtarlarınızla ayarlayın.
3. Veritabanını başlatın:
   ```bash
   python src/database/setup.py
   ```

---

## ▶️ Kullanım

1. n8n workflow dosyalarını (`n8n_workflows/`) kendi n8n instance'ınıza içe aktarın.
2. MCP Server'ı başlatın:
   ```bash
   python mcp_server/server.py
   ```
3. Telegram botunuzu `.env` dosyasındaki token ile başlatın ve `/start` komutuyla etkileşime geçin.

---


## 📱 Uygulamadan Görseller

### 1. Ana Menü — Tüm Özellikler Tek Ekranda
Kullanıcı, günün haberlerinden son dakikaya, ilgi alanı seçiminden profil/istatistiklere kadar tüm işlevlere tek bir menüden erişebiliyor.

![Ana Menü](https://github.com/user-attachments/assets/d7e37a1d-82a5-4a7f-be7e-ff2244ff2aa8)

---

### 2. Kişiselleştirme — İlgi Alanı Seçimi
Kullanıcı 12 farklı kategori arasından istediği kadar seçim yapabiliyor; bu seçimler, sistemin haber filtreleme ve önceliklendirme mantığının temelini oluşturuyor.

![Kişiselleştirme](https://github.com/user-attachments/assets/c60c34c6-f989-499e-8360-1473fa2e9080)

---

### 3. Günlük Bülten — Kişiselleştirilmiş Haber Akışı
Sistem, kullanıcının seçtiği kategorilere göre günün öne çıkan haberlerini özetleyerek, kaynak linkleriyle birlikte sunuyor.

![Günlük Bülten](https://github.com/user-attachments/assets/a925182a-4e94-475e-b936-652e49721c47)

---

### 4. Son Dakika — Gerçek Zamanlı Bildirim
Önemli gelişmeler, kategorilere ayrılmış ve özetlenmiş şekilde anlık olarak kullanıcıya iletiliyor.

![Son Dakika](https://github.com/user-attachments/assets/0cba2cab-ef12-410a-967d-14520c50c28d)

---

### 5. AI Agent — Serbest Sohbet & Web Araması
Kullanıcı, menü dışında serbestçe soru sorabiliyor; AI Agent gerçek zamanlı web araması yaparak güncel ve doğru bilgiyle yanıt üretiyor.

![AI Agent Sohbet](https://github.com/user-attachments/assets/18587ea3-76db-41b8-9265-c0446a78c434)

---

### 6. Profil & Davranışsal Analitik
Sistem, kullanıcının okuma geçmişini ve etkileşimlerini analiz ederek ilgi profilini sürekli güncelliyor; kullanıcıya kendi etkileşim istatistiklerini de şeffaf şekilde gösteriyor.

![Profil & Analitik](https://github.com/user-attachments/assets/16bf1401-b2ea-492b-8412-c0f54ea0e1bf)

---

## 👥 Katkıda Bulunanlar

Proje, 2 kişilik bir ekip tarafından geliştirilmiştir.

- **Elanur İmirgi**
- **Zeynep Ravza Dursun**

Danışman: Dr. Öğr. Üyesi Oğuz Emre Kural

---


## 📄 Lisans

Bu proje [MIT License](./LICENSE) ile lisanslanmıştır.
