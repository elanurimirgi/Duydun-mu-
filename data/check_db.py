import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "haber_asistani.db")

def clear_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    tables = ["news_logs", "bulletin_news", "interactions"]

    for table in tables:
        cursor.execute(f"DELETE FROM {table}")
        count = cursor.rowcount
        print(f"✅ {table} temizlendi — {count} kayıt silindi.")

    for table in tables:
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

    conn.commit()
    conn.close()
    print("\n✅ Tüm tablolar temizlendi. users ve user_preferences dokunulmadı.")

if __name__ == "__main__":
    clear_tables()