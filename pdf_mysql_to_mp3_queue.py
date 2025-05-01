#!/usr/bin/env python3
# pdf_mysql_to_mp3_queue.py

import os
import sys
import traceback
import tempfile
import json
import time
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector
from PyPDF2 import PdfReader
from gtts import gTTS
from pydub import AudioSegment

# 1. Tentukan path ke .env secara eksplisit dan load ulang variabel
PROJECT_DIR = Path(__file__).parent.resolve()
dotenv_path = PROJECT_DIR / ".env"
ENV_KEYS = [
    "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "PDF_DIR", "MP3_DIR", "LANG"
]
for key in ENV_KEYS:
    os.environ.pop(key, None)
load_dotenv(dotenv_path, override=True)

# 2. Baca konfigurasi dan direktori
DB_CFG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}
PDF_DIR = Path(os.getenv("PDF_DIR", "./pdf"))
MP3_DIR = Path(os.getenv("MP3_DIR", "./mp3"))
LANG    = os.getenv("LANG", "id")

# 3. Pastikan direktori output ada
try:
    MP3_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"[Error] Gagal membuat direktori {MP3_DIR}: {e}", file=sys.stderr)
    sys.exit(1)

# 4. Ambil satu entri pending dari DB (produk_hukum_lists)
def get_next_pending(cursor):
    cursor.execute(
        "SELECT id, file_peraturan FROM produk_hukum_lists "
        "WHERE conversion_status = 'pending' AND id=52 ORDER BY id ASC LIMIT 1"
    )
    return cursor.fetchone()

# 5. Update status, mp3_path, dan error pada tabel produk_hukum_lists
def update_status(cursor, conn, record_id, status, error_msg=None, mp3_path=None):
    fields = ["conversion_status = %s"]
    params = [status]
    if error_msg is not None:
        fields.append("conversion_error = %s")
        params.append(error_msg)
    else:
        fields.append("conversion_error = NULL")
    if mp3_path is not None:
        fields.append("mp3_path = %s")
        params.append(mp3_path)
    params.append(record_id)
    sql = f"UPDATE produk_hukum_lists SET {', '.join(fields)} WHERE id = %s"
    cursor.execute(sql, params)
    conn.commit()

# 6. Fungsi konversi halaman 1-10 dengan chunking teks, retry, dan concatenation audio
def pdf_to_mp3(pdf_path: Path, mp3_path: Path, max_chars=3000, max_retries=3, retry_delay=1):
    reader = PdfReader(str(pdf_path))
    text_pages = []
    for idx, page in enumerate(reader.pages[:10], start=1):
        text = page.extract_text()
        if not text or not text.strip():
            raise ValueError(f"Halaman {idx} tidak dapat diekstraksi; kemungkinan PDF hasil scan atau rusak.")
        text_pages.append(text)
    full_text = "\n".join(text_pages)

    # Pecah menjadi chunk <= max_chars
    chunks = []
    start = 0
    length = len(full_text)
    while start < length:
        end = start + max_chars
        if end >= length:
            chunks.append(full_text[start:].strip())
            break
        space = full_text.rfind(' ', start, end)
        if space == -1 or space <= start:
            space = end
        chunks.append(full_text[start:space].strip())
        start = space

    # Convert tiap chunk dengan retry
    combined = AudioSegment.empty()
    for idx, chunk in enumerate(chunks, start=1):
        if not chunk:
            continue
        tmp_path = None
        for attempt in range(1, max_retries + 1):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tts = gTTS(text=chunk, lang=LANG)
                    tts.write_to_fp(tmp)
                    tmp_path = tmp.name
                segment = AudioSegment.from_mp3(tmp_path)
                combined += segment
                break  # sukses, keluar dari retry loop
            except (json.JSONDecodeError, ValueError) as e:
                err_msg = str(e)
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise ValueError(f"Chunk {idx} gagal setelah {max_retries} percobaan: {err_msg}")
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    raise ValueError(f"Error umum di chunk {idx}: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except:
                        pass

    # Export file final
    combined.export(str(mp3_path), format="mp3")

# 7. Alur utama
def main():
    conn = mysql.connector.connect(**DB_CFG)
    cursor = conn.cursor()
    task = get_next_pending(cursor)
    if not task:
        print("✅ Tidak ada entri pending.")
        cursor.close()
        conn.close()
        return
    record_id, filename = task
    print(f"🔄 Memproses id={record_id}, file={filename}")
    update_status(cursor, conn, record_id, "in_progress")
    pdf_file = PDF_DIR / filename
    if not pdf_file.exists():
        err = f"File PDF tidak ditemukan: {pdf_file}"
        print("❌", err)
        update_status(cursor, conn, record_id, "error", error_msg=err)
    else:
        try:
            mp3_filename = f"peraturan_{record_id}.mp3"
            mp3_file = MP3_DIR / mp3_filename
            pdf_to_mp3(pdf_file, mp3_file)
            update_status(cursor, conn, record_id, "completed", mp3_path=mp3_filename)
            print(f"✅ Selesai: {mp3_filename}")
        except Exception as e:
            tb_msg = str(e)[:255]
            print("❌ Error saat konversi:", e)
            update_status(cursor, conn, record_id, "error", error_msg=tb_msg)
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
