# ClipClip.ai

**Studio klip podcast dan video pendek AI berarsitektur *local-first* untuk Windows x64.**

`ClipClip.ai` memadukan antarmuka **Next.js** dengan layanan pemrosesan **FastAPI + FFmpeg + Whisper + ONNX Runtime + Remotion**, berjalan secara lokal dan terisolasi tanpa mengirim berkas video Anda ke cloud.

---

## Fitur Utama

### 1. Analisis Klip & Transkripsi Lokal
- **Penilaian Editorial 5 Dimensi:** Menemukan potongan podcast/video yang utuh secara konteks (*Hook Strength* 30%, *Content Payoff* 25%, *Standalone Clarity* 20%, *Emotional Impact* 15%, *Edit Readiness* 10%).
- **Mode Heuristik & AI Opsional:** Berjalan 100% offline dengan analisis heuristik lokal, atau menggunakan API key **Gemini** / **OpenAI** milik Anda sendiri (hanya mengirim teks transkrip saat analisis).
- **Transkripsi Per-Kata Lokal:** Menggunakan `faster-whisper` (`small`, offline/terverifikasi manifest) dengan dukungan deteksi otomatis campuran, `id`, `en`, `es`, dan `hi` (+ transliterasi Latin *Hinglish* serta kosakata khusus `vocab`), atau impor langsung `.json`/`.srt`.
- **Input Fleksibel:** Unggah video (`MP4`, `MOV`, `MKV`, `WEBM`, `AVI`), audio saja (`MP3`, `WAV`, `M4A`, `AAC`, `FLAC`, `OGG` dengan kanvas `9:16`, `16:9`, `1:1`, `4:5`), atau ambil dari URL YouTube bila diaktifkan.

