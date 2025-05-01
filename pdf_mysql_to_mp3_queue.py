#!/usr/bin/env python3
# pdf_mysql_to_mp3_queue.py

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector
from PyPDF2 import PdfReader
from gtts import gTTS

# Load .env explicitly
PROJECT_DIR = Path(__file__).parent.resolve()
load_dotenv(PROJECT_DIR / ".env", override=True)

# Database configuration
db_cfg = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}
PDF_DIR = Path(os.getenv("PDF_DIR", "./pdf"))
MP3_DIR = Path(os.getenv("MP3_DIR", "./mp3"))
LANG = os.getenv("LANG", "id")

# Ensure output directory exists
MP3_DIR.mkdir(parents=True, exist_ok=True)

# Fetch all pending records
def get_pending(cursor):
    cursor.execute(
        "SELECT id, file_peraturan FROM produk_hukum_lists "
        "WHERE conversion_status = 'pending' ORDER BY id ASC LIMIT 1"
    )
    return cursor.fetchall()

# Update status and paths in DB
def update_status(cursor, conn, record_id, status, mp3_path=None, error_msg=None):
    fields = ["conversion_status = %s"]
    params = [status]
    if mp3_path is not None:
        fields.append("mp3_path = %s")
        params.append(mp3_path)
    if error_msg:
        fields.append("conversion_error = %s")
        params.append(error_msg)
    sql = f"UPDATE produk_hukum_lists SET {', '.join(fields)} WHERE id = %s"
    params.append(record_id)
    cursor.execute(sql, params)
    conn.commit()

# Convert PDF pages 1-10 to MP3
def pdf_to_mp3(pdf_path: Path, mp3_path: Path):
    reader = PdfReader(str(pdf_path))
    text_chunks = []
    # Limit to pages 1-10
    for i, page in enumerate(reader.pages[:10], start=1):
        text = page.extract_text()
        if not text or not text.strip():
            raise ValueError(f"Halaman {i} tidak dapat diekstraksi; kemungkinan hasil scan atau rusak.")
        text_chunks.append(text)
    full_text = "\n".join(text_chunks)
    # Generate TTS
    tts = gTTS(text=full_text, lang=LANG)
    tts.save(str(mp3_path))

# Process a single record
def process_record(cursor, conn, record_id, filename):
    update_status(cursor, conn, record_id, "in_progress")
    pdf_file = PDF_DIR / filename
    if not pdf_file.exists():
        err = f"File tidak ditemukan: {pdf_file}"
        update_status(cursor, conn, record_id, "error", error_msg=err)
        print(f"❌ [id={record_id}] {err}")
        return
    try:
        mp3_name = f"peraturan_{record_id}.mp3"
        mp3_file = MP3_DIR / mp3_name
        pdf_to_mp3(pdf_file, mp3_file)
        update_status(cursor, conn, record_id, "completed", mp3_path=mp3_name)
        print(f"✅ [id={record_id}] Selesai: {mp3_name}")
    except Exception as e:
        msg = str(e)[:255]
        update_status(cursor, conn, record_id, "error", error_msg=msg)
        print(f"❌ [id={record_id}] Error saat konversi: {msg}")

# Main processing for all pending
def main():
    conn = mysql.connector.connect(**db_cfg)
    cursor = conn.cursor()

    pending = get_pending(cursor)
    if not pending:
        print("✅ Tidak ada entri pending.")
    else:
        for record_id, filename in pending:
            print(f"🔄 Memproses id={record_id}, file={filename}")
            process_record(cursor, conn, record_id, filename)

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
