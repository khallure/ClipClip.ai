"""Subtle Remotion renderer bridge and FFmpeg alpha/blur/behind compositor."""
from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

from .. import config, media
from ..process_runtime import external_popen
from ..runtime_profile import IsolationError, checked_path
from . import pipeline as subtle_pipeline
from . import styles as subtle_styles
from . import verify as subtle_verify


def _renderer_boundary() -> Path:
    return config.RUNTIME.package if config.RUNTIME.packaged else config.PROJECT


def renderer_dir() -> Path:
    root = _renderer_boundary()
    return checked_path(root / 'subtle-renderer', root, 'Subtle renderer')


def find_browser_executable(rdir: Path | None = None) -> Path | None:
    target_dir = rdir or renderer_dir()
    configured = os.environ.get('REMOTION_BROWSER_EXECUTABLE', '').strip()
    if configured:
        try:
            candidate = checked_path(configured, _renderer_boundary(), 'Subtle browser executable')
            if candidate.is_file():
                return candidate
        except IsolationError:
            pass
    cache_root = checked_path(
        target_dir / 'node_modules' / '.remotion' / 'chrome-headless-shell',
        target_dir,
        'Subtle browser cache',
    )
    if not cache_root.is_dir():
        return None
    binary_name = 'chrome-headless-shell.exe' if os.name == 'nt' else 'chrome-headless-shell'
    stack = [cache_root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_symlink():
                continue
            item = checked_path(entry, cache_root, 'Subtle browser binary')
            if item.is_dir():
                stack.append(item)
            elif item.is_file() and item.name.lower() == binary_name:
                return item
    return None


def renderer_capabilities() -> dict[str, Any]:
    node_exe = config.binary('node')
    rdir = renderer_dir()
    script = checked_path(rdir / 'render.mjs', rdir, 'Subtle render script')
    bundle_index = checked_path(rdir / 'bundle' / 'index.html', rdir, 'Subtle bundle')
    src_entry = checked_path(rdir / 'src' / 'index.ts', rdir, 'Subtle source entry')
    browser_path = find_browser_executable(rdir)

    node_ok = bool(node_exe and Path(node_exe).is_file())
    script_ok = script.is_file()
    bundle_ok = bundle_index.is_file()
    src_ok = src_entry.is_file()
    browser_ok = bool(browser_path and browser_path.is_file())

    reason = None
    if not node_ok:
        reason = 'Node.js lokal belum tersedia untuk renderer Subtle.'
    elif not script_ok:
        reason = 'Script subtle-renderer/render.mjs tidak ditemukan.'
    elif not (bundle_ok or src_ok):
        reason = 'Bundle Remotion Subtle maupun sumber renderer tidak ditemukan.'
    elif not browser_ok:
        reason = 'Chrome Headless Shell untuk Subtle belum terpasang. Jalankan scripts\\setup.ps1.'
    elif not bundle_ok:
        # Bundled on demand works in dev, while packaged installs are read-only and ship bundle/.
        if config.RUNTIME.packaged:
            reason = 'Bundle Remotion Subtle tidak ditemukan pada paket aplikasi.'

    available = node_ok and script_ok and browser_ok and (bundle_ok or (src_ok and not config.RUNTIME.packaged))
    return {
        'available': bool(available),
        'node': node_ok,
        'script': script_ok,
        'bundle': bundle_ok,
        'browser': browser_ok,
        'reason': reason,
    }


def _remotion_env() -> dict[str, str]:
    env = config.child_environment()
    profile_cache = checked_path(config.CACHE_DIR / 'remotion', config.RUNTIME.root, 'Remotion cache profile')
    profile_cache.mkdir(parents=True, exist_ok=True)
    env.setdefault('LOCALAPPDATA', str(profile_cache))
    env.setdefault('APPDATA', str(profile_cache))
    env.setdefault('USERPROFILE', str(config.TEMP_DIR))
    env.setdefault('TEMP', str(config.TEMP_DIR))
    env.setdefault('TMP', str(config.TEMP_DIR))
    return env


def render_layer(
    captions: dict[str, Any],
    style: dict[str, Any],
    out_path: Path,
    *,
    layer: str = 'all',
    still_frame: int | None = None,
    concurrency: int | None = None,
    cancelled=None,
    work_dir: Path | None = None,
) -> Path:
    if layer not in {'all', 'behind', 'front', 'mask'}:
        raise media.MediaError(f'Layer render Subtle tidak valid: {layer}')
    caps = renderer_capabilities()
    if not caps['available']:
        raise media.MediaError(caps['reason'] or 'Renderer Subtle belum siap.')

    node_exe = config.binary('node')
    assert node_exe is not None
    rdir = renderer_dir()
    script = checked_path(rdir / 'render.mjs', rdir, 'Subtle render script')
    bundle_dir = checked_path(rdir / 'bundle', rdir, 'Subtle bundle directory')
    browser_path = find_browser_executable(rdir)
    fonts_dir = subtle_styles.subtle_fonts_dir()

    scratch = work_dir or out_path.parent
    scratch.mkdir(parents=True, exist_ok=True)
    cap_file = scratch / f'_render_captions_{layer}.json'
    style_file = scratch / f'_render_style_{layer}.json'
    cap_file.write_text(json.dumps(captions, ensure_ascii=False), encoding='utf-8')
    style_file.write_text(json.dumps(style, ensure_ascii=False), encoding='utf-8')

    cmd = [
        node_exe,
        str(script),
        '--captions', str(cap_file),
        '--style', str(style_file),
        '--fonts-dir', str(fonts_dir),
        '--layer', layer,
        '--out', str(out_path),
    ]
    if (bundle_dir / 'index.html').is_file():
        cmd.extend(['--bundle-dir', str(bundle_dir)])
    if browser_path is not None:
        cmd.extend(['--browser-executable', str(browser_path)])
    if still_frame is not None:
        cmd.extend(['--still', str(int(still_frame)), '--png'])
    if concurrency is not None and concurrency > 0:
        cmd.extend(['--concurrency', str(int(concurrency))])

    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    proc = external_popen(
        cmd,
        cwd=str(rdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_remotion_env(),
        creationflags=creationflags,
    )
    try:
        stdout_b = b''
        stderr_b = b''
        while True:
            try:
                stdout_b, stderr_b = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancelled and cancelled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise media.MediaCancelled('Render subtitle Subtle dibatalkan.') from None
        if cancelled and cancelled():
            raise media.MediaCancelled('Render subtitle Subtle dibatalkan.')
        if proc.returncode != 0:
            err = (stderr_b or stdout_b or b'').decode('utf-8', errors='replace').strip()
            snippet = err[-500:] if len(err) > 500 else err
            raise media.MediaError(f'Renderer Subtle gagal (kode {proc.returncode}): {snippet or "tanpa pesan"}')
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise media.MediaError('Renderer Subtle selesai tetapi tidak menghasilkan berkas keluaran.')
        return out_path
    finally:
        cap_file.unlink(missing_ok=True)
        style_file.unlink(missing_ok=True)


def _prepare_base_clip(
    source: Path,
    base_mp4: Path,
    directory: Path,
    settings: dict[str, Any],
    source_info: dict[str, Any],
    *,
    out_w: int,
    out_h: int,
    bg_hex: str,
    cancelled=None,
) -> None:
    start = float(settings['start'])
    end = float(settings['end'])
    if source_info.get('audio_only'):
        media.make_audio_canvas(
            source,
            base_mp4,
            start=start,
            end=end,
            width=out_w,
            height=out_h,
            bg_hex=bg_hex or '#0E0E10',
            cancelled=cancelled,
        )
        return

    filters: list[str] = []
    if settings['aspect'] == 'vertical' and settings.get('_reframe_plan'):
        from ..reframe_render import render_filter
        filters.extend(render_filter(settings['_reframe_plan'], start, end, directory))
    elif settings['aspect'] == 'vertical':
        crop_width = max(2, min(source_info['width'], int(source_info['height'] * 9 / 16)) // 2 * 2)
        crop_height = max(2, min(source_info['height'], int(source_info['width'] * 16 / 9)) // 2 * 2)
        x = int((source_info['width'] - crop_width) * settings.get('crop_x', 0.5)) // 2 * 2
        y = (source_info['height'] - crop_height) // 4 * 2
        filters.extend([f'crop={crop_width}:{crop_height}:{x}:{y}', 'scale=1080:1920:flags=lanczos', 'setsar=1'])
    else:
        filters.extend(['pad=ceil(iw/2)*2:ceil(ih/2)*2', 'setsar=1'])

    cmd = [
        media.tool('ffmpeg'),
        '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-ss', f'{start:.6f}',
        *media.input_args(source),
        '-t', f'{end - start:.6f}',
        '-map', '0:v:0',
        '-map', '0:a:0?',
        '-vf', ','.join(filters),
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '16',
        '-pix_fmt', 'yuv420p',
        '-threads', '4',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        str(base_mp4),
    ]
    media.run(cmd, cwd=directory, cancelled=cancelled)


def export_subtle_clip(
    source: Path,
    directory: Path,
    settings: dict[str, Any],
    transcript: dict[str, Any] | None,
    source_info: dict[str, Any],
    cancelled=None,
    *,
    corrections: dict[Any, str] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    segments = (transcript or {}).get('segments') or []
    if not any(seg.get('words') for seg in segments):
        raise media.MediaError('Subtitle animasi Subtle memerlukan waktu per kata. Siapkan waktu per kata terlebih dahulu.')

    caps = renderer_capabilities()
    if not caps['available']:
        raise media.MediaError(caps['reason'] or 'Renderer Subtle belum siap.')

    start = float(settings['start'])
    end = float(settings['end'])
    if end <= start:
        raise media.MediaError('Rentang waktu klip tidak valid untuk ekspor Subtle.')

    subtle_opts = dict(settings.get('subtle') or {})
    out_w, out_h = subtle_pipeline.compute_output_dimensions(
        source_info,
        aspect=settings['aspect'],
        canvas=subtle_opts.get('canvas', '9:16'),
    )
    norm_corrections: dict[int, str] | None = None
    if corrections:
        norm_corrections = {int(k): v for k, v in corrections.items()}

    target = directory / 'clip.mp4'
    partial = directory / 'clip.partial.mp4'
    base_mp4 = directory / '_subtle_base.mp4'
    overlay_mov = directory / '_subtle_overlay.mov'
    mask_mp4 = directory / '_subtle_mask.mp4'
    matte_mp4 = directory / '_subtle_matte.mp4'
    behind_mov = directory / '_subtle_behind.mov'
    front_mov = directory / '_subtle_front.mov'
    preview_jpg = directory / 'preview.jpg'

    try:
        _prepare_base_clip(
            source,
            base_mp4,
            directory,
            settings,
            source_info,
            out_w=out_w,
            out_h=out_h,
            bg_hex=str(subtle_opts.get('canvas_bg') or '#0E0E10'),
            cancelled=cancelled,
        )
        base_info = media.probe(base_mp4)
        base_source_info = {**base_info, 'audio_only': bool(source_info.get('audio_only'))}

        prep = subtle_pipeline.prepare_subtle_captions(
            video_path=base_mp4,
            source_info=base_source_info,
            transcript=transcript or {'segments': []},
            start=start,
            end=end,
            aspect=settings['aspect'],
            options=subtle_opts,
            corrections=norm_corrections,
            width=base_info['width'],
            height=base_info['height'],
            offset_sec=0.0,
            crop_box=None,
            cache_dir=cache_dir,
            cancelled=cancelled,
        )
        cap = prep['captions']
        resolved_style = prep['resolved_style']
        notes = list(prep.get('notes') or [])

        (directory / 'captions.json').write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding='utf-8')
        (directory / 'captions.srt').write_text(prep['srt'], encoding='utf-8')
        (directory / 'captions.vtt').write_text(prep['vtt'], encoding='utf-8')

        from .. import subtle_vision
        vision_ready = bool(subtle_vision.capabilities().get('available'))
        use_behind = bool(
            prep.get('behind_active')
            and cap.get('big')
            and not source_info.get('audio_only')
            and vision_ready
        )

        qa_overlay: Path
        qa_behind: Path | None = None
        qa_matte: Path | None = None
        ffmpeg = media.tool('ffmpeg')

        if use_behind:
            subtle_vision.generate_matte(
                base_mp4,
                matte_mp4,
                quality=str(subtle_opts.get('matte_quality') or 'balanced'),
                cancelled=cancelled,
            )
            render_layer(cap, resolved_style, behind_mov, layer='behind', cancelled=cancelled, work_dir=directory)
            cap, dropped = subtle_verify.prune_hidden_bigs(cap, behind_mov, matte_mp4)
            if dropped:
                notes.append(f'{len(dropped)} kata raksasa dilewati karena tertutup subjek.')
                (directory / 'captions.json').write_text(json.dumps(cap, ensure_ascii=False, indent=2), encoding='utf-8')
                render_layer(cap, resolved_style, behind_mov, layer='behind', cancelled=cancelled, work_dir=directory)
            render_layer(cap, resolved_style, front_mov, layer='front', cancelled=cancelled, work_dir=directory)

            fc = (
                '[0:v][1:v]overlay=eof_action=pass[bg];'
                '[0:v][2:v]alphamerge[fg];'
                '[bg][fg]overlay=eof_action=pass[mid];'
                '[mid][3:v]overlay=eof_action=pass[v]'
            )
            comp_cmd = [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(base_mp4),
                '-i', str(behind_mov),
                '-i', str(matte_mp4),
                '-i', str(front_mov),
                '-filter_complex', fc,
                '-map', '[v]',
                '-map', '0:a:0?',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-threads', '4',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', '-map_metadata', '-1',
                str(partial),
            ]
            qa_overlay = front_mov
            qa_behind = behind_mov
            qa_matte = matte_mp4
        elif prep.get('backdrop_blur'):
            render_layer(cap, resolved_style, overlay_mov, layer='all', cancelled=cancelled, work_dir=directory)
            render_layer(cap, resolved_style, mask_mp4, layer='mask', cancelled=cancelled, work_dir=directory)
            fc = (
                '[0:v]split[orig][blursrc];'
                '[blursrc]gblur=sigma=24[blurred];'
                '[blurred][2:v]alphamerge[glass];'
                '[orig][glass]overlay=eof_action=pass[bg];'
                '[bg][1:v]overlay=eof_action=pass[v]'
            )
            comp_cmd = [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(base_mp4),
                '-i', str(overlay_mov),
                '-i', str(mask_mp4),
                '-filter_complex', fc,
                '-map', '[v]',
                '-map', '0:a:0?',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-threads', '4',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', '-map_metadata', '-1',
                str(partial),
            ]
            qa_overlay = overlay_mov
        else:
            render_layer(cap, resolved_style, overlay_mov, layer='all', cancelled=cancelled, work_dir=directory)
            fc = '[0:v][1:v]overlay=eof_action=pass[v]'
            comp_cmd = [
                ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(base_mp4),
                '-i', str(overlay_mov),
                '-filter_complex', fc,
                '-map', '[v]',
                '-map', '0:a:0?',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-threads', '4',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', '-map_metadata', '-1',
                str(partial),
            ]
            qa_overlay = overlay_mov

        media.run(comp_cmd, cwd=directory, cancelled=cancelled)
        info = media.probe(partial)

        preset = settings.get('duration_preset')
        if preset in {'20', '30', '45', '60'} and info['duration'] > float(preset) + 1 / max(1, info['fps']) + .000001:
            raise media.MediaError(
                f'Ekspor melewati batas {preset} detik dan tidak ditandai siap. Periksa batas kalimat atau pendekkan akhir klip.'
            )
        expected = (base_info['width'], base_info['height'])
        if (
            (info['width'], info['height']) != expected
            or info['video_codec'] != 'h264'
            or (base_info['has_audio'] and not info['has_audio'])
            or abs(info['duration'] - (end - start)) > .45
        ):
            raise media.MediaError('Verifikasi ekspor Subtle gagal. File tidak ditandai siap; coba lagi atau periksa sumber.')
        if cancelled and cancelled():
            raise media.MediaCancelled('Ekspor dibatalkan.')

        qa = subtle_verify.verify_export(
            video_path=partial,
            source_path=base_mp4,
            cap=cap,
            style=resolved_style,
            overlay_path=qa_overlay,
            preview_out=preview_jpg,
            behind_path=qa_behind,
            matte_path=qa_matte,
        )
        partial.replace(target)
        info['subtle_qa'] = qa
        info['subtle_notes'] = notes
        return info
    finally:
        for tmp in (partial, base_mp4, overlay_mov, mask_mp4, matte_mp4, behind_mov, front_mov):
            tmp.unlink(missing_ok=True)