### 2. Subtle Motion Captions (`39` Gaya Animasi + Live Preview 60 FPS)
Terintegrasi dari [`0xpratzyy/subtle`](https://github.com/0xpratzyy/subtle) (MIT) dengan dukungan penuh untuk Studio interaktif dan ekspor sinematik:
- **Katalog Lengkap:** `39` preset JSON (`35` talking-head + `4` musik/lirik) + `3` varian warna (`ticket:yellow`, `singalong:lit`, `chalkline:marker`) + editor **Custom JSON Recipe** + `30` berkas font `.ttf` OFL bawaan.
- **Pratinjau 60 FPS & Klik-Kata Interaktif:** Live overlay langsung di atas pemutar video dengan penyesuaian *real-time* (aksen warna, ukuran, posisi `Y`, *case*, bayangan, dan sunting teks per halaman) serta toggle *highlight* manual per kata.
- **Pemrosesan Cerdas:** Normalisasi angka & mata uang terucap (`EN`/`HI`/`ES`/`ID` termasuk `Rp`, `juta`, `ribu`, `persen`), *hard-cut snapping*, deteksi *beat* musik & penyelarasan lirik, *auto-palette* warna video, *auto-legibility* kontras WCAG, serta efek *frosted-glass backdrop blur*.
- **Segmentasi ONNX Windows & *Behind-the-Speaker*:**
  - **Tier-1 (Bawaan):** `mediapipe_selfie_segmentation` (`selfie_segmentation.onnx`, Apache-2.0) untuk *Smart Placement* anti-wajah dan efek teks 3-layer di belakang pembicara.
  - **Tier-2 (Opsional On-Demand):** Unduhan terverifikasi SHA-256 untuk `Xenova/modnet` (Apache-2.0) untuk potongan subjek/rambut lebih halus serta pratinjau *live cutout* WebGL.
- **Ekspor & QA:** Render layer transparan **ProRes 4444 Alpha** via headless Remotion (`remotion@4.0.534`), komposit **FFmpeg** `gblur`/`alphamerge`/3-layer, sidecar `SRT`, `VTT`, `captions.json`, dan contact sheet inspeksi visual `preview.jpg`.

### 3. Fitur Inti ClipClip.ai Lainnya
- **Poppins Highlight Subtitle:** Preset subtitle word-level `Poppins` (`ASS`/`SRT`/`VTT`) beserta varian `Clean`, `Bold`, dan `None`.
- **Auto-Face Smart Reframe (`9:16`):** Deteksi shot & wajah lokal berbasis `YuNet 2023mar` + `PySceneDetect` dengan editor *keyframe* per shot.
- **Packaging Desktop & Auto-Updater:** Dibungkus sebagai aplikasi desktop **Electron** + installer per-user **Inno Setup 7** lengkap dengan **Built-in Update Checker** HTTPS-only & verifikasi SHA-256 ganda (Python + Electron).

---

## Struktur Proyek

| Direktori | Peran |
|---|---|
| [`frontend/`](frontend) | Aplikasi Studio Next.js 16 (React 19, `SubtleOverlay`, `SubtleStudioPanel`, `PoppinsCaptions`, `ReframeEditor`, `UpdatePanel`) |
| [`backend/`](backend) | Server FastAPI, worker media FFmpeg/Whisper, mesin `app/subtle/`, vision ONNX (`subtle_vision.py`, `subtle_models.py`), updater, dan aset bawaan |
| [`subtle-renderer/`](subtle-renderer) | Paket Remotion headless (`remotion@4.0.534`, `build-bundle.mjs`, `render.mjs`) untuk render ProRes 4444 alpha & layer mask |
| [`desktop/`](desktop) | Wrapper Electron (`main.cjs`, isolasi profil & port, `build-desktop.mjs`, `license-inventory.mjs`) dan skrip PyInstaller + Inno Setup |
| [`scripts/`](scripts) | Otomasi bootstrap, launcher lokal, verifikasi E2E, dan build installer Windows |
| [`docs/`](docs) | Dokumentasi arsitektur, kontrak API, panduan pengujian, dan catatan handoff |

---

## Cara Menjalankan Secara Lokal (Windows x64)

### Prasyarat
- Windows 10/11 x64 dengan PowerShell.
- Koneksi internet pada **pemasangan pertama** (hanya untuk mengunduh toolchain terverifikasi & dependency dari lockfile ke folder lokal proyek `.tools/`, `.venv/`, `node_modules/`, serta Chrome Headless Shell ke `subtle-renderer/`).

### Langkah Menjalankan
1. **Clone repository:**
   ```powershell
   git clone https://github.com/khallure/ClipClip.ai.git
   cd ClipClip.ai
   ```
2. **Bootstrap toolchain & dependensi terisolasi:**
   ```powershell
   powershell.exe -NoProfile -File .\scripts\setup.ps1
   ```
   Perintah ini memverifikasi checksum SHA-256 toolchain (`Python 3.12`, `Node.js 24`, `FFmpeg 9`, `Inno Setup 7`), memasang `.venv` dari `scripts/requirements-windows-cp312.lock.txt`, memasang dependency `frontend/`, `desktop/`, serta membangun bundle `subtle-renderer/`.
3. **Jalankan Studio lokal:**
   ```powershell
   powershell.exe -NoProfile -File .\scripts\start.ps1
   ```
   Studio akan terbuka di `http://127.0.0.1:3300` (backend FastAPI di `127.0.0.1:8300`). Gunakan `-NoBrowser` bila ingin menyalakan layanan tanpa membuka browser, dan hentikan layanan lewat:
   ```powershell
   powershell.exe -NoProfile -File .\scripts\stop.ps1
   ```
4. **Jalankan seluruh suite pengujian:**
   ```powershell
   powershell.exe -NoProfile -File .\scripts\test.ps1
   ```

---

## Konfigurasi Opsional & Model Transkripsi

- Seluruh pengaturan runtime tersimpan lokal di luar folder source (`.dev/profile/config/backend.env` pada mode source, atau `%LOCALAPPDATA%\ClipClip.ai\config\backend.env` pada mode instalasi Windows).
- Untuk transkripsi ucapan lokal (`Whisper small`), unduh atau impor paket offline ZIP langsung melalui panel **Model Setup** di antarmuka Studio.
- Mode default memakai analisis klip heuristik offline. Untuk memakai **Gemini** atau **OpenAI**, atur `AI_PROVIDER`, `GEMINI_API_KEY` / `OPENAI_API_KEY` di `backend.env` lokal Anda (**jangan pernah menyertakan berkas `.env` ke Git**).

---

## Build Aplikasi Desktop & Installer Windows (Opsional)

Sesudah bootstrap `scripts\setup.ps1` selesai:

```powershell
# Membangun aplikasi desktop portabel (Electron + PyInstaller + frontend standalone + subtle-renderer)
powershell.exe -NoProfile -File .\scripts\build-quality-upgrade.ps1

# Membangun installer ClipClip.ai-Setup-*.exe + manifest update latest.json via Inno Setup 7
powershell.exe -NoProfile -File .\scripts\build-installer.ps1
```

---

## Privasi, Keamanan & Isolasi

- **Isolasi Profil & Data:** Data proyek, cache model, dan file media pengguna disimpan di `.dev/profile/` (development) atau `%LOCALAPPDATA%\ClipClip.ai\` (terpasang), terpisah dari kode sumber maupun direktori instalasi program.
- **Tanpa Telemetri Tersembunyi:** Tidak ada kunci API atau kredensial yang ikut terkirim ke Git; unduhan model (`Whisper`, `MODNet`) dan pembaruan aplikasi mewajibkan HTTPS dengan verifikasi SHA-256 ketat (*fail-closed*).

---

## Atribusi & Lisensi Pihak Ketiga

Proyek ini mengintegrasikan dan mendistribusikan pemberitahuan komponen sumber terbuka berikut sesuai ketentuan lisensinya masing-masing:
- **Subtle** ([`0xpratzyy/subtle`](https://github.com/0xpratzyy/subtle)) — MIT License ([`LICENSE.Subtle-MIT.txt`](backend/assets/licenses/LICENSE.Subtle-MIT.txt), [`THIRD_PARTY.Subtle.md`](backend/assets/licenses/THIRD_PARTY.Subtle.md)).
- **MediaPipe Selfie Segmentation & MODNet ONNX** — Apache-2.0 License ([`LICENSE.Segmentation-Apache-2.0.txt`](backend/assets/models/segmentation/LICENSE.Segmentation-Apache-2.0.txt)).
- **Keluarga Font Subtitle (`30` berkas `.ttf`)** — SIL Open Font License 1.1 (`OFL-*.txt` di [`backend/assets/subtle_fonts/`](backend/assets/subtle_fonts) dan [`backend/assets/fonts/OFL.txt`](backend/assets/fonts/OFL.txt)).
- **YuNet Face Detection** (`face_detection_yunet_2023mar.onnx`) & **OpenAI Whisper** — MIT License ([`LICENSE.YuNet.txt`](backend/assets/models/LICENSE.YuNet.txt), [`LICENSE.OpenAI-Whisper.txt`](backend/assets/models/LICENSE.OpenAI-Whisper.txt)).
- **Remotion**, **FFmpeg**, **Next.js**, **FastAPI**, **PyAV**, **ONNX Runtime**, serta dependensi Python/Node lainnya — detail lengkap dikumpulkan otomatis oleh [`desktop/license-inventory.mjs`](desktop/license-inventory.mjs) saat packaging.

---

## Dokumentasi Lanjutan

- [Panduan Aturan & Boundary Pengembangan](AGENTS.md)
- [Arsitektur Sistem](docs/ARCHITECTURE.md)
- [Kontrak API Backend](docs/API_CONTRACT.md) & [API Clipping Upgrade](docs/CLIPPING_UPGRADE_API.md)
- [Model Reframing & Segmentasi](docs/REFRAME_MODELS.md)
- [Evaluasi Kualitas Editorial](docs/EDITORIAL_EVAL.md)
- [Panduan Pengujian](docs/TESTING.md) & [Catatan Handoff](docs/HANDOFF.md)
