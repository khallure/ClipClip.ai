# Model dan kompatibilitas Smart Reframe — Milestone 1

Catatan versi: uraian ini adalah baseline M1. Model audio-visual opsional yang kini
tersedia, lisensi weights terpisah, dan pengujian CUDA dibahas pada
[laporan Milestone 2](ACTIVE_SPEAKER_M2_VERIFICATION.md).

17 November 2026. Fitur ini memakai model lokal untuk wajah dan algoritma perubahan shot. Tidak ada panggilan Gemini/OpenAI, transkripsi ulang, atau identifikasi pembicara aktif dalam analisis reframing M1.

| Komponen | Versi / lisensi diperiksa | Keputusan |
|---|---|---|
| OpenCV Python | 4.14.0.94; [OpenCV 4.x Apache 2.0](https://opencv.org/license/) | CPU DNN untuk YuNet; satu distribusi cv2, tidak memasang headless bersamaan |
| PySceneDetect | 0.7.3; [BSD 3-Clause](https://github.com/Breakthrough/PySceneDetect/blob/main/LICENSE) | ContentDetector untuk hard cut, ambang 22, panjang minimum shot sekitar 0,12 detik |
| YuNet | `face_detection_yunet_2023mar.onnx`; [MIT model directory](https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/LICENSE) | 232.589 byte, disertakan lokal bersama lisensi dan manifest sumber/hash |
| TalkNet | Repositori [TalkNet-ASD](https://github.com/TaoRuijie/TalkNet-ASD) menampilkan lisensi MIT dan contoh lingkungan Python 3.7.9 | Belum diintegrasikan. Perlu port/tes Python 3.12/Windows, PyTorch/CUDA, dependensi demo, serta pemeriksaan asal dan ketentuan bobot pretrained yang diambil terpisah |
| MediaPipe Face Landmarker / pyannote | Tidak dipasang | Tidak diperlukan untuk M1. Gerak mulut tidak dipakai sebagai bukti berbicara. Diarization saja tidak mengaitkan suara dengan wajah; model opsional yang dipilih kelak perlu diperiksa ketentuan/token Hugging Face-nya |

Model YuNet ada di `backend/assets/reframe/` dengan `LICENSE.YuNet` dan `manifest.json`. SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. Sumber bobot adalah mirror resmi [OpenCV Zoo](https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx); tidak membutuhkan token Hugging Face. Aplikasi memeriksa hash dan tidak mengambil bobot dari internet saat analisis.

## Windows dan GPU

Teruji pada Python 3.12.14 Windows, OpenCV 4.14.0.94, PySceneDetect 0.7.3, NumPy yang sudah terpasang, serta FFmpeg portable proyek. `pip check` tidak menemukan dependency yang rusak. Dependensi opsional dipin di `backend/requirements-reframe.txt`. Launcher Setup sekarang menyertakan `-WithReframe`; mode manual dan clipping V1 tidak memerlukan detektor ini.

Loader nama file OpenCV gagal pada path folder beremoji. Implementasi membaca ONNX ke buffer memori memakai overload resmi `FaceDetectorYN.create`, sehingga model tetap bisa dipakai pada lokasi proyek sekarang. Proxy H.264 analisis berada di folder temporary dan dibuang setelah selesai; sumber AV1/4K asli dibaca lewat FFmpeg dan tidak diganti proxy saat ekspor.

Build OpenCV terpasang melaporkan `cuda.getCudaEnabledDeviceCount()=0` dan tidak menawarkan target DNN CUDA, walaupun PC memiliki RTX 3070. Kode M1 memilih `DNN_BACKEND_OPENCV/DNN_TARGET_CPU` secara eksplisit. Tidak ada klaim akselerasi NVIDIA yang sudah teruji. Analisis CPU 60 detik sumber 4K memerlukan sekitar 27,38 detik pada uji awal; analisis 30 detik melalui aplikasi memerlukan 14,3 detik pada run terukur. Ini pengukuran contoh, bukan jaminan semua video.

## Batas makna confidence

`face_samples[].faces[].confidence` adalah skor deteksi wajah YuNet. `track_id` hanya ID track lokal pada satu shot dan direset setelah cut; bukan identitas pembicara lintas kamera. `speaker_confidence` harus `null` pada M1 dan validasi API menolak angka buatan di kolom itu.

Lebih dari satu wajah berarti komposisi ambigu: default Focus Both menampilkan seluruh sumber dalam kanvas 9:16 dengan letterbox dan tanda review. Pengguna bisa memilih kiri/kanan atau menggambar jalur manual. Tanpa wajah, framing tengah stabil ditahan dan diberi tanda review. Ketika satu track terhalang atau identitas tracking berubah, sistem menahan posisi daripada berpindah ke wajah lain tanpa bukti.

Kode: `backend/app/reframe_detection.py` (`capabilities`, `_face_detector`, `_track_sample`, `_compose_shot`, `analyze_reframe`). Deteksi wajah sekitar 3 sampel/detik, tracking IoU dan filter temporal di dalam shot. Analysis proxy mempertahankan jam FPS nominal untuk shot detection; pengujian input VFR yang lebih luas masih diperlukan. Ekspor tetap memakai sumber asli dan tidak menambahkan FPS resampling.

Milestone 2 akan memerlukan model audio-visual yang benar-benar menerima audio dan track wajah, kalibrasi confidence/hold time, serta kasus pembicara menyela dan occlusion. Tidak ada fitur M2 yang ditampilkan seolah sudah bekerja: pilihan Active Speaker dan Follow Active Speaker dinonaktifkan dengan penjelasan.
