# Handoff pengembangan MultiPC

## Status Stage 2

| Bagian | Status |
|---|---|
| Salinan awal Stage 1 | 155 file sumber terpilih tercatat cocok ukuran dan SHA-256 pada inventaris lokal Stage 1 (`stage-1/SOURCE-SHA256.json`). |
| Stage 2A (Portabilitas Backend & Isolasi Profil) | **SELESAI (Source).** `backend/app/lab_profile.py`, `backend/app/quality_dev_profile.py`, dan `backend/tests/test_runtime_isolation.py` mengenali workspace MultiPC secara dinamis melalui `podcut-dev-workspace.json` dan `.dev/profile` tanpa path PC A. `backend/app/runtime_profile.py` tetap identik 100% dengan SHA-256 Stage 1. |
| Stage 2B.1 (Manifest Toolchain & Persiapan Bootstrap) | **SELESAI (Script & Manifest).** `scripts/toolchain.lock.json` dan `scripts/setup.ps1` siap melakukan validasi fail-closed atas marker workspace, lockfile, dan SHA-256 toolchain. |
| Stage 2B.2A (Akuisisi Toolchain Terverifikasi & Preflight Bootstrap) | **SELESAI (VERIFIED & PREFLIGHT PASS).** Keempat arsip biner Windows x64 telah diunduh ke `.tools/downloads/`, dicocokkan terhadap SHA-256 resmi penerbit, dan lulus `scripts/setup.ps1 -ValidateOnly`. |
| Stage 2B.2B (Eksekusi Instalasi Bootstrap Terisolasi) | **SELESAI (PASS).** `scripts/setup.ps1 -SkipBuild` berhasil dieksekusi di PC B. `.tools/python` (`3.12.16`), `.tools/node` (`v24.22.3` + `npm 11.19.1`), `.tools/ffmpeg` (`9.0.2`), `.venv` (65 paket Python ber-hash), `.dev/profile`, `frontend/node_modules`, dan `desktop/node_modules` telah terpasang secara terisolasi. |
| Stage 2C.1 & 2C.1 FIX (Pengujian Isolasi Runtime & Profil Backend) | **SELESAI (`40 PASS`, `0 FAIL`, `0 SKIP` dalam `2,21s`).** Setelah eksekusi awal mencatat `39 PASS / 1 FAIL` karena bug lokasi fixture di `test_clone_profiles_and_installed_profiles_never_mix`, fixture diperbaiki pada `backend/tests/test_runtime_isolation.py` (tanpa mengubah implementasi backend apa pun) dan retest lulus penuh `40 / 40 PASS`. |
| Stage 2C.2A (Portable Runtime Regression Test) | **SELESAI (`20 PASS`, `0 FAIL`, `0 SKIP` dalam `2,44s`).** Seluruh 20 kasus uji `backend/tests/test_portable_runtime.py` lulus di PC B tanpa perubahan source, tanpa bytecode/cache, dan tanpa penulisan di luar `.dev/profile/temp/`. |
| Stage 2C.2B (Packaged Runtime Regression Test) | **SELESAI (`18 PASS`, `0 FAIL`, `0 SKIP` dalam `2,32s` — simulasi unit/in-process).** Seluruh 18 kasus uji `backend/tests/test_packaged_runtime.py` lulus di PC B tanpa menjalankan executable packaged sungguhan, tanpa build, tanpa perubahan source, dan tanpa penulisan di luar `.dev/profile/temp/`. |
| Stage 2C.2C (Desktop Jobhost & Windows Job Object Regression Test) | **SELESAI (`46 PASS`, `0 FAIL`, `0 SKIP` dalam `3,93s`).** Seluruh 46 kasus uji `backend/tests/test_desktop_jobhost.py` lulus di PC B, termasuk 2 uji nyata Windows Job Object + kepemilikan socket port `3300` menggunakan `.tools/node/node.exe` tanpa proses/listener tertinggal (`0`). |
| Stage 2C.3 & 2C.3 FIX (Frontend Typecheck/Build & Desktop Node Tests) | **SELESAI (`PASS`).** Frontend `tsc --noEmit` (`PASS`) & `next build --webpack` (`PASS`, `frontend/.next/BUILD_ID` & `standalone/server.js` siap). Setelah eksekusi awal desktop Node tests mencatat `9 PASS / 2 FAIL` karena fixture `profile_root` lama, 3 file tes (`boundaries.test.cjs`, `portability.test.cjs`, `frontend-traces.test.cjs`) disesuaikan tanpa mengubah `desktop/app/boundaries.cjs` (tetap identik Stage 1 SHA-256) dan retest lulus penuh **`16 / 16 PASS`** (`6,90s`). |
| Stage 2C.4 (Core Backend Feature Suites — 21 File Tes) | **SELESAI (`549 PASS`, `0 FAIL`, `2 SKIP` dalam `110,39s`).** Seluruh 21 suite fitur inti backend (API, analisis klip, durasi, caption Poppins, reframe, active speaker, ingestion, media/FFmpeg, model provisioning, AI config/provider offline) lulus tanpa 1 pun kegagalan di PC B; `2 SKIP` di `test_reframe_detection.py` wajar karena file bobot YuNet belum disalin ke `.dev/profile/models/reframe/`. |
| Stage 2C.5 (Launcher Smoke Test `start.ps1` & `stop.ps1`) | **SELESAI (`PASS`).** Perbaikan pra-uji di `scripts/start.ps1` (menambahkan `workspace_id = $script:WorkspaceId` ke `$state` + validasi fail-closed `Assert-DevState $state` sebelum `Start-Process`). `start.ps1 -NoBrowser` menyalakan backend (`127.0.0.1:8300`) & frontend (`127.0.0.1:3300`) dengan sukses (`HTTP 200` pada `/`, `/api/runtime-identity`, `/api/health`; `ffmpeg`/`ffprobe`/`whisper` = `true`; tolak `403` pada request langsung tanpa token & `Origin` palsu), lalu `stop.ps1` mematikan seluruh proses dan melepas port ke `0`. |
| Stage 2D (Uji End-to-End Video Sintetis & Auto-Face Reframe) | **SELESAI (`PASS`, `35 / 35` cek + probe YuNet Reframe + ekspor gabungan).** `scripts/verify-phase2a-flow.py` diporting ke marker MultiPC (`podcut-dev-workspace.json`) dan lulus penuh melawan Studio live `127.0.0.1:3300` (upload 45s H.264/AAC, analisis heuristik `20s`, preview & ekspor `1080x1920` Poppins Highlight `#D7F35C` + ASS/SRT/VTT, ekspor original `640x360`, range `206`, cache reuse, contact sheet visual terverifikasi). Probe tambahan `POST /reframe/analyze` (`YuNet 2023mar`, 9 sampel wajah) + ekspor `vertical` gabungan `reframe_id` + Poppins juga **`PASS`**. |
| Stage 2E (Build Paket Desktop Privat `build-quality-upgrade.ps1`) | **SELESAI (`BUILD PASS`, manifest terverifikasi).** Perbaikan kompatibilitas Windows PowerShell 5.1 pada `Invoke-BuildStep` di `scripts/build-quality-upgrade.ps1`. Build penuh selesai di `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/dist/PODCUTAI-InstallerDev-win32-x64` (`4.581` file, `1.098,64 MB`, `257` third-party notices, Electron fuses aktif, `0` `.env` bocor, `yt_dlp_ejs` & YuNet SHA-256 cocok). |
| Stage 2F (Uji Eksekusi Runtime Paket Desktop `.exe` Baru — Profil Terisolasi) | **SELESAI (`PASS` dengan 2 catatan QA jalur dalam).** `PODCUTBackend.exe --bootstrap-info`, peluncuran nyata `PODCUTAI-InstallerDev.exe --isolated-profile <uuid>`, auto-seed `backend.env` + `face_detection_yunet_2023mar.onnx` (SHA-256 cocok), UI React di dalam Electron (`/api/jobs`, `/api/health`, `/api/models/whisper` `200 OK`), proteksi `403` tanpa `x-podcut-ui-token`/`x-podcut-run-token`, verifikasi `/api/health` frozen (`packaged=true`, `build=phase3a`, `ffmpeg`/`ffprobe`/`youtube_ingest.available`/`reframe.auto_face` semua `true`), serta shutdown Windows Job Object (`clean=true`, `active_processes=0`, port `3300`/`8300` = `0`) seluruhnya terverifikasi di PC B tanpa menyentuh `%LOCALAPPDATA%` maupun `.dev/profile`. |
| Feature Branch `feat/clipclip-installer` (Rebrand `ClipClip.ai` + Windows Installer `Setup.exe`) | **SELESAI (`PASS` — ter-merge ke `main`).** Rebrand penuh ke `ClipClip.ai` (UI, judul jendela, ikon multi-resolusi `.ico`/`.png`/favicon, `ClipClip.ai.exe`, `%LOCALAPPDATA%\ClipClip.ai`), seluruh `703 / 703` tes lulus (`675` backend + `16` desktop + `12` live handoff + Next.js build), Inno Setup `7.1.1` x64 portabel terverifikasi di `.tools/inno/` (`0` key registry) & otomatis didukung `scripts/setup.ps1 -DownloadTools`, build `ClipClip.ai-win32-x64` (`1.098,85 MB`, `4.585` file, `0` `.env` bocor) + **`ClipClip.ai-Setup-1.0.0-x64.exe` (`315,39 MB`, LZMA2 solid, per-user)**, serta verifikasi runtime frozen `PASS`. |
| Live Handoff Stage H1 (Safe Auto-Publisher Audit) | **SELESAI (READ-ONLY AUDIT).** Arsitektur least-privilege (`drive.file` + one-time Picker), sanitasi fail-closed, penyimpanan DPAPI di luar Git, dan tahapan implementasi selesai diaudit tanpa mengubah file. |
| Live Handoff Stage H2A (Offline Status Schema, Sanitizer & Renderer) | **SELESAI (`12 PASS`, `0 FAIL`, `0 SKIP` dalam `0,250s`, mencakup 25 subtest sanitasi).** `docs/LIVE_STATUS.json`, paket stdlib `scripts/live_handoff/`, wrapper `scripts/publish-live-status.ps1`, dan `scripts/tests/test_live_handoff.py` lulus 100% offline dengan `0` request jaringan dan `0` perubahan `.venv`/backend/frontend/desktop. |
| Live Handoff Stage H2B+ (Bounded Docs Writer / OAuth / DPAPI / Automasi) | **NOT RUN (Menunggu Persetujuan).** Belum diimplementasikan; wajib memakai area terkelola (`BEGIN`/`END`) agar konten manual & marker `LIVE_REFRESH_TEST` di Google Docs produksi tidak terhapus. |
| Git | **AKTIF & TERSINKRON ke GitHub Privat (`origin/main` & `origin/feat/clipclip-installer`).** Seluruh pekerjaan Stage 1 → 2F + rebrand `ClipClip.ai` + Windows Installer Inno Setup 7.1.1 (`setup.ps1 -DownloadTools` auto-bootstrap) telah ter-merge bersih (*fast-forward*) ke `main` dan di-push ke `https://github.com/khallure/ClipClip.ai.git`. |
| Paket historis | Phase 3A dan Quality Upgrade V1 tersimpan sebagai referensi terlindungi dan tidak disentuh. |

