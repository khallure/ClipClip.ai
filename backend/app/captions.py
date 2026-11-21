"""Subtitle Poppins sederhana: satu kata aktif, tanpa kotak atau animasi.

Dokumen kata dan baris yang sama dipakai oleh preview browser dan ASS/libass.
Waktu kata selalu berasal dari transkrip; tidak pernah diinterpolasi dari teks.
"""
from __future__ import annotations

import bisect
import html
import math
import re
import struct
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config


class CaptionStyle(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    font_weight: Literal[600, 700] = 600
    font_size_pct: float = Field(default=4.5, ge=2, le=9, strict=True)
    position_y_pct: float = Field(default=70, ge=20, le=85, strict=True)
    active_color: str = Field(default='#D7F35C', pattern=r'^#[0-9A-Fa-f]{6}$')
    stroke_px: float = Field(default=1.0, ge=0, le=3, strict=True)
    shadow_px: float = Field(default=.5, ge=0, le=3, strict=True)
    max_words: int = Field(default=4, ge=2, le=5, strict=True)

    @field_validator('active_color')
    @classmethod
    def uppercase_color(cls, value):
        return value.upper()


def font_directory() -> Path:
    directory = config.RUNTIME.validate_project(config.BACKEND / 'assets' / 'fonts', 'Caption fonts')
    for name in ('Poppins-SemiBold.ttf', 'Poppins-Bold.ttf', 'OFL.txt'):
        config.RUNTIME.validate_project(directory / name, 'Caption font')
        if not (directory / name).is_file():
            raise ValueError('Font Poppins lokal tidak tersedia. Pulihkan backend/assets/fonts dari proyek sebelum mengekspor subtitle.')
    return directory


class _FontMetrics:
    """Baca metrik TTF asli tanpa dependensi/font jaringan atau font pengganti."""
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        count = self.u16(4)
        tables = {}
        for i in range(count):
            tag, _, offset, length = struct.unpack_from('>4sIII', self.data, 12 + 16*i)
            tables[tag.decode('ascii')] = (offset, length)
        self.tables = tables
        self.units_per_em = self.u16(tables['head'][0] + 18)
        os2 = tables['OS/2'][0]
        self.weight = self.u16(os2 + 4)
        self.win_ascent, self.win_descent = struct.unpack_from('>HH', self.data, os2 + 74)
        self.ass_scale = (self.win_ascent + self.win_descent) / self.units_per_em
        hhea = tables['hhea'][0]
        self.num_metrics = self.u16(hhea + 34)
        hmtx = tables['hmtx'][0]
        self.advances = [self.u16(hmtx + i*4) for i in range(self.num_metrics)]
        cmap = tables['cmap'][0]
        choices = []
        for i in range(self.u16(cmap + 2)):
            platform, encoding, offset = struct.unpack_from('>HHI', self.data, cmap + 4 + i*8)
            absolute = cmap + offset
            fmt = self.u16(absolute)
            if platform in (0, 3) and fmt in (4, 12):
                choices.append((fmt, absolute))
        if not choices:
            raise ValueError('Peta karakter Poppins tidak didukung.')
        self.cmap_format, self.cmap = max(choices)
        if self.cmap_format == 12:
            group_count = struct.unpack_from('>I', self.data, self.cmap + 12)[0]
            self.groups = [struct.unpack_from('>III', self.data, self.cmap + 16 + 12*i) for i in range(group_count)]
            self.group_starts = [g[0] for g in self.groups]
        else:
            count = self.u16(self.cmap + 6) // 2
            self.seg_count = count
            self.ends = [self.u16(self.cmap + 14 + i*2) for i in range(count)]
            self.starts_at = self.cmap + 16 + count*2
            self.deltas_at = self.starts_at + count*2
            self.offsets_at = self.deltas_at + count*2

    def u16(self, offset):
        return struct.unpack_from('>H', self.data, offset)[0]

    def glyph(self, char: str) -> int:
        code = ord(char)
        if self.cmap_format == 12:
            i = bisect.bisect_right(self.group_starts, code) - 1
            if i >= 0:
                first, last, glyph = self.groups[i]
                if code <= last:
                    return glyph + code - first
            return 0
        if code > 0xFFFF:
            return 0
        i = bisect.bisect_left(self.ends, code)
        if i >= self.seg_count or code < self.u16(self.starts_at + i*2):
            return 0
        delta = self.u16(self.deltas_at + i*2)
        offset_address = self.offsets_at + i*2
        offset = self.u16(offset_address)
        if not offset:
            return (code + delta) & 0xFFFF
        glyph = self.u16(offset_address + offset + 2*(code-self.u16(self.starts_at + i*2)))
        return ((glyph + delta) & 0xFFFF) if glyph else 0

    def width(self, text: str, font_size: float) -> float:
        return sum(self.advances[min(self.glyph(char), self.num_metrics-1)] for char in text) / self.units_per_em * font_size


@lru_cache(maxsize=2)
def _metrics(weight: int) -> _FontMetrics:
    name = 'Poppins-SemiBold.ttf' if weight == 600 else 'Poppins-Bold.ttf'
    return _FontMetrics(font_directory() / name)


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} harus berupa angka terbatas.')
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} harus berupa angka terbatas.') from None
    if not math.isfinite(value):
        raise ValueError(f'{name} harus berupa angka terbatas.')
    return value


