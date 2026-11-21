# PODCUT AI MultiPC Dev

Workspace pengembangan Windows x64 untuk PODCUT Quality Upgrade V1. Next.js menyediakan studio lokal. FastAPI mengelola proyek, transkripsi atau impor transkrip, rekomendasi klip, subtitle Poppins, reframing, dan ekspor MP4 dengan FFmpeg. Buka [aturan kerja](AGENTS.md) sebelum memakai Codex atau Antigravity dari workspace ini.

## Siapkan clone

1. Buka PowerShell biasa di root clone. Pilih folder lokal dengan akses tulis, di luar direktori paket, profil, atau data PODCUT lama.
2. Jalankan bootstrap yang diizinkan oleh kebijakan Windows.

   ```powershell
   powershell.exe -NoProfile -File .\scripts\setup.ps1
   ```

3. Bootstrap mengambil toolchain Windows x64 yang dipatok dan memasang paket dari lockfile. Ia membuat `.tools/`, `.venv/`, `node_modules/`, dan `.dev/profile/` milik clone tersebut. Siapkan koneksi untuk mengunduh dependensi pada pemasangan pertama.
4. Siapkan frontend untuk launcher dan jalankan tes isolasi.

   ```powershell
   powershell.exe -NoProfile -File .\scripts\test.ps1
   ```

5. Buka studio lewat launcher. Jika Phase 3A atau Quality lain sedang memakai port 3300 atau 8300, tutup instans tersebut secara normal terlebih dahulu.

   ```powershell
   powershell.exe -NoProfile -File .\scripts\start.ps1
   ```

   Studio tersedia di `http://127.0.0.1:3300`. Gunakan `start.ps1 -NoBrowser` untuk pemeriksaan tanpa membuka browser. Tutup layanan clone ini dengan `powershell.exe -NoProfile -File .\scripts\stop.ps1`.

Jika Windows menolak perintah, simpan pesan error dan lanjutkan hanya dengan kebijakan perangkat yang diizinkan. Detail hasil PC A dan PC B dicatat di [pengujian](docs/TESTING.md).

## Uji alur clipping

- Pakai video lokal sintetis dan transkrip fixture untuk pemeriksaan pertama. Untuk transkripsi, impor ZIP Whisper small yang cocok dengan manifest melalui panel setup model.
- Pilih rekomendasi atau rentang manual. Ekspor MP4 dengan opsi vertikal, Poppins Highlight, dan Smart Reframe jika komponen lokal siap. Buka hasil ekspor dan periksa durasi, gambar, suara, serta subtitle.
- Untuk uji ingestion yang sudah diizinkan, gunakan sumber dapat diakses yang hak penggunaannya jelas. Fallback upload tersedia jika sumber link tidak dapat diproses.
- Mode source memakai heuristik lokal untuk analisis klip. Konfigurasi runtime ada di `.dev/profile/config/backend.env`. Gemini dan OpenAI merupakan jalur provider terpisah, di luar pengujian live Stage 2.

## Build paket privat

Jalankan sesudah bootstrap dan sesuai persetujuan tahap aktif.

```powershell
powershell.exe -NoProfile -File .\scripts\build-quality-upgrade.ps1
```

Cari direktori build baru di `quality-upgrade/runs/<uuid>/`. Folder itu berisi paket portabel, manifest, dan bukti build. Gunakan [panduan pengujian](docs/TESTING.md) untuk memeriksa paket sebelum meluncurkan atau memindahkannya. Paket lama tetap di lokasi asal.

Lihat [arsitektur](docs/ARCHITECTURE.md), [kontrak API](docs/API_CONTRACT.md), [QA editorial](docs/EDITORIAL_EVAL.md), dan [status handoff](docs/HANDOFF.md) untuk melanjutkan tugas di komputer lain.
