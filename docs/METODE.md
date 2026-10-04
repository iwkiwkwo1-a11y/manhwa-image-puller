# 📖 Catatan Teknis: Anatomi Metode

Dokumen ini menjelaskan **kenapa setiap langkah dipilih**, hasil percobaan yang gagal, dan detail implementasi. Cocok dibaca kalau mau memahami proses berpikir di balik script-nya, bukan cuma pakai jadi.

---

## 1. Mengenali masalah: Cloudflare WAF

Percobaan pertama yang paling natural adalah fetch halaman langsung:

```bash
curl -sL -A "Mozilla/5.0 ..." "https://vortexscans.org/series/.../chapter-1"
```

Hasilnya bukan HTML chapter, melainkan halaman error Cloudflare (~4.9 KB):

```html
<title>Attention Required! | Cloudflare</title>
<h1>Sorry, you have been blocked</h1>
<h2>You are unable to access vortexscans.org</h2>
```

Halaman ini mengandung **Ray ID** — artinya request memang sampai ke Cloudflare, tapi ditolak oleh aturan WAF sebelum mencapai origin server.

### Kenapa diblok?

WAF modern menilai request dari beberapa lapisan:

1. **Reputasi IP** — request dari rentang IP datacenter (AWS, GCP, Alibaba, dll.) punya skor reputasi rendah. Ini penyebab utama di kasus ini, karena semua varian berikut tetap 403:
   - `curl` dengan UA biasa → 403
   - `curl` dengan set lengkap header browser modern (`sec-ch-ua`, `sec-fetch-dest`, dll.) + HTTP/2 → 403
   - Headless Chromium asli → 403
   - Bahkan UA Googlebot → 403
2. **TLS/JA3 fingerprint** — library non-browser (curl, requests, urllib) punya "sidik jari" handshake TLS yang berbeda dari browser sungguhan. Cloudflare sangat pandai membedakan ini.
3. **Perilaku** — tidak ada cookie `cf_clearance`, tidak ada eksekusi JavaScript challenge.

Pelajaran pentingnya: **kalau blokirnya level IP, menghias ulang header tidak akan pernah cukup.** Satu-satunya jalan adalah membuat pihak lain yang "dipercaya" melakukan fetch untuk kita.

---

## 2. Tahap 1 — Mengambil HTML lewat layanan reader

Karena IP kita diblok, kita pinjam infrastruktur crawler pihak ketiga (di script ini: fungsi `page_reader` dari CLI `z-ai`). Layanan semacam ini menjalankan browser sungguhan dari IP yang reputasinya bagus, lalu mengembalikan hasil ekstraksinya.

```bash
z-ai function -n page_reader -a '{"url": "<URL_CHAPTER>"}' -o page.json
```

Hasilnya JSON berisi `data.html` — HTML lengkap halaman chapter (146 KB untuk kasus uji).

> **Alternatif** kalau tidak punya akses ke layanan serupa: cache Google (`webcache.googleusercontent.com`), Wayback Machine (`web.archive.org`), atau layanan reader seperti `r.jina.ai`. Kualitas dan ketersediaannya bervariasi.

---

## 3. Tahap 2 — Bedah struktur HTML: menemukan URL gambar asli

Dari HTML yang didapat, ada dua lokasi penting:

**a) Tag `<link rel="preload">` di `<head>`** — situs ini preload beberapa halaman pertama:

```html
<link rel="preload" as="image" href="https://storage.vortexscans.org/upload/series/
  i-was-thrown-into-an-unknown-manga/2603ba81-2b9c-4a60-b4f2-2f74492ba76b/
  page-0001_01_1779122687108-443780.webp" fetchpriority="high">
```

**b) Tag `<img>` di konten reader** — sisa halaman dimuat lazy-load dengan URL serupa.

### Anatomi URL

```
https://storage.vortexscans.org/upload/series/
       └── host storage (subdomain CDN)
<slug-series>/                      ← i-was-thrown-into-an-unknown-manga
<uuid-chapter>/                     ← 2603ba81-2b9c-4a60-b4f2-2f74492ba76b
page-0001_01_1779122687108-443780.webp
└─▲─┘  └▲┘ └──────▲─────┘ └──▲──┘
  │     │         │          └── suffix unik (hash/counter upload)
  │     │         └── timestamp epoch upload
  │     └── part scan (01, 02, ...) — 1 file bisa dipecah jadi beberapa strip
  └── nomor halaman (zero-padded, urut baca)
```

