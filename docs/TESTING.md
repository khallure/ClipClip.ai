# Pengujian PODCUT MultiPC

Sumber bukti Stage 2 adalah perintah dan hasil dari clone aktif dengan profil baru. Paket Phase 3A dan Quality Upgrade historis berfungsi sebagai referensi perbandingan. Isi satu baris hasil untuk tiap komputer yang benar-benar diuji.

## Input dan dependensi

| Bagian | Sumber yang diperiksa |
|---|---|
| Toolchain | Windows x64. Bootstrap memakai pin Python 3.12.16, Node 24.22.3, FFmpeg dan FFprobe 9.0.2, dan Electron 45.3.4. Cocokkan arsip dengan pin yang dipakai setup. |
| Paket Python dan Node | Lock Python desktop di `desktop/backend/requirements-phase2b.lock.txt` serta `package-lock.json` di `frontend/` dan `desktop/`. |
| Model | Manifest Whisper small di `backend/assets/models/whisper-small-manifest.json`. Siapkan ZIP offline yang cocok bila ingin memverifikasi transkripsi. Fixture impor transkrip cukup untuk menguji alur edit atau ekspor tanpa model. YuNet kecil tersedia beserta lisensinya. |
| Media | Gunakan pola gambar dan audio sintetis, atau fixture milik pengguna yang ditunjuk untuk tes. `scripts/make-sample.ps1` memakai Windows Speech dan FFmpeg lokal. Simpan media serta hasil pada profil atau run uji. |

## Perintah dari root clone

```powershell
powershell.exe -NoProfile -File .\scripts\setup.ps1
powershell.exe -NoProfile -File .\scripts\test.ps1
powershell.exe -NoProfile -File .\scripts\start.ps1 -NoBrowser
powershell.exe -NoProfile -File .\scripts\stop.ps1
powershell.exe -NoProfile -File .\scripts\build-quality-upgrade.ps1
```

Jalankan hanya tahapan yang sesuai tugas dan izin. Bootstrap menyiapkan dependensi. `test.ps1` menjalankan rangkaian isolasi default dan build frontend. Opsi `-HistoricalSuite` mencakup tes lama dengan asumsi port, paket, dan provider yang mungkin berbeda. Laporkan lingkup dan kegagalan sebenarnya. Launcher start memerlukan frontend production yang sudah dibangun.

Tes desktop dan traceback package mengikuti skrip dalam `desktop/` dan `scripts/`. Jalankan verifikator build yang dirujuk pada handoff dengan UUID run baru, dan sediakan input uji yang sesuai dengan kontrak CLI-nya. Periksa manifest paket sebelum memindahkan file.

## Kriteria penerimaan

| Skenario | Bukti yang diharapkan |
|---|---|
| Bootstrap baru | Dimulai dari folder source saja. Versi tool dan arsip cocok. Semua output setup tertulis di clone atau profilnya. |
| Root kedua | Ulangi pemeriksaan dari path lokal lain dengan konfigurasi baru. Cocokkan root, owner marker, dan lokasi output. |
| Path asing | Root, profil, link, atau redirect yang keluar dari batas yang disetujui ditolak sebelum akses tulis ke lokasi itu. |
| Port sibuk | Port 3300 atau 8300 yang dipegang proses asing ditolak, proses pemiliknya tetap berjalan. Setelah konflik selesai, start dan stop bekerja pada clone sendiri. |
| Unit dan API | Catat backend pytest, tes Node desktop, typecheck/build Next.js, dan tes API yang benar-benar dijalankan. Periksa rute health, identitas, streaming, dan error yang relevan. |
| Ekspor | FFprobe memeriksa durasi, dimensi, video, dan audio. Bila relevan, buka MP4 dan cek caption, reframing, serta subtitle pendamping. |
| Restart | Idealnya proyek sintetis tetap terlihat saat membuka ulang dengan profil yang sama. Catat bukti bahwa port dilepas ketika berhenti. |
| Paket baru | Artefak lengkap dan manifest konsisten. Catat apakah tes hanya memeriksa file, menjalankan service, atau membuka UI. Model, fitur online, tampilan UI, dan PC lain masing-masing dicatat terpisah. |
| PC B | Mulai dari source bersih. Jalankan bootstrap, tes, build, dan ekspor di PC B dengan profil baru. Catat SHA source ketika Git tersedia, versi tool, hasil, serta batas cek. |

Gunakan `PASS` bila bukti skenario tercatat, `FAIL` bila percobaan gagal, dan `NOT RUN` bila belum dijalankan. Pada setiap hasil, catat komputer, perintah, input, ID build, jumlah tes, serta lokasi bukti yang sudah disanitasi. Tes dengan transkrip sintetis memverifikasi mekanik dan kontrak. Kualitas editorial, Whisper nyata, akses YouTube, provider live, dan UI memerlukan catatan terpisah sesuai apa yang diuji.
