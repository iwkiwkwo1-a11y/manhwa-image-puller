#!/usr/bin/env python3
"""
Manhwa Image Puller
===================
Script untuk menarik gambar halaman manhwa dari situs agregator
(studi kasus: VortexScans) secara bersih / full-resolution.

Dibuat untuk tujuan BELAJAR web scraping: bagaimana menghadapi situs
yang dilindungi Cloudflare WAF, dan bagaimana memanfaatkan layanan
reader pihak ketiga + proxy publik secara bertahap.

Cara pakai:
  python3 vortex_pull.py <URL_CHAPTER> [folder_output]

Contoh:
  python3 vortex_pull.py "https://vortexscans.org/series/nama-series/chapter-1"

Alur kerja (3 tahap):
  1. Ambil HTML halaman chapter lewat layanan `page_reader` (z-ai CLI)
     -> karena Cloudflare memblokir IP datacenter, kita pinjam infrastruktur reader.
  2. Ekstrak semua URL gambar page dari HTML
     -> pola: storage.<domain>/upload/series/<slug>/<uuid>/page-*.webp
  3. Download tiap gambar lewat proxy Google Translate (translate.goog)
     -> request di-fetch oleh IP Google sehingga lolos WAF, dan file yang
        diterima adalah byte ASLI dari storage (tidak ada re-encode).
  4. Verifikasi header RIFF (WebP valid) + retry otomatis 3x dengan backoff.

Output: <folder_output>/NNN.webp (berurutan sesuai halaman)

Kebutuhan: python3, pip install requests, dan CLI `z-ai` untuk tahap 1.
"""

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

CHAPTER_URL = sys.argv[1] if len(sys.argv) > 1 else ""
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "./output"
WORK_DIR = os.path.dirname(os.path.abspath(__file__))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------- STEP 1: ambil HTML halaman chapter via page_reader ----------
def fetch_page_html(chapter_url: str) -> str:
    out_json = os.path.join(WORK_DIR, "_chapter_page.json")
    cmd = ["z-ai", "function", "-n", "page_reader",
           "-a", json.dumps({"url": chapter_url}), "-o", out_json]
    print(f"[1/3] page_reader -> {chapter_url}")
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    data = json.load(open(out_json))
    html = data["data"]["html"]
    print(f"      OK  html_len={len(html)}  title={data['data'].get('title', '?')!r}")
    return html


# ---------- STEP 2: ekstrak URL gambar halaman ----------
def extract_image_urls(html: str) -> list:
    """Cari semua URL gambar page pada folder chapter.
    Pola: https://storage.vortexscans.org/upload/series/<slug>/<uuid>/page-XXXX_*.webp
    Urutkan berdasarkan nomor page agar urutan baca benar."""
    pat = re.compile(
        r"https://storage\.vortexscans\.org/upload/series/"
        r"(?P<slug>[a-z0-9-]+)/(?P<uuid>[a-f0-9-]{36})/page-(?P<num>\d+)[^\s\"'<>]*?\.webp"
    )
    seen, ordered = set(), []
    for m in pat.finditer(html):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            ordered.append((m.group("num"), url))
    ordered.sort(key=lambda t: t[0])
    return [u for _, u in ordered]


# ---------- STEP 3: download via proxy translate.goog ----------
def via_google_proxy(url: str) -> str:
    """storage.vortexscans.org -> storage-vortexscans-org.translate.goog

    Format Google: titik pada host diganti '-', lalu ditambah '.translate.goog'.
    Request di-fetch oleh server Google (IP mereka tidak diblok WAF),
    respons biner gambar direlay apa adanya tanpa re-encode."""
    host_part, path_part = url.split("://", 1)[1].split("/", 1)
    proxied_host = host_part.replace(".", "-")
    return (f"https://{proxied_host}.translate.goog/{path_part}"
            f"?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en")


def is_valid_webp(b: bytes) -> bool:
    """Verifikasi magic bytes: RIFF....WEBP"""
    return len(b) > 40 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"


def download_one(idx: int, url: str, out_dir: str) -> dict:
    proxied = via_google_proxy(url)
    fname = os.path.join(out_dir, f"{idx:03d}.webp")
    last_err = None
    for attempt in range(1, 4):
        try:
            r = requests.get(proxied, headers={
                "User-Agent": UA,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://translate.goog/",
            }, timeout=120)
            if r.status_code == 200 and is_valid_webp(r.content):
                with open(fname, "wb") as f:
                    f.write(r.content)
                return {"idx": idx, "ok": True, "size": len(r.content), "file": fname}
            last_err = f"HTTP {r.status_code} len={len(r.content)}"
        except Exception as e:
            last_err = repr(e)
        time.sleep(2 * attempt)  # backoff sebelum retry
    return {"idx": idx, "ok": False, "error": last_err}


def main():
    if not CHAPTER_URL:
        print("Pemakaian: python3 vortex_pull.py <URL_CHAPTER> [folder_output]")
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)

    html = fetch_page_html(CHAPTER_URL)
    urls = extract_image_urls(html)
    if not urls:
        print("!! Tidak ada URL gambar ditemukan. Cek HTML di _chapter_page.json")
        sys.exit(1)
    print(f"[2/3] Ditemukan {len(urls)} gambar halaman")

    print(f"[3/3] Download via proxy translate.goog -> {OUT_DIR}")
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(download_one, i, u, OUT_DIR): (i, u)
                for i, u in enumerate(urls, 1)}
        for fut in as_completed(futs):
            res = fut.result()
            tag = f"p{res['idx']:03d}"
            if res["ok"]:
                print(f"   OK   {tag}  {res['size']/1024:.0f} KB")
            else:
                print(f"   FAIL {tag}  {res['error']}")
            results.append(res)

    ok = sum(1 for r in results if r["ok"])
    fail = [r for r in results if not r["ok"]]
    print("=" * 50)
    print(f"SELESAI: {ok}/{len(results)} berhasil | output: {OUT_DIR}")
    if fail:
        print("Gagal:", [r["idx"] for r in fail])
        sys.exit(2)


if __name__ == "__main__":
    main()