## Cara berpindah sesi

1. Buka [AGENTS.md](../AGENTS.md), [arsitektur](ARCHITECTURE.md), dan [panduan pengujian](TESTING.md) di Codex atau Antigravity. Gunakan root clone yang dipilih untuk tugas tersebut.
2. Catat tujuan, lingkup izin, perubahan terakhir, hasil tes, serta satu pekerjaan yang masih terbuka. Rujuk file source dengan path relatif. Sisakan video, log, konfigurasi, dan kredensial di profil komputer yang menghasilkannya.
3. Setelah Git disetujui, gunakan satu branch per tugas sebagai unit handoff. Review diff yang dipilih dan hasil pemindaian rahasia. Commit checkpoint yang sudah bisa ditinjau dan push hanya branch yang disetujui ke repo privat. Pertahankan `main` untuk merge yang sudah direview.
4. Di komputer berikutnya, cocokkan SHA branch sebelum bekerja. Jalankan pemeriksaan minimum dari clone dan profil komputer itu. Jika komputer asal mati, gunakan checkpoint remote terakhir atau cadangan Git terenkripsi yang sudah tersedia. Catat perubahan yang masih lokal saat sesi asal berhenti.

## Pembaruan bukti

### Checkpoint `feat/clipclip-installer` — 22 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Rebranding penuh aplikasi menjadi **`ClipClip.ai`** (UI Studio, ikon multi-resolusi `.ico`/`.png`/favicon, executable `ClipClip.ai.exe`, dan folder data Windows `%LOCALAPPDATA%\ClipClip.ai`), penambahan compiler **Inno Setup 7.1.1** x64 portabel terverifikasi di `.tools/inno/`, serta pembuatan Windows Installer per-user **`ClipClip.ai-Setup-1.0.0-x64.exe`** di branch fitur baru `feat/clipclip-installer`.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA dasar | SHA remote `main`:** `feat/clipclip-installer` (cabang dari `994665449c73c23d96473a009110fa80eeb7f1af`) | perubahan fitur belum di-commit (menunggu persetujuan) | `origin/main` `994665449c73c23d96473a009110fa80eeb7f1af`.
- **Perubahan utama pada branch `feat/clipclip-installer`:**
  - **Ikon & visual installer (baru):** `desktop/assets/icon.ico` (multi-res `16..256`), `desktop/assets/icon.png`, `desktop/assets/wizard-small.bmp`, `desktop/assets/wizard-sidebar.bmp`, `frontend/app/icon.png`.
  - **Rebranding & profil data `%LOCALAPPDATA%\ClipClip.ai`:** `backend/app/runtime_profile.py` (`APP_DIRECTORY = 'ClipClip.ai'`), `backend/app/quality_dev_profile.py`, `backend/app/main.py`, `backend/app/captions.py`, `frontend/app/layout.tsx`, `frontend/app/page.tsx`, `frontend/components/ModelSetup.tsx`, `frontend/next.config.ts`, `desktop/app/boundaries.cjs` (`APP_NAME = 'ClipClip.ai'`), `desktop/app/main.cjs`, `desktop/app/package.json`, `desktop/build-desktop.mjs`, `scripts/build-quality-upgrade.ps1`, `scripts/verify-phase2a-flow.py`, serta penyelarasan fixture di `backend/tests/test_runtime_isolation.py` & `desktop/tests/{boundaries,portability,frontend-traces}.test.cjs`.
  - **Toolchain & installer Windows:** `scripts/toolchain.lock.json` (menambahkan `tools.inno_setup` `7.1.1` x64 `innosetup-7.1.1-x64.exe`, SHA-256 `8a5a6711...b3f0`, `ISCC.exe` SHA-256 `e0f9f1dc...1644`), `scripts/setup.ps1` (auto-download, verifikasi SHA-256 & `/PORTABLE=1` Inno Setup 7.1.1 dengan `/DIR` ter-quote aman untuk path berspasi), `desktop/installer/ClipClip.iss` (per-user `%LOCALAPPDATA%\Programs\ClipClip.ai`, kompresi LZMA2 solid, menjaga data pengguna di `%LOCALAPPDATA%\ClipClip.ai` saat uninstall), dan `scripts/build-installer.ps1`.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - **Regresi penuh (703 / 703 PASS):**
    - Backend full `pytest` (25 file): **`675 PASSED`, `0 FAILED`, `0 SKIPPED`** (`124,37s`).
    - Frontend `tsc --noEmit` & `next build --webpack` (termasuk route `/icon.png`): **`PASS`**.
    - Desktop Node tests (4 file): **`16 PASSED`, `0 FAILED`, `0 SKIPPED`** (`7,38s`).
    - Live Handoff tests (`scripts/tests/test_live_handoff.py`): **`12 PASSED`, `0 FAILED`, `0 SKIPPED`** (`0,45s`).
  - **Inno Setup 7.1.1 portabel:** `innosetup-7.1.1-x64.exe` (`14.336.568` B, SHA-256 cocok rilis resmi `jrsoftware/issrc`) dibongkar dengan `/PORTABLE=1` ke `.tools/inno/` (`HKCU_INNO_KEYS=0`, `setup.ps1 -ValidateOnly` **`PASS`**, `ISCC --preprocess` **`PASS`**).
  - **Build penuh `scripts/build-installer.ps1` (`BuildId: 2f609c68-5a21-4bd3-accb-283c864269bb`):** **`PASS`** (`exit 0`):
    - Paket desktop: `quality-upgrade/runs/2f609c68-5a21-4bd3-accb-283c864269bb/dist/ClipClip.ai-win32-x64` (`4.585` file, `1.098,85 MB`, `ClipClip.ai.exe`, `PODCUTBackend.exe`, `257` third-party notices, Electron fuses aktif, `DOTENV_LEAKS=0`, `EJS_COUNT=3`, `YUNET_OK=true`).
    - Installer Windows: `quality-upgrade/runs/2f609c68-5a21-4bd3-accb-283c864269bb/installer/ClipClip.ai-Setup-1.0.0-x64.exe` (**`315,39 MB`** / `330.709.350` B, SHA-256 `b99e0b09fe2eb0cdca191030ecab21b86aea9512daef0575ba2819ef2f40822e`, unsigned private build).
  - **Verifikasi runtime frozen `ClipClip.ai`:** **`PASS`** (`READ_PACKAGE_OK=true`, `--bootstrap-info` read-only mengarah ke `...\ClipClip.ai\profiles\<uuid>`, `/api/runtime-identity` `packaged=true`, `/api/health` `200` `ffmpeg=true` `ffprobe=true` `yt_avail=true` `auto_face=true`, `/` `200` judul `ClipClip.ai — Studio Klip Podcast`, `/icon.png` `200` `image/png`, tanpa token `403`, `JOB_CLOSED clean=true active_processes=0`, `HOST_EXIT=0`).
  - **Eksekusi GUI installer di Windows pengguna:** **`NOT RUN`** (siap dijalankan langsung oleh pengguna lewat Explorer/PowerShell agar jendela wizard muncul di layar utama).
