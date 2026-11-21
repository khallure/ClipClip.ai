# Aturan kerja PODCUT MultiPC

Berlaku untuk Codex, Antigravity, dan perubahan manual di workspace ini. Baca dokumen ini secara eksplisit saat memulai sesi dan saat berpindah PC.

1. Kerjakan source di clone yang aktif. Cocokkan direktori kerja dengan `podcut-dev-workspace.json` sebelum menjalankan perintah. Simpan toolchain di `.tools/`, environment Python di `.venv/`, dan data runtime di `.dev/profile/` milik clone itu.
2. Lindungi PODCUT original, salinan Installer-Dev, paket Phase 3A, Quality Upgrade V1 historis, Validation Kit, paket transfer, dan profil pengguna. Perlakukan semua lokasi itu sebagai referensi baca saja. Gunakan input sintetis atau media yang diberi izin secara khusus.
3. Pertahankan pemeriksaan batas path, kepemilikan profil dan proses, serta pengikatan server ke `127.0.0.1`. Jika port dipakai instans asing, laporkan pemiliknya bila aman dan tunggu instans itu ditutup secara normal. Batasi penghentian proses ke catatan milik launcher clone aktif.
4. Simpan rahasia, konfigurasi lokal, model besar, video, transkrip, hasil, ZIP, dan log di luar source yang akan dikirim. Periksa daftar file dan diff yang dipilih sebelum commit atau push yang disetujui. Untuk laporan, cantumkan status dan lokasi relatif bukti tanpa nilai token atau data pengguna.
5. Gunakan persetujuan tahap aktif sebagai batas kerja. Minta persetujuan eksplisit sebelum menjalankan Git, mengirim source, mengubah keamanan Windows, mengaktifkan provider live, memakai media pribadi, menjalankan paket baru, atau menghapus data. Pengujian memakai heuristik lokal dan fixture yang telah disetujui.
6. Pertahankan perilaku clipping dan batas keamanan ketika mengubah toolchain atau path. Jalankan tes yang relevan. Tandai hasil `PASS`, `FAIL`, atau `NOT RUN` per skenario dan per PC.

- [README](README.md) untuk bootstrap dan titik masuk.
- [Arsitektur](docs/ARCHITECTURE.md) untuk peta komponen dan batas runtime.
- [Pengujian](docs/TESTING.md) untuk perintah, fixture, dan kriteria lulus.
- [Handoff](docs/HANDOFF.md) untuk status pekerjaan dan perpindahan PC.
