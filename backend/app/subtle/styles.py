"""Loader and preset customizer for all 39 Subtle caption styles and custom recipes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any

from .. import config
from ..runtime_profile import checked_path

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,40}$')

CATEGORY_BY_STYLE: dict[str, str] = {
    'glide': 'editorial',
    'accent': 'editorial',
    'editorial': 'editorial',
    'magazine': 'editorial',
    'cinema': 'editorial',
    'serif': 'editorial',
    'shine': 'editorial',
    'quiet': 'editorial',
    'haiku': 'editorial',
    'title': 'editorial',
    'keyword': 'behind',
    'cover': 'behind',
    'outline': 'behind',
    'poster': 'behind',
    'rise': 'behind',
    'numeral': 'behind',
    'glass': 'container',
    'chip': 'container',
    'cards': 'container',
    'block': 'container',
    'sticker': 'container',
    'ticket': 'container',
    'terminal': 'container',
    'hype': 'kinetic',
    'impact': 'kinetic',
    'punch': 'kinetic',
    'bottom': 'kinetic',
    'stack': 'kinetic',
    'flip': 'kinetic',
    'highlighter': 'kinetic',
    'newsroom': 'kinetic',
    'karaoke': 'kinetic',
    'duo': 'kinetic',
    'whisper': 'kinetic',
    'split': 'kinetic',
    'downbeat': 'music',
    'billboard': 'music',
    'chalkline': 'music',
    'singalong': 'music',
}

_MANUAL_ACCENT_PATHS: dict[str, list[tuple[str, str | None]]] = {
    'stack': [('activeColor', None)],
    'bottom': [('activeColor', None)],
    'impact': [('activeColor', None)],
    'highlighter': [('activeColor', None)],
    'editorial': [('emphasis.color', None)],
    'cards': [('recipe.emphasis.color', None)],
    'glass': [('emphasis.color', None)],
    'title': [('emphasis.color', None)],
    'whisper': [('emphasis.color', None)],
    'ticket': [('emphasis.color', None)],
    'quiet': [('recipe.emphasis.color', None)],
    'punch': [('recipe.active.color', None), ('recipe.emphasis.color', None)],
    'newsroom': [('color', None)],
    'duo': [('color', None)],
    'terminal': [('color', None), ('activeColor', None), ('emphasis.fill', None)],
    'sticker': [('container.fill', None), ('emphasis.fill', 'light')],
    'block': [('container.fill', None)],
    'chip': [('emphasis.fill', 'dark')],
    'serif': [('emphasis.gradTop', 'light'), ('emphasis.gradBottom', None)],
    'singalong': [('activeColor', None)],
}


def subtle_fonts_dir() -> Path:
    directory = config.RUNTIME.validate_project(config.BACKEND / 'assets' / 'subtle_fonts', 'Subtle fonts')
    if not directory.is_dir():
        raise ValueError('Direktori font Subtle lokal tidak ditemukan.')
    return directory


def subtle_styles_dir() -> Path:
    directory = config.RUNTIME.validate_project(config.BACKEND / 'assets' / 'subtle_styles', 'Subtle styles')
    if not directory.is_dir():
        raise ValueError('Direktori preset style Subtle tidak ditemukan.')
    return directory


def deep_merge(base: Any, over: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    m = re.fullmatch(r'#?([0-9a-fA-F]{6})', (h or '').strip())
    if not m:
        return (255, 255, 255)
    n = int(m.group(1), 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return '#' + ''.join(f'{max(0, min(255, int(round(c)))):02X}' for c in rgb)


def _mix_hex(a: str, b: str, t: float) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return _rgb_to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def _set_path(obj: dict[str, Any], dotpath: str, val: Any) -> None:
    parts = dotpath.split('.')
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = val


def _get_path(obj: dict[str, Any], dotpath: str) -> Any:
    cur: Any = obj
    for part in dotpath.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _accent_paths(style: dict[str, Any]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []

    def walk(o: Any, prefix: str) -> None:
        if not isinstance(o, dict):
            return
        if isinstance(o.get('auto'), str):
            out.append((prefix, None))
            return
        for k, v in o.items():
            if k == 'variants':
                continue
            walk(v, f'{prefix}.{k}' if prefix else k)

    walk(style, '')
    for dotpath, mode in _MANUAL_ACCENT_PATHS.get(style.get('name', ''), []):
        if not any(p == dotpath for p, _ in out):
            out.append((dotpath, mode))
    return out


def default_accent(style: dict[str, Any]) -> str | None:
    paths = _accent_paths(style)
    if not paths:
        return None
    first = next((p for p, mode in paths if not mode), paths[0][0])
    val = _get_path(style, first)
    if isinstance(val, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', val.strip()):
        return val.strip().upper()
    if isinstance(val, dict) and isinstance(val.get('fallback'), str):
        return val['fallback'].strip().upper()
    return '#D7F35C'


def has_auto_palette(obj: Any) -> bool:
    if isinstance(obj, dict):
        return isinstance(obj.get('auto'), str) or any(has_auto_palette(v) for k, v in obj.items() if k != 'variants')
    if isinstance(obj, list):
        return any(has_auto_palette(v) for v in obj)
    return False


def style_needs_blur(style: dict[str, Any]) -> bool:
    return bool(style.get('backdropBlur') or ((style.get('recipe') or {}).get('plate') or {}).get('backdropBlur'))


def list_styles() -> list[dict[str, Any]]:
    directory = subtle_styles_dir()
    items = []
    for path in sorted(directory.glob('*.json')):
        checked_path(path, directory, 'Subtle style preset')
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        name = raw.get('name') or path.stem
        behind = bool((raw.get('behind') or {}).get('enabled'))
        group = raw.get('group') or 'caption'
        variants = list((raw.get('variants') or {}).keys())
        items.append({
            'name': name,
            'label': raw.get('label') or name.title(),
            'description': raw.get('description') or '',
            'group': group,
            'category': CATEGORY_BY_STYLE.get(name, 'music' if group == 'music' else 'editorial'),
            'behind_enabled': behind,
            'backdrop_blur': style_needs_blur(raw),
            'has_container': bool(raw.get('container') or (raw.get('recipe') or {}).get('plate')),
            'subject_timeline': bool(raw.get('subject')),
            'has_auto_palette': has_auto_palette(raw),
            'default_accent': default_accent(raw),
            'variants': variants,
            'default_case': raw.get('case', 'sentence'),
            'default_y': (raw.get('position') or {}).get('y', 50),
        })
    return items


def build_tweaks_override(base_style: dict[str, Any], tweaks: dict[str, Any] | None) -> dict[str, Any]:
    if not tweaks:
        return {}
    out: dict[str, Any] = {}
    accent = tweaks.get('accent')
    if isinstance(accent, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', accent.strip()):
        clean_hex = accent.strip().upper()
        for dotpath, mode in _accent_paths(base_style):
            if mode == 'light':
                _set_path(out, dotpath, _mix_hex(clean_hex, '#FFFFFF', 0.55))
            elif mode == 'dark':
                _set_path(out, dotpath, _mix_hex(clean_hex, '#000000', 0.78))
            else:
                _set_path(out, dotpath, clean_hex)
    size_pct = tweaks.get('size_pct')
    if isinstance(size_pct, (int, float)) and not isinstance(size_pct, bool) and abs(float(size_pct) - 100.0) > 0.01:
        clamped = max(50.0, min(180.0, float(size_pct)))
        out['size'] = round(float(base_style.get('size', 4.0)) * (clamped / 100.0), 2)
    y_val = tweaks.get('y')
    if isinstance(y_val, (int, float)) and not isinstance(y_val, bool):
        out['position'] = {'y': max(5.0, min(92.0, float(y_val)))}
    case_val = tweaks.get('case')
    if case_val in {'upper', 'sentence', 'lower'}:
        out['case'] = case_val
    if tweaks.get('shadow') is False:
        out['shadow'] = None
        out['legibility'] = {'mode': 'none'}
    return out


def _validate_custom_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, dict):
        raise ValueError('Format custom recipe style harus berupa objek JSON.')
    clean = copy.deepcopy(recipe)
    name = str(clean.get('name') or 'custom')
    if not NAME_RE.fullmatch(name):
        name = 'custom'
    clean['name'] = name
    fonts_dir = subtle_fonts_dir()

    def verify_fonts(node: Any) -> None:
        if isinstance(node, dict):
            if 'file' in node and 'family' in node:
                fname = str(node['file'])
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.ttf', fname):
                    raise ValueError(f'Nama berkas font tidak valid: {fname}')
                target = checked_path(fonts_dir / fname, fonts_dir, 'Subtle recipe font')
                if not target.is_file():
                    raise ValueError(f'Font tidak ditemukan di repositori lokal: {fname}')
            for v in node.values():
                verify_fonts(v)
        elif isinstance(node, list):
            for v in node:
                verify_fonts(v)

    verify_fonts(clean)
    return clean


def load_style(
    name: str,
    *,
    variant: str = '',
    tweaks: dict[str, Any] | None = None,
    custom_recipe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if custom_recipe is not None:
        base = _validate_custom_recipe(custom_recipe)
    else:
        raw_name = (name or 'glide').strip().lower()
        if ':' in raw_name and not variant:
            raw_name, _, variant = raw_name.partition(':')
        if not NAME_RE.fullmatch(raw_name):
            raise ValueError(f'Nama style tidak valid: {name}')
        styles_dir = subtle_styles_dir()
        path = checked_path(styles_dir / f'{raw_name}.json', styles_dir, 'Subtle style file')
        if not path.is_file():
            raise ValueError(f'Style "{raw_name}" tidak tersedia.')
        base = json.loads(path.read_text(encoding='utf-8-sig'))

    if variant:
        variants = base.get('variants') or {}
        if variant not in variants:
            raise ValueError(f'Style "{base.get("name")}" tidak memiliki varian "{variant}".')
        base = deep_merge(base, variants[variant])
        base['variant'] = variant

    override = build_tweaks_override(base, tweaks)
    if override:
        base = deep_merge(base, override)
    return base