- **Bukti lokal relatif:**
  - `quality-upgrade/runs/2f609c68-5a21-4bd3-accb-283c864269bb/installer/ClipClip.ai-Setup-1.0.0-x64.exe`
  - `quality-upgrade/runs/2f609c68-5a21-4bd3-accb-283c864269bb/installer/installer-manifest.json`
  - `quality-upgrade/runs/2f609c68-5a21-4bd3-accb-283c864269bb/desktop-package-manifest.json`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Pengguna mencoba langsung klik ganda `ClipClip.ai-Setup-1.0.0-x64.exe` di Windows Explorer, lalu menyetujui commit & push branch `feat/clipclip-installer` (dan opsional merge ke `main`) di GitHub privat.

---

### Checkpoint Stage 2F — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menguji eksekusi runtime nyata dari paket desktop privat hasil build Stage 2E (`quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/dist/PODCUTAI-InstallerDev-win32-x64/PODCUTAI-InstallerDev.exe` & `PODCUTBackend.exe`) menggunakan `--isolated-profile <uuid>` baru agar `%LOCALAPPDATA%` dan `.dev/profile` tidak tersentuh.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `main` | `42373a28c15c3c3e3caa542cba15171c9a41f14b` *(+3 perubahan lokal belum di-commit: `scripts/verify-phase2a-flow.py`, `scripts/build-quality-upgrade.ps1`, `docs/HANDOFF.md`)* | `origin/main` `42373a28c15c3c3e3caa542cba15171c9a41f14b`.
- **Perubahan source pada Stage 2F:**
  - Tidak ada file source implementasi maupun file tes yang diubah. Hanya `docs/HANDOFF.md` yang diperbarui.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - **Prapemeriksaan:** `scripts/stop.ps1` membersihkan record DEV lama (`exit 0`); port `3300=0`, `8300=0`.
  - **Uji 1 — Frozen `--bootstrap-info` (read-only):** **`PASS`** (`exit 0`, `schema=1`, `build=phase3a`, `profile_id=installer-dev`, `profile_root` mengarah ke `quality-upgrade/runs/1dfec1a0-.../PODCUTAI-InstallerDev/profiles/<uuid>` melalui `quality_dev_profile.py`, direktori profil belum dibuat).
  - **Uji 2 — Peluncuran nyata `PODCUTAI-InstallerDev.exe --isolated-profile 2e2a7e73-be9b-4b57-9d1e-637e168e3332`:** **`PASS` (dengan 2 catatan QA jalur dalam)**:
    - `runtime/desktop/<run_id>.json` mencapai `status=ready` (`desktop_pid=25160`, `host_pid=15508`, `backend=23540`, `frontend=11140`), `LIVE_PORT_3300=1`, `LIVE_PORT_8300=1`.
    - `desktop.log` mencatat `Verified private frontend and backend; opening isolated desktop UI.`, dan `backend.stdout.log` membuktikan UI React di dalam `BrowserWindow` Electron memuat penuh `/api/jobs`, `/api/health`, dan `/api/models/whisper` (`HTTP 200 OK`) menggunakan `x-podcut-ui-token`.
    - Auto-seed profil baru bekerja: `config/backend.env` (`YOUTUBE_INGEST_ENABLED=true`, `AI_PROVIDER=heuristic`) dan `models/reframe/face_detection_yunet_2023mar.onnx` (SHA-256 cocok `8f2383e4...2fa4`).
    - Proteksi keamanan tanpa token menolak semua request eksternal dengan **`HTTP 403`** (`GET 3300/`, `GET 3300/api/health`, `GET 8300/api/runtime-identity`).
    - Crash-safety Job Object: setelah proses Electron ditutup, `POST_TESTA_PORT_3300=0` dan `POST_TESTA_PORT_8300=0`.
    - *Catatan 1:* Jalur profil QA bersarang sangat dalam (`258` karakter) memicu peringatan non-fatal Chromium `GPUPersistentCache\DawnGraphiteCache` (`0xCE` Windows MAX_PATH) di stderr tanpa menghentikan aplikasi; tidak terjadi pada jalur instalasi/profil normal yang lebih pendek.
    - *Catatan 2:* Pemanggilan `CloseMainWindow()` otomatis pada skrip PowerShell uji mengalami timeout (window non-interaktif/judul UTF-8) sehingga ditutup via `Kill()`; shutdown normal bersih diverifikasi eksplisit pada Uji 3 di bawah.
  - **Uji 3 — Verifikasi `/api/health`, `/api/runtime-identity` & Shutdown Bersih Frozen via `--job-host` (`instance f9230be4-532a-4b68-b25b-27baeaacd3b7`):** **`PASS` (`exit 0`)**:
    - `GET http://127.0.0.1:3300/api/runtime-identity` (`x-podcut-ui-token`): **`200 OK`**, `build=phase3a`, `packaged=true`, `x-podcut-proxy-profile=installer-dev`, `run_id` cocok.
    - `GET http://127.0.0.1:3300/api/health`: **`200 OK`**, `status=ok`, `ffmpeg=true`, `ffprobe=true`, **`youtube_ingest.enabled=true` & `available=true`** (membuktikan `yt-dlp` + `yt_dlp_ejs` aktif di dalam `PODCUTBackend.exe`), **`reframe.available=true` & `auto_face=true`** (membuktikan OpenCV + PySceneDetect + auto-seeded YuNet aktif di dalam `PODCUTBackend.exe`), `whisper_setup.state=missing` (wajar untuk profil kosong baru).
    - `GET http://127.0.0.1:3300/`: **`200 OK`** (`29.555` byte); tanpa `x-podcut-ui-token` → **`403`**.
    - Shutdown `{"op":"shutdown"}`: **`JOB_CLOSED={"event":"closed","active_processes":0,"clean":true}`**, **`HOST_EXIT=0`**, `PYCACHE=0`.
- **Bukti lokal relatif:**
  - `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/PODCUTAI-InstallerDev/profiles/2e2a7e73-be9b-4b57-9d1e-637e168e3332/logs/desktop-7b186a0e-1209-4ab1-8c64-5be1448e3648/`
  - `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/PODCUTAI-InstallerDev/profiles/f9230be4-532a-4b68-b25b-27baeaacd3b7/`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Commit & push seluruh perbaikan **Stage 2D–2F** (`scripts/verify-phase2a-flow.py`, `scripts/build-quality-upgrade.ps1`, `docs/HANDOFF.md`) ke GitHub privat (`origin/main`) untuk menutup seluruh rangkaian migrasi Multi-PC Stage 2 secara lengkap dan tersinkron.

---

### Checkpoint Stage 2E — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Membangun paket desktop privat **Quality Upgrade** (Electron `45.3.4` + PyInstaller `6.22.3` + Next.js `16.4.6` standalone + FFmpeg `9.0.2`) di PC B lewat `scripts/build-quality-upgrade.ps1` ke direktori run UUID unik tanpa menjalankan executable paket baru, tanpa menyentuh paket historis Phase 3A/Quality V1, dan tanpa mengubah profil lama.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `main` | `42373a28c15c3c3e3caa542cba15171c9a41f14b` *(+3 perubahan lokal belum di-commit: `scripts/verify-phase2a-flow.py`, `scripts/build-quality-upgrade.ps1`, `docs/HANDOFF.md`)* | `origin/main` `42373a28c15c3c3e3caa542cba15171c9a41f14b`.
- **Perubahan source pada Stage 2E (2 file):**
  - `scripts/build-quality-upgrade.ps1`: Memperbaiki kompatibilitas Windows PowerShell 5.1 pada `Invoke-BuildStep` (menyetel `$ErrorActionPreference = 'Continue'` hanya selama pemanggilan native executable dengan `*>` lalu mengembalikan `'Stop'` di blok `finally`, sambil tetap *fail-closed* bila `$exitCode -ne 0`). Percobaan awal (`ed92b2b0-0941-4d32-9da2-85638d97d306`) sebelumnya gagal pada baris `INFO:` pertama PyInstaller karena perlakuan PowerShell 5.1 terhadap stderr native. AST syntax check `0` error; SHA-256 `81f9e209d6c16b048ab180efab7e71109e78ea07c70955f956acb82beb0ab7a0`.
  - `docs/HANDOFF.md`: Mencatat hasil Stage 2E.
  - Catatan: `desktop/backend/PODCUTBackend.spec`, `desktop/build-desktop.mjs`, `desktop/app/boundaries.cjs`, dan `backend/app/runtime_profile.py` tidak diubah.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - `powershell.exe -NoProfile -File scripts/build-quality-upgrade.ps1`: **`BUILD PASS`** (`exit 0`, ~11 menit; run UUID `1dfec1a0-005c-4af7-b09b-465c949c1857`):
    - `frontend-stage.log`, `next-build.log`, `pyinstaller.log`, `packaging.log`: seluruhnya **`PASS`**.
    - Output paket: `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/dist/PODCUTAI-InstallerDev-win32-x64` (`4.581` file, `1.098,64 MB` / `1.152.004.921` byte, `257` third-party notices, Electron fuses aktif, `no_installer=true`, `no_updater=true`).
    - Audit manifest (`desktop-package-manifest.json`): `PODCUTAI-InstallerDev.exe` (`249,9 MB`), `resources/app.asar`, `PODCUTBackend.exe` (`24,8 MB`), `frontend/server.js`, `node/node.exe` (SHA-256 cocok `toolchain.lock.json`), `tools/ffmpeg.exe` & `ffprobe.exe`, `face_detection_yunet_2023mar.onnx` (SHA-256 cocok `8f2383e4...2fa4`), font `Poppins-SemiBold.ttf`/`Poppins-Bold.ttf`, `yt_dlp_ejs` (`core.min.js`/`lib.min.js`), `licenses/inventory.json` seluruhnya lengkap; **`0` kebocoran file `.env`**.
    - Kebersihan workspace: `PYCACHE=0` di `backend/`, `quality-upgrade/runs/` ter-ignore penuh oleh Git.
  - Eksekusi / uji runtime paket `.exe` hasil build baru: **`NOT RUN`** (menunggu persetujuan eksplisit; DEV Studio saat ini masih aktif di port `3300`/`8300` sehingga harus dimatikan dulu sebelum menjalankan paket desktop).
