# Manhwa Image Puller 🕷️📖

> Dokumentasi & script **cara menarik gambar manhwa full-resolution** dari situs agregator (studi kasus: VortexScans) yang dilindungi **Cloudflare WAF** — proyek belajar web scraping.

---

## ⚠️ Disclaimer

Proyek ini dibuat **murni untuk tujuan belajar** web scraping: bagaimana menganalisis proteksi situs, mencoba berbagai pendekatan, dan menemukan metode yang bekerja.

- Gunakan hasil script ini untuk **keperluan pribadi** (misal: membaca offline).
- Karya manhwa adalah milik **author & publisher asli**. Kredit scan group tetap wajib diberikan kalau hasilnya dipakai/disebarkan.
- Tidak ada gambar manhwa yang di-commit di repo ini — yang didokumentasikan hanya **metode & script**-nya.

---

## 🎯 Masalahnya

Situs agregator manhwa (contoh: `vortexscans.org`) dilindungi **Cloudflare WAF** yang memblokir akses dari:

- IP datacenter / VPS (curl, python requests, headless browser dari VPS)
- TLS fingerprint yang tidak dikenal (curl punya JA3 fingerprint khas yang dikenali Cloudflare)

Akibatnya, request langsung ke halaman chapter **maupun** ke subdomain storage gambarnya (`storage.vortexscans.org`) berakhir `403 Forbidden` berisi halaman *"Sorry, you have been blocked"*.

Tujuan: mendapatkan file gambar halaman chapter ** apa adanya** (full-res, tanpa screenshot, tanpa re-encode, tanpa watermark UI situs).

---

## 🧪 Hasil Eksperimen

Berbagai metode dicoba secara berurutan. Ini hasil jujurnya:

| # | Metode | Target | Hasil | Catatan |
|---|--------|--------|-------|---------|
| 1 | `curl` + UA browser biasa | halaman chapter | ❌ 403 | Halaman "Sorry, you have been blocked" |
| 2 | `curl` + header browser lengkap (`sec-ch-ua`, `sec-fetch-*`, HTTP/2) | storage gambar | ❌ 403 | Bukan soal header — blokir level IP |
| 3 | `curl` dengan UA Googlebot | homepage | ❌ 403 | WAF tidak percaya UA saja |
| 4 | Headless browser (Chromium via Playwright) | halaman chapter | ❌ 403 | IP datacenter tetap terdeteksi |
| 5 | Proxy publik `api.codetabs.com` | storage gambar | ❌ 522 | Server proxy gagal menjangkau origin |
| 6 | Proxy publik `api.allorigins.win` | storage gambar | ❌ 522 | Sama, gagal |
| 7 | Image proxy `wsrv.nl` (images.weserv.nl) | storage gambar | ❌ 400 | `Domain or TLD blocked by policy` |
| 8 | Layanan reader (`page_reader` / infrastruktur crawler) | halaman chapter | ✅ 200 | HTML lengkap diterima (146 KB) |
| 9 | **Proxy Google Translate (`*.translate.goog`)** | storage gambar | ✅ **200** | **Byte asli, tanpa re-encode!** |

**Kesimpulan:** kombinasi **#8 untuk HTML** + **#9 untuk gambar** = solusi final.

---

## 🏗️ Arsitektur Solusi

```
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1 — Ambil HTML halaman chapter                            │
│                                                                 │
│  script ──► layanan page_reader (crawler infra)                 │
│                 │                                               │
│                 └──► vortexscans.org          [200 OK ✅]       │
│                      (IP crawler tidak diblok WAF)              │
│                                                                 │
│  STEP 2 — Ekstrak URL gambar dari HTML (regex)                  │
│                                                                 │
│  https://storage.vortexscans.org/upload/series/<slug>/<uuid>/   │
│          page-0001_01_1779122687108-443780.webp                 │
│                                                                 │
│  STEP 3 — Download tiap gambar via proxy translate.goog         │
│                                                                 │
│  script ──► storage-vortexscans-org.translate.goog              │
│                 │  (Google fetch pakai IP Google)               │
│                 └──► storage.vortexscans.org  [200 OK ✅]       │
│                      byte asli .webp direlay apa adanya         │
│                                                                 │
│  STEP 4 — Validasi magic bytes RIFF....WEBP + retry 3x          │
└─────────────────────────────────────────────────────────────────┘
```