def _display_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Setiap kata subtitle harus memiliki teks.')
    # Untrusted text must never become an ASS override block or line command.
    text = ' '.join(text.split())
    text = ''.join(char for char in text if not unicodedata.category(char).startswith('C'))
    return ' '.join(text.strip().split()).replace('\\', '/').replace('{', '[').replace('}', ']')


def _corrections(data):
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError('Koreksi subtitle harus berupa peta ID kata ke teks.')
    clean = {}
    for key, value in data.items():
        if isinstance(key, bool) or not re.fullmatch(r'0|[1-9][0-9]*', str(key)):
            raise ValueError('ID koreksi kata tidak valid.')
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or len(value.strip().split()) != 1:
            raise ValueError('Koreksi harus berupa satu kata, 1–80 karakter, tanpa mengubah waktunya.')
        clean[int(key)] = _display_text(value)
    return clean


def _separator(previous: str, current: str) -> str:
    if not previous or re.match(r'^[,.;:!?%\)\]\}”’]', current) or re.search(r'[\(\[\{“‘]$', previous):
        return ''
    return ' '


def _join(words):
    out, previous = '', ''
    for word in words:
        text = word['text']
        out += _separator(previous, text) + text
        previous = text
    return out


_JOINERS = {'and', 'or', 'the', 'a', 'an', 'of', 'to', 'for', 'in', 'on', 'with', 'dan', 'atau', 'yang', 'di', 'ke', 'dari', 'untuk', 'dengan'}
_UNITS = {'%', 'percent', 'persen', 'juta', 'ribu', 'miliar', 'million', 'billion', 'thousand', 'seconds', 'detik', 'menit', 'hours', 'jam', 'tahun', 'years', 'kg', 'km'}


def _phrase_groups(words, max_words):
    result, i = [], 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and j-i+1 < max_words:
            if re.search(r'[.!?;]["\'”’)]?$', words[j]['text']) or words[j+1]['start'] - words[j]['end'] >= .45:
                break
            j += 1
        if j-i+1 == max_words and j+1 < len(words):
            last = words[j]['text'].strip('.,;:!?').casefold()
            following = words[j+1]['text'].strip('.,;:!?').casefold()
            if (last in _JOINERS or (re.search(r'\d', last) and following in _UNITS)) and j-i+1 > 2:
                j -= 1
        result.append(words[i:j+1])
        i = j+1
    return result


def _lines(words, metrics, font_size, available):
    if metrics.width(_join(words), font_size) <= available:
        return [[word['id'] for word in words]]
    candidates = []
    for split in range(1, len(words)):
        first, second = words[:split], words[split:]
        wa, wb = metrics.width(_join(first), font_size), metrics.width(_join(second), font_size)
        if max(wa, wb) <= available:
            candidates.append((abs(wa-wb), split))
    if candidates:
        split = min(candidates)[1]
        return [[word['id'] for word in words[:split]], [word['id'] for word in words[split:]]]
    return None