- **Bukti lokal relatif:**
  - `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/desktop-package-manifest.json`
  - `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/packaging.log`
  - `quality-upgrade/runs/1dfec1a0-005c-4af7-b09b-465c949c1857/dist/PODCUTAI-InstallerDev-win32-x64/PODCUTAI-InstallerDev.exe`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Pilih prioritas berikutnya: (1) **Commit & Push perubahan Stage 2D + 2E** (`scripts/verify-phase2a-flow.py`, `scripts/build-quality-upgrade.ps1`, `docs/HANDOFF.md`) ke GitHub privat, atau (2) **Stage 2F: Matikan DEV Studio lalu uji runtime paket `.exe` baru** memakai `--isolated-profile <uuid>` agar profil lama tidak tersentuh.

---

### Checkpoint Stage 2D — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Memporting `scripts/verify-phase2a-flow.py` agar bebas dari path hardcoded PC A, lalu menjalankan verifikasi integrasi end-to-end video sintetis penuh melawan Studio lokal `http://127.0.0.1:3300` (upload, transkrip, analisis klip heuristik, preview & ekspor MP4 vertikal Poppins Highlight + sidecar, ekspor original, range `206`, contact sheet visual), ditambah probe live **Auto-Face Reframe (YuNet 2023mar)** dan ekspor vertikal gabungan `reframe_id` + Poppins.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `main` | `42373a28c15c3c3e3caa542cba15171c9a41f14b` *(+2 perubahan lokal belum di-commit: `scripts/verify-phase2a-flow.py`, `docs/HANDOFF.md`)* | `origin/main` `42373a28c15c3c3e3caa542cba15171c9a41f14b`.
- **Perubahan source pada Stage 2D (2 file):**
  - `scripts/verify-phase2a-flow.py`: Mengganti konstanta PC A (`D:/PODCUT-Labs/Installer-Dev` & `C:/Users/video/AppData/Local/PODCUTAI-InstallerDev`) dengan resolver fail-closed `_resolve_workspace()` berbasis `podcut-dev-workspace.json` (`PROJECT`, `PROJECT / '.dev' / 'profile'`). Syntax `ast.parse` OK; SHA-256 `2c306ca48f15699af24949eaada6493930f79f8de3998a40c137e9c30f794e67`.
  - `docs/HANDOFF.md`: Mencatat sinkronisasi remote GitHub privat (`origin/main`) dan hasil penuh Stage 2D.
  - Catatan pengaturan lokal `.dev/profile/` (di luar Git, atas persetujuan pengguna saat uji Studio interaktif): `YOUTUBE_INGEST_ENABLED=true` di `.dev/profile/config/backend.env` dan salinan lokal terverifikasi `face_detection_yunet_2023mar.onnx` (`8f2383e4...2fa4`) di `.dev/profile/models/reframe/`.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - `.venv\Scripts\python.exe -B scripts/verify-phase2a-flow.py`: **`35 / 35 CHECKS PASS`** (`exit 0`, ~9 detik; ID verifikasi `20261121T132344Z-63dec1b0b1`, project sintetis `268d720ff83e425bbf9e9542a3472d86`):
    - Sumber sintetis `640x360` H.264/AAC `45,0s` (`13.648.833` byte) dibuat lewat `.tools/ffmpeg` & diverifikasi `.tools/ffprobe`.
    - Upload lewat proxy `127.0.0.1:3300`, impor transkrip word-timestamp bahasa Indonesia, range playback `206`, analisis heuristik `duration_preset=20` (`2` kandidat utuh ≤ `20s`), dan cache reuse tanpa ASR ulang: **`PASS`**.
    - Ekspor `vertical-poppins` (`1080x1920`, H.264/AAC, `3,0s`, `873.990` byte) + sidecar `.ass` (`Poppins SemiBold`, `#D7F35C`), `.srt`, `.vtt` + font independen + range `206`: **`PASS`**.
    - Ekspor `original` (`640x360`, H.264/AAC, `3,0s`, `515.390` byte) + range `206`: **`PASS`**.
    - Contact sheet visual (`contact-sheet.png`) terverifikasi menampilkan crop vertikal `1080x1920` dengan caption Poppins SemiBold & highlight lime `#D7F35C` (*"Ternyata kita tidak perlu"*).
    - Isolasi filesystem & model cache sebelum/sesudah: **`PASS`** (`0` file baru di luar `.dev/profile/`, `0` `__pycache__` di `backend/`).
  - Probe live **Auto-Face Reframe + Ekspor Gabungan** pada project `268d720ff83e425bbf9e9542a3472d86`: **`PASS`** (`POST /reframe/analyze` `mode=auto_face` → `HTTP 200`, OpenCV `4.14.0.94` + PySceneDetect `0.7.3` + YuNet `2023mar` memproses `9` sampel wajah, `1` shot, `2` keyframe, `plan_id=b154c216227ed75e0762eb5812028e26`; ekspor `vertical` gabungan `reframe_id` + `poppins_highlight` → `export_id=ff5de8e7a5fd4332b3da487a75586d84`, `1080x1920`, `status=ready`).
- **Bukti lokal relatif:**
  - `.dev/profile/runtime/phase2a/20261121T132344Z-63dec1b0b1/report.json`
  - `.dev/profile/runtime/phase2a/20261121T132344Z-63dec1b0b1/contact-sheet.png`
  - `.dev/profile/runtime/phase2a/20261121T132344Z-63dec1b0b1/vertical-poppins.mp4` & `original.mp4`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Commit & push perubahan Stage 2D (`scripts/verify-phase2a-flow.py` + `docs/HANDOFF.md`) ke GitHub privat, lalu lanjut ke **Stage 2E** (porting & build paket desktop `.exe` privat via `scripts/build-quality-upgrade.ps1`) atau langsung masuk fitur produk baru.

---

### Checkpoint Git Lokal — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menginisialisasi repository Git lokal di PC B (`branch main`) menggunakan identitas repo-lokal saja (`PODCUT MultiPC Dev <podcut-local@localhost>`) tanpa mengubah konfigurasi Git global, melakukan pemindaian rahasia & path terlarang pra-commit, lalu mengamankan seluruh baseline Stage 1 → 2C.5 + H2A dalam checkpoint commit lokal sebelum menyambungkan repo remote privat.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal baseline | SHA remote:** `main` | `f603408bc63779a513b03db4606ae15e2f6ae36f` | `NOT CONFIGURED` (belum ada remote / belum push).
- **Audit pra-commit & integritas:**
  - Total file terlacak pada commit baseline: **`169` file (`2,36 MB`)** mengikuti `.gitignore`.
  - File/folder sensitif & runtime yang terverifikasi **ter-ignore (`0` masuk Git)**: `.dev/`, `.tools/`, `.venv/`, `frontend/node_modules/`, `frontend/.next/`, `frontend/tsconfig.tsbuildinfo`, `desktop/node_modules/`, `stage-1/`, semua `*.env` (kecuali `backend/.env.example`), `*.log`, `*.zip`, `*.exe`, `*.dll`.
  - Pemindaian rahasia: **`0` rahasia / token / API key nyata** (6 string dummy pada `scripts/tests/test_live_handoff.py` adalah fixture sintetis unit test sanitizer; catatan: jika nanti push ke GitHub dengan Push Protection aktif, fixture dummy tersebut bisa perlu dibuat secara dinamis bila terpicu).
  - Konfigurasi Git ditulis strictly `--local` (`.git/config`), tidak menyentuh `~/.gitconfig`.
  - Integritas working tree: `backend/app/runtime_profile.py` tetap identik Stage 1 SHA-256 (`3d1507dcb026dc2f725a393fde0c03cbc233e74030d0967079a573152e010025`); `scripts/start.ps1` tetap identik Stage 2C.5 SHA-256 (`c41276350b62a10a53f2726f8e22c982665fa6a4cb72e2abe887c9f8d64d0020`).
- **Satu langkah berikutnya (Menunggu Input Pengguna):**
  - Menerima URL repo remote privat (mis. GitHub privat) dari pengguna untuk mendaftarkan `origin` dan (setelah persetujuan eksplisit) melakukan push checkpoint `main` dengan aman.