### Kenapa `translate.goog` bisa lolos?

Format proxy Google Translate versi web:

```
https://<host-dengan-titik-diganti-dash>.translate.goog/<path>?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en
```

Contoh nyata dari project ini:

```
https://storage-vortexscans-org.translate.goog/upload/series/i-was-thrown-into-an-unknown-manga/2603ba81-2b9c-4a60-b4f2-2f74492ba76b/page-0001_01_1779122687108-443780.webp?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en
```

- Server Google yang melakukan fetch ke origin (IP Google punya reputasi tinggi, tidak masuk daftar blok WAF).
- Untuk konten **biner** (gambar), Google hanya **me-relay byte apa adanya** — tidak ada terjemahan, tidak ada re-encode. Jadi file yang kita terima **identik dengan file di server**.
- Terbukti dari hasil: 53/53 file valid WebP, dimensi 800px lebar (full-res), ukuran 99 KB – 743 KB.

---

## 🚀 Cara Pakai

### Kebutuhan

- Python 3.8+
- `pip install requests`
- CLI [`z-ai`](https://www.npmjs.com/package/z-ai-web-dev-sdk) terpasang (dipakai untuk STEP 1): `npm install -g z-ai-web-dev-sdk`

### Menjalankan

```bash
# format
python3 vortex_pull.py "<URL_CHAPTER>" [folder_output]

# contoh nyata
python3 vortex_pull.py \
  "https://vortexscans.org/series/i-was-thrown-into-an-unknown-manga/chapter-1" \
  "./chapter-1"
```

Output:

```
[1/3] page_reader -> https://vortexscans.org/series/...
      OK  html_len=146688  title='I Was Thrown Into an Unknown Manga Chapter 1'
[2/3] Ditemukan 53 gambar halaman
[3/3] Download via proxy translate.goog -> ./chapter-1
   OK   p001  617 KB
   OK   p002  434 KB
   ...
==================================================
SELESAI: 53/53 berhasil | output: ./chapter-1
```

### Struktur hasil

```
chapter-1/
├── 001.webp    # halaman 1 (urutan sesuai baca)
├── 002.webp
├── ...
└── 053.webp
```

Semua file WebP valid, lebar 800px, tinggi bervariasi sesuai panjang strip halaman (format webtoon).

---

## 🔧 Troubleshooting

| Gejala | Kemungkinan sebab | Solusi |
|--------|-------------------|--------|
| `Tidak ada URL gambar ditemukan` | Situs ganti tema/struktur HTML | Buka `_chapter_page.json`, sesuaikan regex di `extract_image_urls()` |
| Banyak `FAIL` HTTP 429 | Terlalu cepat / kena rate limit proxy | Turunkan `max_workers` jadi 1–2, atau tambah `time.sleep` antar request |
| File hasil bukan gambar | Proxy mengembalikan halaman error HTML | Sudah otomatis di-retry 3x; kalau tetap gagal, coba beberapa saat lagi |
| `z-ai: command not found` | CLI z-ai belum terpasang | `npm install -g z-ai-web-dev-sdk` |

---

## 🗺️ Rencana Pengembangan

- [ ] Mode batch: tarik semua chapter sekaligus dari halaman series
- [ ] Konversi otomatis ke PNG/JPG (paket `Pillow`) untuk keperluan typesetting
- [ ] Deteksi otomatis slug series + nomor chapter dari URL
- [ ] Output PDF (gabung semua halaman jadi 1 file)
- [ ] Fallback proxy alternatif kalau `translate.goog` dibatasi

---

## 📁 Struktur Repo

```
manhwa-image-puller/
├── README.md          # dokumentasi utama (file ini)
├── vortex_pull.py     # script utama (3 tahap, siap pakai)
├── docs/
│   └── METODE.md      # penjelasan teknis mendalam per tahap
└── .gitignore
```
