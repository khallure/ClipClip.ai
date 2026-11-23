# Subtle Third-Party Attributions & Notices

ClipClip.ai integrates caption presets, timing/grouping/emphasis/legibility algorithms,
React/SVG style components, and open-licensed fonts from `0xpratzyy/subtle` (Refs):

- Subtle code (`https://github.com/0xpratzyy/subtle`): MIT License — Copyright (c) 2026 Refs (`LICENSE.Subtle-MIT.txt`).
- Bundled fonts (`backend/assets/subtle_fonts/`): 30 font weights across 16 font families under SIL Open Font License 1.1 (see `OFL-*.txt` in `backend/assets/subtle_fonts/`).
- Export video renderer (`subtle-renderer/`): Uses Remotion (`remotion`, `@remotion/*` 4.0.534) under the Remotion License (`https://www.remotion.dev/docs/license`: free for individuals and companies up to 3 people; larger organizations require a Remotion Company License).
- Windows-native person segmentation models (`backend/assets/models/segmentation/`):
  - MediaPipe Selfie Segmentation (Apache-2.0 License)
  - MODNet (optional on-demand download, Apache-2.0 License)
- Spoken-word timing uses `faster-whisper` (`Systran/faster-whisper-small`, MIT License) and face detection uses OpenCV YuNet 2023mar (MIT License).