---

### Checkpoint Stage 2C.5 — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Memperbaiki bug pra-uji pada `scripts/start.ps1` (field `workspace_id` belum disertakan di `$state`), lalu menjalankan smoke test launcher lokal (`scripts/start.ps1 -NoBrowser` → probe endpoint & proteksi keamanan → `scripts/stop.ps1`) dalam satu siklus `try/finally` tanpa menjalankan build paket desktop atau inisialisasi Git.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.5 (2 file):**
  - `scripts/start.ps1`: Menambahkan `workspace_id = $script:WorkspaceId;` ke hashtable `$state` (baris 17) agar selaras dengan kontrak `Assert-DevState` di `scripts/common.ps1:213`, serta memanggil `Assert-DevState $state` (baris 19) sebelum `Start-Process` agar kesalahan identitas gagal tertutup (fail-closed) sebelum proses apa pun diluncurkan. Parse AST PowerShell `0` error; SHA-256 `87a26103258cd03e38aca17454c69296c35edfd2b73ba43cc2956e8831993da7` → `c41276350b62a10a53f2726f8e22c982665fa6a4cb72e2abe887c9f8d64d0020`.
  - `docs/HANDOFF.md`: Mencatat hasil Stage 2C.5.
  - Catatan integritas: `scripts/stop.ps1`, `scripts/common.ps1`, dan `backend/app/runtime_profile.py` (identik SHA-256 Stage 1) tidak diubah.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Kondisi pra-uji: `port3300=0`, `port8300=0`, `.dev/profile/runtime/processes.json=False`.
  - Catatan sandbox: percobaan pertama di sandbox standar gagal sebelum meluncurkan proses karena pembatasan akses CIM (`Get-NetTCPConnection`/`Get-CimInstance`); eksekusi penuh dijalankan di luar sandbox dalam satu blok `try/finally`.
  - `scripts/start.ps1 -NoBrowser`: **`PASS`** (`exit 0`, `42,36s`; output `PODCUT Installer DEV is ready: http://127.0.0.1:3300`, `Profile: C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev\.dev\profile`).
  - Verifikasi `.dev/profile/runtime/processes.json`: `schema=2`, `profile_id=installer-dev`, `protocol=podcut-installer-dev-v1`, `project_root` & `profile_root` menunjuk ke workspace PC B, panjang `run_token=64` hex (tidak dicetak), 2 proses terdaftar (`backend` & `frontend`), listener aktif `port 3300=1` & `port 8300=1`.
  - Probe `GET http://127.0.0.1:3300/`: **`HTTP 200`** (`29.555` byte, judul `PODCUT AI — Studio Klip Podcast`).
  - Probe proxy `GET http://127.0.0.1:3300/api/runtime-identity`: **`HTTP 200`**, header `x-podcut-proxy-profile=installer-dev`, `x-podcut-proxy-run-id` cocok dengan `run_id`, tidak membocorkan header `x-podcut-run-token` (`False`), payload JSON cocok (`build=phase2a`, `port=8300`, `frontend_port=3300`, `backend_host=127.0.0.1`).
  - Probe proxy `GET http://127.0.0.1:3300/api/health`: **`HTTP 200`**, `status=ok`, `ffmpeg=true`, `ffprobe=true`, `whisper=true` (`whisper_model=small`), `ai.provider=heuristic`, `youtube_ingest.enabled=false`, `reframe.available=false` (`manual=true`, wajar karena bobot YuNet belum di-seed di mode source).
  - Uji negatif keamanan: `GET http://127.0.0.1:8300/api/runtime-identity` tanpa token → **`HTTP 403`**; `GET http://127.0.0.1:3300/api/health` dengan `Origin: http://evil.example` → **`HTTP 403`**.
  - Shutdown `scripts/stop.ps1`: **`PASS`** (`exit 0`; total siklus `69,56s`; `backend` dan `frontend` dihentikan terverifikasi).
  - Kondisi pasca-uji: `port3300=0`, `port8300=0`, `.dev/profile/runtime/processes.json=False`, proses workspace tertinggal `0`, `__pycache__`/`.pytest_cache` di `backend/` = `0`, `.dev/profile/data` = `0` item, `.git` = `False`.
  - Pemeriksaan log (`.dev/profile/logs/`): `backend.stderr.log` bersih tanpa error/traceback; `frontend.stderr.log` hanya berisi 1 peringatan informasional Next.js (`"next start" does not work with "output: standalone"` — tidak menghambat dev server); tidak ada token atau rahasia tercatat di log.
- **Bukti lokal relatif:**
  - `.dev/profile/logs/backend.stdout.log`, `.dev/profile/logs/backend.stderr.log`, `.dev/profile/logs/frontend.stdout.log`, `.dev/profile/logs/frontend.stderr.log`.
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Pilih salah satu langkah berikut sesuai prioritas pengguna: (1) **Stage 2D** uji alur klip & ekspor video sintetis end-to-end di PC B, (2) **Build Paket Desktop Privat** (`scripts/build-quality-upgrade.ps1`), atau (3) **Inisialisasi Git & Checkpoint Backup** agar pekerjaan di PC B aman tersimpan.

---

### Checkpoint Stage 2C.4 — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan ke-21 suite tes fitur inti backend di `backend/tests/` (seluruh suite backend di luar 4 suite runtime/jobhost yang sudah `124 / 124 PASS`) secara terisolasi menggunakan `.venv\Scripts\python.exe -B -m pytest -q -r sfe -p no:cacheprovider` dengan direktori `--basetemp` baru yang unik di `.dev/profile/temp/`.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.4:**
  - Tidak ada file source implementasi maupun file tes yang diubah. Hanya `docs/HANDOFF.md` yang diperbarui.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Perintah: `.venv\Scripts\python.exe -B -m pytest <21 file tes fitur inti> -q -r sfe -p no:cacheprovider --basetemp C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev\.dev\profile\temp\pytest-2c4-core-b78052cc1c684d64959f3aa1895f8a96`
  - Hasil aktual: **`549 PASSED`, `0 FAILED`, `2 SKIPPED` (durasi `110,39 detik` / `1 menit 50 detik`)**.
  - Rincian `2 SKIPPED`: `backend/tests/test_reframe_detection.py:22` dan `backend/tests/test_reframe_detection.py:97` (`Optional local reframing dependencies are not installed.` — wajar karena `face_detection_yunet_2023mar.onnx` dari `backend/assets/models/` belum di-seed ke `.dev/profile/models/reframe/` pada mode source).
  - Akumulasi total seluruh 25 file `backend/tests/test_*.py` + 4 file `desktop/tests/*.test.cjs` di PC B: **`689 PASS`, `0 FAIL`, `2 SKIP`**, ditambah frontend `tsc --noEmit` & `next build --webpack` **`PASS`**.
  - Kondisi pasca-tes: `port3300=0`, `port8300=0`, proses workspace tertinggal `0`, `0` `__pycache__`/`.pytest_cache` di `backend/`, `.dev/profile/data` tetap `0` item.
- **Bukti lokal relatif:**
  - Direktori `--basetemp` baru: `.dev/profile/temp/pytest-2c4-core-b78052cc1c684d64959f3aa1895f8a96`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Lanjutkan ke **Stage 2C.5**: uji nyalakan Studio lokal PODCUT di PC B dengan `scripts/start.ps1 -NoBrowser` (verifikasi `http://127.0.0.1:8300`, `http://127.0.0.1:3300`, `/api/runtime-identity`, `/api/health`) lalu matikan bersih dengan `scripts/stop.ps1` (memastikan port dilepas ke `0`).

---

### Checkpoint Stage 2C.3 FIX — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Memperbaiki fixture `profile_root` pada `desktop/tests/boundaries.test.cjs` dan `desktop/tests/portability.test.cjs` serta mengganti contoh username PC A pada fixture negatif `desktop/tests/frontend-traces.test.cjs` tanpa mengubah `desktop/app/boundaries.cjs`, lalu menjalankan ulang seluruh rangkaian tes Node desktop.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.3 FIX (4 file):**
  - `desktop/tests/boundaries.test.cjs`: Memisahkan fixture `PACKAGE_ROOT` (`<PROJECT>/desktop`) dan `PACKAGED_PROFILE` (`<PROFILE>/temp/desktop-boundaries-fixture/PODCUTAI-InstallerDev`) agar memenuhi kontrak independen aplikasi packaged tanpa mengubah kode keamanan.
  - `desktop/tests/portability.test.cjs`: Memakai `PACKAGED_PROFILE` (`<PROFILE>/temp/desktop-portability-fixture/PODCUTAI-InstallerDev`) pada helper `info()` untuk pengujian relokasi packaged, sambil mempertahankan pengujian `lab-boundaries.cjs` (`PROFILE`).
  - `desktop/tests/frontend-traces.test.cjs`: Mengganti literal username PC A pada fixture penolakan negatif baris 13–14 menjadi `synthetic-user` dengan kasus penolakan yang sama persis.
  - `docs/HANDOFF.md`: Mencatat retest `16 / 16 PASS` sambil mempertahankan bukti `9 PASS / 2 FAIL` sebelumnya pada Checkpoint Stage 2C.3.
  - Catatan: `desktop/app/boundaries.cjs` tidak diubah dan terverifikasi masih cocok 100% dengan SHA-256 Stage 1.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Perintah retest: `.tools\node\node.exe --test tests\boundaries.test.cjs tests\frontend-traces.test.cjs tests\portability.test.cjs tests\private-http.test.cjs` (dari `desktop/`).
  - Hasil aktual: **`16 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `6,90 detik`)**:
    - `boundaries.test.cjs`: **`6 / 6 PASS`**
    - `frontend-traces.test.cjs`: **`2 / 2 PASS`**
    - `portability.test.cjs`: **`5 / 5 PASS`**
    - `private-http.test.cjs`: **`3 / 3 PASS`**
  - Kondisi pasca-retest: `port3300=0`, `port8300=0`, proses workspace tertinggal `0`, sisa folder fixture di `desktop/` = `0`.
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Lanjutkan ke **Stage 2C.4**: uji nyalakan & matikan studio lokal PODCUT di PC B menggunakan `scripts/start.ps1 -NoBrowser`, verifikasi handshake `/api/runtime-identity` & halaman `http://127.0.0.1:3300`, lalu hentikan bersih dengan `scripts/stop.ps1` (atau jalankan tes fitur backend inti terlebih dahulu).

