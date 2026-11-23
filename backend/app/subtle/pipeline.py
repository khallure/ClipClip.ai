"""Unified orchestrator for building verified Subtle captions.json documents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import media
from .beats import detect_beats
from .cuts import detect as detect_cuts
from .group import build_captions_document, extract_interval_words, to_srt, to_vtt
from .legibility import analyse as analyse_legibility
from .placement import apply as apply_placement
from .shots import apply as apply_shots
from .styles import list_styles, load_style, style_needs_blur

CANVAS_DIMS: dict[str, tuple[int, int]] = {
    '9:16': (1080, 1920),
    '16:9': (1920, 1080),
    '1:1': (1080, 1080),
    '4:5': (1080, 1350),
}


def compute_output_dimensions(
    source_info: dict[str, Any],
    *,
    aspect: str,
    canvas: str = '9:16',
) -> tuple[int, int]:
    if source_info.get('audio_only'):
        return CANVAS_DIMS.get(canvas, (1080, 1920))
    if aspect == 'vertical':
        return (1080, 1920)
    w = int(source_info['width'])
    h = int(source_info['height'])
    return (w + (w % 2), h + (h % 2))


def prepare_subtle_captions(
    *,
    video_path: Path | None,
    source_info: dict[str, Any],
    transcript: dict[str, Any],
    start: float,
    end: float,
    aspect: str,
    options: dict[str, Any] | None = None,
    corrections: dict[int, str] | None = None,
    width: int | None = None,
    height: int | None = None,
    offset_sec: float | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    sampler=None,
    cuts_override: list[int] | None = None,
    beats_override: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    cancelled=None,
) -> dict[str, Any]:
    opts = dict(options or {})
    tweaks = opts.get('tweaks') or {}
    style = load_style(
        opts.get('style', 'glide'),
        variant=opts.get('variant', ''),
        tweaks=tweaks,
        custom_recipe=opts.get('custom_recipe'),
    )
    notes: list[str] = []
    audio_only = bool(source_info.get('audio_only'))
    music = style.get('group') == 'music' or (style.get('page') or {}).get('snap') in ('beat', 'bar')
    out_w, out_h = (
        (int(width), int(height))
        if width and height
        else compute_output_dimensions(source_info, aspect=aspect, canvas=opts.get('canvas', '9:16'))
    )
    fps = 30.0 if audio_only else max(1.0, float(source_info.get('fps') or 30.0))
    duration_ms = max(100, int(round((end - start) * 1000.0)))
    language = transcript.get('language')
    words = extract_interval_words(
        transcript.get('segments') or [],
        start,
        end,
        corrections=corrections,
        hinglish=bool(opts.get('hinglish')),
    )

    if offset_sec is None:
        offset_sec = float(start)

    # Cuts (for talking-head footage)
    cuts: list[int] = []
    if cuts_override is not None:
        cuts = list(cuts_override)
    elif video_path is not None and not audio_only and not music and opts.get('cuts', 'auto') == 'auto':
        try:
            cuts = detect_cuts(
                video_path,
                fps=fps,
                start=offset_sec if offset_sec > 0 else None,
                end=(offset_sec + (end - start)) if offset_sec > 0 else (end - start if offset_sec == 0 else None),
                cancelled=cancelled,
            )
            if cuts:
                notes.append(f'{len(cuts)} pergantian shot (hard cut) terdeteksi; halaman disesuaikan agar tidak terpotong.')
        except media.MediaError as exc:
            notes.append(f'Deteksi hard-cut dilewati ({exc}).')

    # Beats (for music styles)
    beats: dict[str, Any] | None = None
    if beats_override is not None:
        beats = beats_override
    elif music and video_path is not None:
        try:
            detected = detect_beats(
                video_path,
                start=offset_sec if offset_sec > 0 else None,
                end=(offset_sec + (end - start)) if offset_sec > 0 else (end - start if offset_sec == 0 else None),
                cache_root=cache_dir / 'beats' if cache_dir else None,
                cancelled=cancelled,
            )
            if detected.get('bpm') and (detected.get('clarity') or 0) >= 0.25:
                beats = detected
                notes.append(f"Beat grid: {detected['bpm']} BPM, {len(detected.get('times', []))} ketukan (clarity {detected.get('clarity')}).")
            else:
                notes.append('Tempo musik kurang stabil; halaman mengikuti waktu kata.')
        except media.MediaError as exc:
            notes.append(f'Deteksi beat dilewati ({exc}).')

    cap, lyrics_stats = build_captions_document(
        words,
        width=out_w,
        height=out_h,
        fps=fps,
        duration_ms=duration_ms,
        style=style,
        language=language,
        emphasis=opts.get('emphasis', 'auto'),
        emphasis_words=opts.get('emphasis_words') or [],
        numbers=opts.get('numbers', 'auto'),
        clean_fillers=bool(opts.get('clean_fillers')),
        replacements=opts.get('replacements') or {},
        max_words=opts.get('max_words'),
        lyrics_text=opts.get('lyrics') or '',
        beats=beats,
        cuts=cuts,
        manual_picks=opts.get('manual_picks') or [],
        manual_drops=opts.get('manual_drops') or [],
        clear_auto_emphasis=bool(opts.get('clear_auto_emphasis')),
        page_edits=opts.get('page_edits') or {},
    )
    if lyrics_stats:
        notes.append(
            f"Penyelarasan lirik: {lyrics_stats.get('matched', 0)}/{lyrics_stats.get('lyricWords', 0)} kata cocok di audio."
        )

    # Smart placement & per-shot behind checks when subject vision is available (Stage 2 fills subtle_vision)
    vision_sampler = sampler
    if vision_sampler is None and video_path is not None and not audio_only:
        try:
            from .. import subtle_vision
            if subtle_vision.capabilities().get('available'):
                vision_sampler = lambda times: subtle_vision.sample_subject(  # noqa: E731
                    video_path,
                    times,
                    offset_sec=offset_sec,
                    crop_box=crop_box,
                    cancelled=cancelled,
                )
        except ImportError:
            vision_sampler = None

    placement_report = None
    if not audio_only and not music and opts.get('placement', 'auto') == 'auto' and tweaks.get('y') is None and vision_sampler is not None and cap['pages']:
        p_notes, placement_report = apply_placement(
            video_path or Path('.'),
            cap,
            style,
            cuts,
            sampler=vision_sampler,
            cancelled=cancelled,
        )
        notes.extend(p_notes)
        if placement_report.get('moved'):
            notes.append(f"Auto-placement: memindahkan {placement_report['moved']} dari {placement_report['pages']} halaman menjauhi wajah/tangan.")

    wants_behind = bool((style.get('behind') or {}).get('enabled'))
    needs_subject = bool(style.get('subject'))
    behind_mode = opts.get('behind', 'auto')
    use_behind = False
    if ((wants_behind and behind_mode != 'off') or needs_subject) and not music and not audio_only and vision_sampler is not None and cap['pages']:
        s_notes, shot_list = apply_shots(
            video_path or Path('.'),
            cap,
            style,
            sampler=vision_sampler,
            force_behind=(behind_mode == 'on'),
            cancelled=cancelled,
        )
        notes.extend(s_notes)
        if wants_behind and behind_mode != 'off':
            use_behind = (behind_mode == 'on') or any(s.get('behind') for s in shot_list)
            behind_shots = [s for s in shot_list if s.get('behind') and s.get('y')]
            if behind_shots:
                style.setdefault('behind', {})
                style['behind']['bigY'] = round(sum(s['y'] for s in behind_shots) / len(behind_shots), 1)
                style['behind']['frontY'] = round(min(70.0, max(52.0, style['behind']['bigY'] + 32.0)), 1)
    elif wants_behind and behind_mode == 'on' and not audio_only and not music:
        use_behind = True

    # Auto legibility (WCAG contrast/busyness) & Auto palette
    leg_mode = 'off' if tweaks.get('shadow') is False else opts.get('legibility', 'auto')
    pal_mode = 'off' if tweaks.get('accent') else opts.get('palette', 'auto')
    if (video_path is not None and not audio_only and cap['pages']
            and (leg_mode != 'off' or pal_mode != 'off')):
        analyse_legibility(
            cap,
            style,
            video_path,
            palette_mode=pal_mode,
            legibility_mode=leg_mode,
            offset_sec=offset_sec,
            crop_box=crop_box,
        )
        lsum = cap.get('legibilitySummary') or {}
        if lsum.get('pages') and lsum.get('boosted'):
            notes.append(
                f"Auto-legibility ({lsum['mode']}): membantu {lsum['boosted']}/{lsum['pages']} halaman (kontras terendah {lsum.get('minContrast')}:1)."
            )
        if cap.get('palette'):
            notes.append(f"Auto-palette dari video: aksen {cap['palette'].get('accent')} ({cap['palette'].get('contrast')}:1).")

    return {
        'captions': cap,
        'resolved_style': style,
        'srt': to_srt(cap['pages']),
        'vtt': to_vtt(cap['pages']),
        'notes': notes,
        'palette': cap.get('palette'),
        'legibility_summary': cap.get('legibilitySummary'),
        'placement': placement_report,
        'beats': cap.get('beats'),
        'cuts': cuts,
        'behind_active': bool(use_behind),
        'backdrop_blur': style_needs_blur(style),
        'lyrics_stats': lyrics_stats,
        'style_list_count': len(list_styles()),
    }