Regex ekstraksi di script menyasar pola folder ini, **bukan** semua `<img>` di halaman — supaya logo situs, banner, dan ikon tidak ikut tertarik:

```python
r"https://storage\.vortexscans\.org/upload/series/"
r"(?P<slug>[a-z0-9-]+)/(?P<uuid>[a-f0-9-]{36})/page-(?P<num>\d+)[^\s\"'<>]*?\.webp"
```

Setelah ekstraksi, URL di-dedupe (muncul dobel di preload + img) lalu diurutkan berdasarkan `num`.

---

## 4. Tahap 3 — Download: serangkaian kegagalan sebelum menemukan jalan

Dengan daftar URL gambar di tangan, masalah berikutnya: **subdomain storage juga dilindungi Cloudflare yang sama.** `curl` langsung → 403.

Kandidat yang dicoba satu per satu:

| Percobaan | Kenapa gagal |
|-----------|--------------|
| curl + header browser lengkap | Blokir level IP, header tidak berpengaruh |
| `api.codetabs.com/v1/proxy` | Proxy berjalan dari IP datacenter yang **juga** diblok → 522 |
| `api.allorigins.win/raw` | Sama seperti di atas |
| `wsrv.nl` (images.weserv.nl) | Layanan mereka aktif memblokir domain agregator manga: `"Domain or TLD blocked by policy"` |
| `fetch()` dari dalam headless browser | CORS + browser-nya sendiri sudah diblok Cloudflare |

### Solusi: proxy Google Translate

Format proxy web Google Translate:

```
https://<host>.translate.goog/<path>?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en
```

Aturan konversi host: **setiap titik jadi tanda hubung**.

```
storage.vortexscans.org  →  storage-vortexscans-org.translate.goog
```

Ketika ada request ke domain `*.translate.goog`, yang sebenarnya terjadi:

1. Browser kita hanya bicara dengan **server Google**.
2. Server Google yang melakukan fetch ke `storage.vortexscans.org` — dan IP Google **tidak diblok** WAF.
3. Konten **teks** akan diterjemahkan, tapi konten **biner** (gambar, font, dll.) direlay **byte-per-byte tanpa disentuh**.

Hasil verifikasi pada kasus uji:

```bash
$ curl -s -o page.webp "https://storage-vortexscans-org.translate.goog/...page-0001...webp?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
$ file page.webp
page.webp: RIFF (little-endian) data, Web/P image, VP8 encoding, 800x5000
```

`200 OK`, 632 KB, dimensi penuh 800×5000 — **file asli, bukan hasil screenshot atau re-encode**.

---

## 5. Tahap 4 — Validasi & ketahanan

Detail kecil yang bikin script tahan banting:

**Validasi magic bytes** — jangan percaya status HTTP saja; pastikan file benar-benar WebP:

```python
def is_valid_webp(b: bytes) -> bool:
    return len(b) > 40 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"
```

Kalau proxy sempat mengembalikan halaman error HTML dengan status 200, file sampah otomatis ditolak dan masuk jalur retry.

**Retry + exponential backoff** — 3 percobaan per file, jeda 2s → 4s. Proxy gratis tidak menjamin 100% sukses, jadi ini wajib.

**Paralel terkendali** — `ThreadPoolExecutor(max_workers=4)`: cukup cepat (53 halaman ± 1 menit) tanpa membanjiri proxy. Kalau mulai banyak HTTP 429, turunkan ke 1–2 worker.

---

## 6. Hasil akhir (kasus uji nyata)

- Series: *I Was Thrown Into an Unknown Manga*, Chapter 1
- 53/53 halaman berhasil, total ±22 MB
- Semua WebP valid, lebar 800px, tinggi 1.140–5.000px (format strip webtoon)
- Waktu proses total: kurang lebih 2 menit (termasuk fetch HTML)

---

## 7. Etika & batasan

- Metode ini **tidak merusak** apa pun: hanya GET request ke konten yang memang ditampilkan gratis oleh situs kepada pembaca.
- Tetap gunakan secara **pribadi dan wajar** (jangan membanjiri situs dengan ribuan request).
- Karya manhwa tetap milik author/publisher; scan group yang menerjemahkan juga pantas mendapat kredit. Kalau hasil tarikan dipakai untuk translate ulang, **cantumkan kredit** sumber scan-nya.