---

### Checkpoint Stage 2C.3 — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan typecheck & production build frontend Next.js (`frontend/`) serta menjalankan tes Node desktop (`desktop/tests/*.test.cjs`) menggunakan `.tools/node/node.exe` secara terisolasi di PC B.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.3:**
  - Tidak ada file source atau tes yang diubah (`desktop/app/boundaries.cjs` dan seluruh tes tetap utuh). Hanya `docs/HANDOFF.md` yang diperbarui; artefak build `frontend/.next` dan `frontend/tsconfig.tsbuildinfo` dihasilkan di dalam workspace.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - `npm run typecheck` (`tsc --noEmit` via `.tools/node/node.exe` + `npm-cli.js`): **`PASS`** (`0` error).
  - `npm run build` (`next build --webpack`, Next.js `16.4.6`): **`PASS`** (`Compiled successfully in 17.9s`, TypeScript `982ms`, static pages `3/3`, `frontend/.next/BUILD_ID` = `True`, `frontend/.next/standalone/server.js` = `True`).
  - `node --test tests\boundaries.test.cjs tests\frontend-traces.test.cjs tests\portability.test.cjs tests\private-http.test.cjs` (durasi `7,39s`): **`9 PASSED`, `2 FAILED`, `0 SKIPPED`**:
    - `desktop/tests/frontend-traces.test.cjs`: **`2 / 2 PASS`** (termasuk verifikasi trace graph standalone `frontend/.next` baru di PC B).
    - `desktop/tests/private-http.test.cjs`: **`3 / 3 PASS`** (loopback `127.0.0.1:3300`).
    - `desktop/tests/portability.test.cjs`: **`4 / 5 PASS`, `1 FAIL`** — gagal pada `relocated Unicode package uses only its own resources and the native-derived isolated profile` (`Error: Program resources and user data must have independent roots.` di `desktop/app/boundaries.cjs:99`).
    - `desktop/tests/boundaries.test.cjs`: **`FAIL` saat load modul** (`Error: Program resources and user data must have independent roots.` di `desktop/app/boundaries.cjs:99`, dipanggil dari baris 10).
  - **Akar masalah `2 FAIL`:** Perubahan `desktop/lab-boundaries.cjs` dari PC A mengubah `PROFILE` menjadi `<PROJECT>/.dev/profile` (sebelumnya `C:\Users\<user>\AppData\Local\PODCUTAI-InstallerDev`). Kedua tes tersebut masih mempassing `profile_root: PROFILE` ke `desktop/app/boundaries.cjs:createBoundaries()`, yang secara sengaja menegakkan aturan aplikasi packaged: basename profil harus `PODCUTAI-InstallerDev` dan tidak boleh berada di dalam `package_root`. Tidak ada bug pada `desktop/app/boundaries.cjs`.
  - Kondisi pasca-tes: `port3300=0`, `port8300=0`, proses workspace tertinggal `0`, sisa folder fixture di `desktop/` = `0`.
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Perbaiki fixture `profile_root` pada `desktop/tests/boundaries.test.cjs` dan `desktop/tests/portability.test.cjs` (serta ganti contoh literal path PC A di `desktop/tests/frontend-traces.test.cjs` baris 13–14 bila disetujui) tanpa mengubah `desktop/app/boundaries.cjs`, lalu retest `desktop/tests/*.test.cjs` hingga seluruhnya `PASS`.

---