def build_caption_document(segments: list[dict], start: float, end: float, width: int, height: int,
                           style: dict | CaptionStyle | None = None, corrections: dict | None = None) -> dict:
    start, end = _finite(start, 'Waktu mulai'), _finite(end, 'Waktu akhir')
    if start < 0 or end <= start:
        raise ValueError('Rentang subtitle harus positif dengan akhir setelah mulai.')
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or not isinstance(height, int) or not 2 <= width <= 16384 or not 2 <= height <= 16384:
        raise ValueError('Dimensi subtitle tidak valid.')
    if not isinstance(segments, list):
        raise ValueError('Transkrip subtitle harus berupa daftar segmen.')
    settings = CaptionStyle.model_validate(style or {}).model_dump()
    edited = _corrections(corrections)
    selected, global_id, previous_start = [], 0, -1.0
    warnings, missing_segments = [], 0
    has_safe_substitutions = False
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('words', []), list):
            raise ValueError('Data waktu kata subtitle tidak valid.')
        raw_words = segment.get('words') or []
        if not raw_words and _finite(segment.get('end', 0), 'Akhir segmen') > start and _finite(segment.get('start', 0), 'Mulai segmen') < end:
            missing_segments += 1
        for word in raw_words:
            if not isinstance(word, dict):
                raise ValueError('Data kata subtitle tidak valid.')
            a, b = _finite(word.get('start'), 'Mulai kata'), _finite(word.get('end'), 'Akhir kata')
            if a < 0 or b <= a or a < previous_start:
                raise ValueError('Waktu kata subtitle harus terurut dan berdurasi positif.')
            previous_start = a
            original = word.get('word', word.get('text'))
            text = edited.get(global_id, _display_text(original))
            has_safe_substitutions |= text != str(original).strip() and global_id not in edited
            if b > start and a < end:
                selected.append({'id': global_id, 'start': round(max(0.0, a-start), 6),
                                 'end': round(min(end-start, b-start), 6), 'text': text})
            global_id += 1
    if any(word_id >= global_id for word_id in edited):
        raise ValueError('Koreksi menunjuk ID kata yang tidak tersedia dalam transkrip ini.')
    if missing_segments:
        warnings.append('Sebagian atau seluruh transkrip belum memiliki waktu kata asli. Jalankan penyelarasan kata; waktu tidak dibuat dari teks.')
    if not selected:
        warnings.append('Tidak ada kata bertimestamp pada interval ini. Subtitle highlight tidak dibuat; audio dan video tetap dapat diekspor.')
    if has_safe_substitutions:
        warnings.append('Karakter kontrol/format pada teks subtitle ditampilkan sebagai teks aman.')
    metrics = _metrics(settings['font_weight'])
    font_size = min(width, height) * settings['font_size_pct'] / 100
    available = width * .82  # 9% safe margin each side, including stroke/shaping tolerance.
    if selected:
        longest = max(metrics.width(word['text'], 1) for word in selected)
        if longest and longest * font_size > available:
            fit_size = available / longest
            if fit_size < min(width, height) * .02:
                raise ValueError('Satu kata terlalu panjang untuk area subtitle. Koreksi teks atau gunakan ukuran/rasio yang lebih lebar.')
            settings['font_size_pct'] = round(fit_size / min(width, height) * 100, 6)
            font_size = min(width, height) * settings['font_size_pct'] / 100
            warnings.append('Ukuran subtitle diperkecil secara proporsional agar kata panjang tetap berada di area aman.')
    groups = []
    for phrase in _phrase_groups(selected, settings['max_words']):
        pending = [phrase]
        while pending:
            group_words = pending.pop(0)
            lines = _lines(group_words, metrics, font_size, available)
            if lines is None and len(group_words) > 1:
                middle = max(1, len(group_words)//2)
                pending = [group_words[:middle], group_words[middle:]] + pending
                continue
            groups.append({'id': len(groups), 'word_ids': [word['id'] for word in group_words],
                           'lines': lines or [[group_words[0]['id']]], 'start': group_words[0]['start'],
                           'end': group_words[-1]['end']})
    for i, group in enumerate(groups):
        following = groups[i+1]['start'] if i+1 < len(groups) else end-start
        group['end'] = round(min(end-start, following, group['end']+.10), 6)
    if any(metrics.glyph(char) == 0 for word in selected for char in word['text'] if not char.isspace()):
        warnings.append('Poppins tidak memuat sebagian karakter sumber. Teks dipertahankan, tetapi renderer dapat memakai font pengganti untuk karakter tersebut.')
    render_info = {'font_family': 'Poppins', 'ass_font_family': 'Poppins SemiBold' if settings['font_weight'] == 600 else 'Poppins',
                   'font_size_px': round(font_size, 6), 'ass_font_size_px': round(font_size * metrics.ass_scale, 6),
                   'line_height': 1.15, 'ascent_override_pct': metrics.win_ascent/metrics.units_per_em*100,
                   'descent_override_pct': metrics.win_descent/metrics.units_per_em*100, 'line_gap_override_pct': 0,
                   'font_kerning': False}
    return {'version': '1.0', 'start': start, 'end': end, 'duration': round(end-start, 6), 'width': width, 'height': height,
            'style': settings, 'words': selected, 'groups': groups, 'warnings': list(dict.fromkeys(warnings)), 'render_info': render_info}


def _stamp(seconds, *, ass=False, vtt=False):
    factor = 100 if ass else 1000
    ticks = max(0, round(float(seconds) * factor))
    h, remainder = divmod(ticks, 3600*factor)
    m, remainder = divmod(remainder, 60*factor)
    s, fraction = divmod(remainder, factor)
    return f'{h}:{m:02}:{s:02}.{fraction:02}' if ass else f'{h:02}:{m:02}:{s:02}{"." if vtt else ","}{fraction:03}'


def _color(rgb):
    value = rgb.lstrip('#')
    return value[4:6] + value[2:4] + value[:2]


def _document_words(document):
    return {word['id']: word for word in document['words']}


def _plain_lines(group, by_id):
    return [_join([by_id[word_id] for word_id in line]) for line in group['lines']]


def to_srt(document: dict) -> str:
    by_id = _document_words(document)
    cues = [f'{i+1}\n{_stamp(group["start"])} --> {_stamp(group["end"])}\n' + '\n'.join(html.escape(line, quote=False) for line in _plain_lines(group, by_id)) + '\n'
            for i, group in enumerate(document['groups'])]
    return '\n'.join(cues)


def to_vtt(document: dict) -> str:
    by_id = _document_words(document)
    cues = [f'{i+1}\n{_stamp(group["start"],vtt=True)} --> {_stamp(group["end"],vtt=True)}\n' + '\n'.join(html.escape(line, quote=False) for line in _plain_lines(group, by_id)) + '\n'
            for i, group in enumerate(document['groups'])]
    return 'WEBVTT\n\n' + '\n'.join(cues)


def to_ass(document: dict) -> str:
    """Setiap interval menampilkan satu kata aktif; jeda tampil putih seluruhnya."""
    settings = CaptionStyle.model_validate(document['style']).model_dump()
    width, height = document['width'], document['height']
    metrics = _metrics(settings['font_weight'])
    css_size = min(width, height) * settings['font_size_pct']/100
    ass_size = css_size * metrics.ass_scale
    family = 'Poppins SemiBold' if settings['font_weight'] == 600 else 'Poppins'
    header = f'''[Script Info]
; PODCUT AI Poppins Highlight - original dialogue and word timings retained.
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
Kerning: no

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: PoppinsHighlight,{family},{ass_size:.6f},&H00FFFFFF,&H00FFFFFF,&H60000000,&H90000000,{-1 if settings['font_weight'] == 700 else 0},0,0,0,100,100,0,0,1,{settings['stroke_px']:.3f},{settings['shadow_px']:.3f},5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    by_id = _document_words(document)
    events = []
    for group in document['groups']:
        words = [by_id[word_id] for word_id in group['word_ids']]
        boundaries = sorted({round(group['start'] * 100), round(group['end'] * 100),
                             *(round(word[key] * 100) for word in words for key in ('start', 'end'))})
        boundaries = [t for t in boundaries if round(group['start']*100) <= t <= round(group['end']*100)]
        for first, last in zip(boundaries, boundaries[1:]):
            if last <= first:
                continue
            midpoint = (first + last)/200
            active = [word for word in words if word['start'] <= midpoint < word['end']]
            active_id = max(active, key=lambda word: (word['start'], word['id']))['id'] if active else None
            for line_index, line_ids in enumerate(group['lines']):
                y = height * settings['position_y_pct']/100 + (line_index-(len(group['lines'])-1)/2)*css_size*1.15
                prefix = f'{{\\an5\\pos({width/2:.3f},{y:.3f})\\b{settings["font_weight"]}}}'
                text, previous = '', ''
                for word_id in line_ids:
                    word = by_id[word_id]
                    text += _separator(previous, word['text'])
                    color = _color(settings['active_color']) if word_id == active_id else 'FFFFFF'
                    text += f'{{\\c&H{color}&}}' + word['text']
                    previous = word['text']
                events.append(f'Dialogue: 0,{_stamp(first/100,ass=True)},{_stamp(last/100,ass=True)},PoppinsHighlight,,0,0,0,,{prefix}{text}')
    return header + '\n'.join(events) + ('\n' if events else '')