### Checkpoint Stage 2C.2C — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan regresi terbatas Stage 2C.2C pada `backend/tests/test_desktop_jobhost.py` saja menggunakan `.venv\Scripts\python.exe -B -m pytest -v -p no:cacheprovider` dengan direktori `--basetemp` baru yang unik di `.dev/profile/temp/`, tanpa mengubah source implementasi dan tanpa menyentuh profil PODCUT lama.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.2C:**
  - Tidak ada file implementasi atau file tes yang diubah (`7 / 7` file source & konfigurasi yang dipantau identik SHA-256 sebelum dan sesudah pengujian). Hanya `docs/HANDOFF.md` yang diperbarui.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Kondisi port awal: `port3300=0`, `port8300=0`, proses workspace `0`.
  - Perintah eksekusi aktual: `.venv\Scripts\python.exe -B -m pytest backend/tests/test_desktop_jobhost.py -v -p no:cacheprovider --basetemp C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev\.dev\profile\temp\pytest-2c2c-d3fefecaaecd48e886a00dc25d36c9bf`
  - Hasil aktual: **`46 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `3,93 detik`)**.
  - Cakupan yang lulus: validasi layout frozen, penolakan perintah & injeksi environment (`9` variasi), isolasi token & `PATHEXT`, protokol JSON-line kontrol/shutdown, parser tabel TCP IPv4/IPv6 loopback, bukti kepemilikan socket & penolakan koneksi asing, serta **2 uji nyata Windows native**:
    1. `test_actual_scoped_node_descendants_are_stopped_by_their_job` (`PASS`) — meluncurkan `.tools/node/node.exe` dan anak prosesnya di dalam Windows Job Object lalu menghentikannya bersih (`active_processes == 0`).
    2. `test_actual_pinned_socket_proof_accepts_owned_node_and_rejects_foreign_job` (`PASS`) — membuktikan `GetExtendedTcpTable` + `IsProcessInJob` di port `127.0.0.1:3300` menerima Node milik job aktif dan menolak Node milik job asing.
  - Integritas pasca-tes: **`PASS`** — `port3300=0`, `port8300=0`, proses workspace tertinggal `0`, `7 / 7` file source & konfigurasi identik SHA-256, `0` `__pycache__`/`.pytest_cache`, `.dev/profile/data` tetap `0` item.
- **Bukti lokal relatif:**
  - Direktori `--basetemp` baru: `.dev/profile/temp/pytest-2c2c-d3fefecaaecd48e886a00dc25d36c9bf`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Lanjutkan ke **Stage 2C.3**: jalankan tes Node di `desktop/tests/*.test.cjs` (dengan pemeriksaan fixture terlebih dahulu karena `desktop/tests/frontend-traces.test.cjs` tercatat memiliki literal path PC A di audit Stage 1) atau lanjutkan ke `typecheck` & build Next.js (`frontend/.next`).

---

### Checkpoint Stage 2C.2B — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan regresi terbatas Stage 2C.2B hanya pada `backend/tests/test_packaged_runtime.py` menggunakan `.venv\Scripts\python.exe -B -m pytest -v -p no:cacheprovider` dengan direktori `--basetemp` baru dan unik di `.dev/profile/temp/`, tanpa build, tanpa menjalankan executable packaged sungguhan, tanpa mengubah source/tes, dan tanpa menyentuh profil PODCUT lama.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.2B:**
  - Tidak ada file implementasi atau file tes yang diubah (`14 / 14` file source & konfigurasi yang dipantau identik SHA-256 sebelum dan sesudah pengujian). Hanya `docs/HANDOFF.md` yang diperbarui.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Percobaan awal pada sandbox standar: diblokir pada tahap inisialisasi capture `pytest` (`FileNotFoundError: No usable temporary directory found` di `.dev/profile/temp/`) karena sandbox standar tidak memiliki izin tulis ke `C:\Dev\...` (bukan kegagalan tes aplikasi).
  - Perintah eksekusi aktual: `.venv\Scripts\python.exe -B -m pytest backend/tests/test_packaged_runtime.py -v -p no:cacheprovider --basetemp C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev\.dev\profile\temp\pytest-2c2b-77eb4b90f09046d0aa3928e1dd354c07`
  - Hasil aktual: **`18 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `2,32 detik`)**.
  - Cakupan kasus yang lulus: validasi layout frozen dari executable & `podcut-package.json`, penolakan 6 variasi redirect frozen sebelum penulisan direktori (`env-root`, `marker-build`, `marker-backend`, `resource-root`, `bootloader-home`, `bootloader-archive`), penolakan klaim packaged root dari mode source, kewajiban identitas launcher (`run_id` + `run_token`) sebelum penulisan, penolakan redirect request worker, mode eksplisit `worker_command` tanpa fallback `-m`, isolasi child environment & state bootloader PyInstaller, isolasi lookup binary packaged (`ffmpeg`/`ffprobe`/`node`) agar tidak mundur ke `.tools/`, kewajiban token & allowlist origin pada FastAPI `TestClient` mode packaged, seeding YuNet non-overwrite & independen, pemulihan DLL search directory pada `process_runtime.external_popen` saat gagal, penolakan mode CLI asing pada `desktop/backend/backend_entry.py`, serta validasi path model Whisper lokal tanpa unduhan.
  - Integritas pasca-tes: **`PASS`** — `14 / 14` file source & konfigurasi identik SHA-256; `0` `__pycache__`/`.pytest_cache` di `backend/` dan `desktop/backend/`; `.dev/profile/data` tetap `0` item.
- **Bukti lokal relatif & batasan verifikasi:**
  - Direktori `--basetemp` baru: `.dev/profile/temp/pytest-2c2b-77eb4b90f09046d0aa3928e1dd354c07`
  - **Batasan penting:** Seluruh 18 kasus pada `test_packaged_runtime.py` adalah pengujian unit / simulasi in-process (`monkeypatch` pada `sys.frozen`, `sys.executable`, `sys._MEIPASS`, `packaged_profile`, `subprocess.Popen`, dan FastAPI `TestClient` ASGI in-memory), **bukan** eksekusi binary PyInstaller (`PODCUTBackend.exe`) maupun Electron sungguhan. Hasil `PASS` ini membuktikan kontrak logika batas runtime packaged, bukan keberhasilan build atau peluncuran aplikasi packaged secara end-to-end.
  - Catatan: `docs/LIVE_STATUS.json` belum diperbarui pada tahap ini karena batasan Stage 2C.2B hanya mengizinkan pembaruan `docs/HANDOFF.md`.
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Lanjutkan ke **Stage 2C.2C** untuk menjalankan satu suite terisolasi berikutnya, yaitu `backend/tests/test_desktop_jobhost.py` (setelah pemeriksaan keamanan subprocess Windows Job Object & port 3300 terlebih dahulu).

---

### Checkpoint Stage H2A — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Implementasi terbatas **Stage H2A (100% OFFLINE)** untuk fondasi lokal PODCUT Live Handoff: skema allowlist ketat, sanitizer fail-closed, canonical renderer dengan marker area terkelola, CLI validasi/dry-run, wrapper PowerShell lokal, dan unit test lokal memakai Python 3.12 stdlib murni (`.tools/python/python.exe -B -I`). Tanpa OAuth, tanpa DPAPI storage, tanpa Google Docs write client, tanpa koneksi jaringan, tanpa perubahan `.venv`/backend/frontend/desktop, dan tanpa menjalankan atau mengubah status Stage 2C.2B.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan Stage H2A & file baru:**
  - `docs/LIVE_STATUS.json` *(baru)*: sumber status ringkas ber-skema allowlist (`schema_version: 1.0`, `node_label: PC-B`, `git_status: NOT CONFIGURED`, status aktual Stage 1..Stage 2C.2B+ dan Stage H1..H2B+).
  - `scripts/live_handoff/__init__.py` *(baru)*: ekspor API modul offline.
  - `scripts/live_handoff/schema.py` *(baru)*: validator allowlist ketat (menolak key ekstra/hilang, bool sebagai int, PASS palsu, tanggal invalid, path traversal) dan renderer teks kanonis deterministik dengan marker `<<<PODCUT-AUTO-STATUS:BEGIN>>>` .. `<<<PODCUT-AUTO-STATUS:END>>>` serta integritas SHA-256 (`Content-SHA256` & `Payload-SHA256`).
  - `scripts/live_handoff/sanitizer.py` *(baru)*: pemindai fail-closed (API key/token/password/private key, path absolut Windows/Linux/UNC/env var, username/hostname OS & email, cuplikan source code & perintah CLI internal, referensi profil/konfigurasi lokal, nama file media/transkrip/database, injeksi marker & `LIVE_REFRESH_TEST`, serta batas ukuran 24 KB input / 12 KB render) yang hanya mencetak kode alasan tanpa membocorkan nilai sensitif.
  - `scripts/live_handoff/cli.py` *(baru)*: CLI offline (`--validate-only`, `--dry-run`, `--json-summary`, `--write-preview` terbatas ke `.dev/profile/`) tanpa mengimpor klien HTTP/socket.
  - `scripts/publish-live-status.ps1` *(baru)*: wrapper PowerShell lokal dengan validasi marker `podcut-dev-workspace.json` untuk menjalankan validasi dan dry-run offline via `.tools/python/python.exe -B -I`.
  - `scripts/tests/test_live_handoff.py` *(baru)*: unit test lokal 100% offline berbasis `unittest` stdlib dengan fixture sementara terisolasi di dalam `.dev/profile/temp/`.
  - `docs/HANDOFF.md` *(diperbarui)*: mencatat bukti dan hasil Stage H2A tanpa mengubah status Stage 2C.2B.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - `scripts/publish-live-status.ps1 -ValidateOnly -JsonSummary` (sandbox standar): **`PASS`** (`result: PASS`, `network_mode: OFFLINE_ONLY`, `network_requests: 0`, `google_docs_modified: false`, `content_sha256: ad1cf16537b5157d4de7245e09ff0b5b4d73f5235a34d4a2fadd086cba9b843e`, `payload_sha256: d13c1b1b2682986492b1fdc48489c90cd4b0b1dcf6f16915b85241c10344d96d`, `4498 bytes`, `50 lines`, `4498 UTF-16 units`).
  - `scripts/publish-live-status.ps1 -DryRun` (sandbox standar): **`PASS`** (menghasilkan preview kanonis ter-sanitasi lengkap dengan `0` request jaringan).
  - `.tools/python/python.exe -B -I scripts/tests/test_live_handoff.py -v`: **`12 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `0,250 detik`)**, termasuk 25 subtest sanitasi (percobaan awal di sandbox standar mencatat `PermissionError` saat membuat direktori sementara di `.dev/profile/temp/`, konsisten dengan perilaku sandbox Stage 2C.1/2C.2A dan membuktikan tidak ada fallback ke Temp Windows; setelah izin tulis workspace diberikan, seluruh 12/12 tes lulus penuh).
  - OAuth, DPAPI storage, Google Docs API writer, publikasi remote, scheduler, dan **Stage 2C.2B**: **`NOT RUN`** (sesuai batas larangan Stage H2A).
- **Data yang berhasil disanitasi & integritas:**
  - Terbukti dalam unit test: penolakan tanpa kebocoran nilai untuk Google API key, OAuth secret/access/refresh token, OpenAI key, Bearer/PEM/password, email pribadi, username & hostname OS aktif maupun sintetis, path absolut Windows berspasi/slash/UNC/`%LOCALAPPDATA%`, path Linux/macOS (`/home`, `/Users`), source code/perintah CLI (`powershell.exe`, `--basetemp`), profil/config lokal (`backend.env`, `.dev/profile`, `PODCUTAI-InstallerDev`), file media/SRT/SQLite, injeksi marker `<<<PODCUT-AUTO-STATUS:END>>>` & `LIVE_REFRESH_TEST`, serta payload melebihi batas ukuran.
  - Terbukti nol koneksi jaringan: modul produksi tidak mengimpor `urllib.request`, `http.client`, atau `socket`, dan dry-run/validate-only lulus dengan monkeypatch pemblokir keras pada `socket.socket`, `socket.create_connection`, `socket.getaddrinfo`, dan `urllib.request.urlopen`.
  - Integritas pasca-tes: `0` `__pycache__` di `scripts/`, `.dev/profile/data` tetap `0` item, `.git` tetap `False`, `.venv`/backend/frontend/desktop tidak disentuh.
- **Bukti lokal relatif:**
  - Direktori fixture sementara terisolasi di `.dev/profile/temp/unittest-h2a-907e97c9709d` dan `.dev/profile/temp/unittest-h2a-e785b6235049` (tidak dilakukan cleanup sesuai larangan Stage H2A).
- **Kendala dan risiko yang masih tersisa:**
  - Sinkronisasi `docs/LIVE_STATUS.json` masih diperbarui manual per checkpoint (belum ada ekstraktor atau writer remote).
  - Konten manual & marker `LIVE_REFRESH_TEST` sudah ada di dokumen Google Docs produksi; tahap penulisan mendatang wajib memakai desain pembaruan terbatas di dalam area `<<<PODCUT-AUTO-STATUS:BEGIN>>>` .. `<<<PODCUT-AUTO-STATUS:END>>>` (fail-closed bila marker hilang/duplikat, tanpa pernah menghapus seluruh dokumen).
  - Sanitizer berbasis pola tidak dapat menggantikan disiplin kurasi manusia terhadap kalimat bebas yang menyamarkan informasi rahasia tanpa pola baku (mis. kata sandi biasa tanpa label `password=`).
  - Git belum dikonfigurasi pada workspace ini sehingga status Git tetap tercatat `NOT CONFIGURED`.
- **Tahap berikutnya yang membutuhkan persetujuan:**
  - Jalur pengembangan utama: **Stage 2C.2B** (`backend/tests/test_packaged_runtime.py`) tetap `NOT RUN` dan menunggu persetujuan terpisah.
  - Jalur Live Handoff: persetujuan **Stage H2B** untuk desain & implementasi offline bounded managed-section updater (parser/planner indeks UTF-16 yang hanya mengganti blok `<<<PODCUT-AUTO-STATUS:BEGIN>>>..END>>>` tanpa menyentuh konten manual/`LIVE_REFRESH_TEST`, lokal mutex lock, dan durable outbox offline) sebelum masuk OAuth/Google Docs pada Stage H3.

---

### Checkpoint Stage 2C.2A — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan regresi terbatas Stage 2C.2A pada `backend/tests/test_portable_runtime.py` saja menggunakan `.venv\Scripts\python.exe -B -m pytest -v -p no:cacheprovider` dengan direktori `--basetemp` baru yang unik di dalam `.dev/profile/temp/`, serta memeriksa secara read-only penyebab exit code 1 awal pada retest Stage 2C.1 dan kontrak kanonikalisasi `DATA_DIR` di `runtime_profile.py`.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.2A:**
  - Tidak ada file implementasi atau file tes yang diubah (`12 / 12` file source & konfigurasi yang dipantau identik SHA-256 sebelum dan sesudah pengujian). Hanya `docs/HANDOFF.md` yang diperbarui.
- **Temuan Pemeriksaan Awal (Read-Only):**
  1. **Penyebab exit code 1 awal pada percobaan retest Stage 2C.1:** Percobaan pertama selalu dijalankan di dalam sandbox standar (`BypassSandbox: false`) sesuai aturan eksekusi; karena tidak ada active workspace yang terdaftar di harness, sandbox standar memblokir akses tulis ke `C:\Dev\...` sehingga `tempfile` Python gagal membuka direktori sementara di `.dev\profile\temp` (`FileNotFoundError: No usable temporary directory found`) sebelum pytest mengoleksi tes. Saat dijalankan dengan izin tulis ke workspace, seluruh tes lulus penuh. Hal yang sama terkonfirmasi pada percobaan sandbox awal Stage 2C.2A dan membuktikan `tempfile` tidak mundur ke `C:\Users\<user>\AppData\Local\Temp`.
  2. **Kontrak `DATA_DIR` pada `backend/app/runtime_profile.py:374-411`:** Perilaku di mana variabel lingkungan warisan `DATA_DIR` yang masih berada di dalam `root` diterima lalu diganti dengan nilai kanonis dari `backend.env` atau default `root / 'data'` adalah **kontrak yang disengaja** (`# Application-specific inherited redirects fail closed before canonical values replace them`). `os.environ` warisan divalidasi fail-closed agar tidak keluar dari `root`, namun konfigurasi aktif selalu diambil murni dari `backend.env` (`interpolate=False`) atau default kanonis.
- **Perintah dan hasil PASS / FAIL / NOT RUN:**
  - Perintah: `.venv\Scripts\python.exe -B -m pytest backend/tests/test_portable_runtime.py -v -p no:cacheprovider --basetemp C:\Dev\PODCUT-Stage 1\PODCUT-MultiPC-Dev\.dev\profile\temp\pytest-2c2a-ce061fc91d004aebb55a708a08a3d7ac`
  - Hasil aktual: **`20 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `2,44 detik`)**.
  - Cakupan yang lulus: Windows Known Folder (`SHGetKnownFolderPath`) mengabaikan spoofing env `LOCALAPPDATA`/`USERPROFILE`/`APPDATA`, validasi UUID kanonis `--isolated-profile` (7 variasi invalid), read-only `bootstrap_info` untuk paket relokasi berkarakter Unicode/spasi, independensi frozen roots dari konstanta `lab_profile`, seeding `backend.env` bebas secret & non-overwrite, penolakan overlap program/data, isolasi API key privat dari child env, `desktop_jobhost.Layout.from_frozen` & `ServicePolicy`, penolakan hardlink tersimulasi, serta penolakan mismatch CLI/env pada `desktop/backend/backend_entry.py`.
  - Integritas pasca-tes: **`PASS`** — `12 / 12` file source & konfigurasi identik SHA-256; `0` `__pycache__`/`.pytest_cache` di `backend/` dan `desktop/backend/`; `.dev/profile/data` tetap `0` item.
- **Bukti lokal relatif:**
  - Direktori `--basetemp` baru: `.dev/profile/temp/pytest-2c2a-ce061fc91d004aebb55a708a08a3d7ac`
- **Satu langkah berikutnya (Menunggu Persetujuan):**
  - Lanjutkan ke **Stage 2C.2B** untuk menjalankan `backend/tests/test_packaged_runtime.py` secara terisolasi di PC B.

---

### Checkpoint Stage 2C.1 FIX — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Perbaikan terbatas pada fixture `test_clone_profiles_and_installed_profiles_never_mix` di `backend/tests/test_runtime_isolation.py` dan retest terisolasi tanpa mengubah implementasi backend mana pun.
- **Komputer dan agent:** PC B — Antigravity.
- **Branch | SHA lokal | SHA remote:** `NOT CONFIGURED` | `N/A` | `N/A` (Git belum diinisialisasi).
- **Perubahan source pada Stage 2C.1 FIX (hanya 2 file sesuai izin):**
  - `backend/tests/test_runtime_isolation.py`: Mengganti path fixture `installed_profile` dengan path sintetis non-eksisten di dalam workspace tetapi di luar `.dev/profile` (`outside_profile = config.PROJECT / 'synthetic-outside-profile' / runtime_profile.APP_DIRECTORY`), dan memverifikasi penerimaan `DATA_DIR` valid di dalam `.dev/profile` vs penolakan `DATA_DIR` di luar `.dev/profile`.
  - `docs/HANDOFF.md`: Mencatat hasil retest `40 / 40 PASS` sambil mempertahankan catatan bukti `FAIL` sebelumnya pada Checkpoint Stage 2C.1.
- **Hasil aktual:** **`40 PASSED`, `0 FAILED`, `0 SKIPPED` (durasi `2,21 detik`)**.

---

### Checkpoint Stage 2C.1 (Eksekusi Awal — Bukti FAIL Historis Dipertahankan) — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Menjalankan pengujian terbatas Stage 2C.1 (`backend/tests/test_runtime_isolation.py`).
- **Hasil awal:** **`39 PASSED`, `1 FAILED`, `0 SKIPPED` (durasi `2,40 detik`)** — `test_clone_profiles_and_installed_profiles_never_mix` gagal di baris 163 karena fixture `installed_profile` berada di bawah `tmp_path` (di dalam `.dev/profile/temp/`). Diselesaikan pada Checkpoint Stage 2C.1 FIX.

---

### Checkpoint Stage 2B.2B — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Eksekusi terbatas Stage 2B.2B (`powershell.exe -NoProfile -File .\scripts\setup.ps1 -SkipBuild`).
- **Hasil:** `PASS` untuk ekstraksi `.tools/`, pembuatan `.venv` (65 paket Python ber-hash, `Diff count: 0`, `pip check: 0`), inisialisasi `.dev/profile`, serta `npm ci` untuk `frontend/` dan `desktop/`.

---

### Checkpoint Stage 2B.2A — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Akuisisi arsip toolchain Windows x64 dari sumber resmi ke `.tools/downloads/`, verifikasi checksum terhadap manifest penerbit, dan preflight `scripts/setup.ps1 -ValidateOnly`.
- **Hasil:** `PASS` untuk unduhan dan verifikasi SHA-256 keempat arsip di `.tools/downloads/` serta `setup.ps1 -ValidateOnly`.

---

### Checkpoint Stage 2B.1 — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Implementasi terbatas Stage 2B.1 (Windows Toolchain Manifest & Bootstrap Preparation) di PC B tanpa instalasi.
- **Hasil:** `PASS` untuk pemeriksaan sintaks PowerShell, format JSON `scripts/toolchain.lock.json`, konsistensi lockfile, serta penolakan `UNVERIFIED` dan SHA-256 salah pada `setup.ps1 -ValidateOnly`.

---

### Checkpoint Stage 2A — 21 November 2026 (PC B)

- **Tujuan dan batas persetujuan:** Implementasi terbatas Stage 2A (Backend Portability & Profile Isolation) di PC B.
- **Hasil:** `PASS` untuk penghapusan path PC A pada `backend/app/lab_profile.py`, `backend/app/quality_dev_profile.py`, dan `backend/tests/test_runtime_isolation.py` (`backend/app/runtime_profile.py` tetap identik 100% dengan SHA-256 Stage 1).

Setiap checkpoint berikutnya memakai format berikut:

```text
Tujuan dan batas persetujuan
Komputer dan agent
Branch | SHA lokal | SHA remote
Perubahan source
Perintah dan hasil PASS / FAIL / NOT RUN
Bukti lokal relatif dan batas verifikasi
Kendala
Satu langkah berikutnya
```
