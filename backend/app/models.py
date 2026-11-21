import math
import re
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .captions import CaptionStyle

DurationPreset = Literal['20', '30', '45', '60', 'auto', 'custom']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def youtube_reference(value: str) -> str:
    """Validate reference only. Never resolve or fetch user supplied hosts."""
    try:
        u = urlparse(value.strip())
        if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
                or u.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}):
            raise ValueError()
        if u.hostname == 'youtu.be':
            ident = u.path.lstrip('/')
        elif u.path == '/watch':
            ident = parse_qs(u.query).get('v', [''])[0]
        elif u.path.startswith(('/shorts/', '/embed/', '/live/')):
            ident = u.path.split('/')[-1]
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
            raise ValueError()
        return f'https://www.youtube.com/watch?v={ident}'
    except (ValueError, AttributeError):
        raise ValueError('Enter a valid HTTPS YouTube video reference. Attach the owned/licensed source file to process it.') from None


class CreateJob(StrictModel):
    title: str = Field(default='Untitled episode', min_length=1, max_length=160)
    youtube_url: str | None = Field(default=None, max_length=2048)

    @field_validator('title')
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError('Project title cannot be blank.')
        return v.strip()

    @field_validator('youtube_url')
    @classmethod
    def reference(cls, v):
        return youtube_reference(v) if v else None


class AnalysisSettings(StrictModel):
    clip_count: int = Field(default=5, ge=3, le=10)
    duration_preset: DurationPreset = 'auto'
    min_duration: float = Field(default=20, ge=5, le=90)
    max_duration: float = Field(default=90, ge=5, le=90)
    target_duration: float = Field(default=45, ge=5, le=90)
    language: Literal['auto', 'id', 'en'] = 'auto'
    manual_start: float | None = Field(default=None, ge=0)
    manual_end: float | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def intervals(self):
        choices = {'20': (8, 18, 20), '30': (10, 27, 30), '45': (10, 40, 45), '60': (12, 54, 60)}
        if self.duration_preset in choices:
            self.min_duration, self.target_duration, self.max_duration = choices[self.duration_preset]
        elif self.duration_preset == 'auto' and min(self.min_duration, self.target_duration, self.max_duration) < 20:
            raise ValueError('Auto menggunakan rentang 20–90 detik. Pilih preset 20 detik atau rentang khusus untuk klip lebih pendek.')
        if not self.min_duration <= self.target_duration <= self.max_duration:
            raise ValueError('Target duration must be between minimum and maximum duration.')
        if (self.manual_start is None) != (self.manual_end is None):
            raise ValueError('Manual assist needs both start and end times.')
        if self.manual_start is not None and self.manual_end - self.manual_start < self.min_duration:
            raise ValueError('Manual range must be at least the minimum clip duration.')
        return self


class ExportSettings(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    aspect: Literal['original', 'vertical'] = 'original'
    crop_x: float = Field(default=.5, ge=0, le=1)
    caption_style: Literal['none', 'clean', 'bold', 'poppins_highlight'] = 'none'
    subtitle_style: CaptionStyle = Field(default_factory=CaptionStyle)
    duration_preset: DurationPreset | None = None
    reframe_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')

    @model_validator(mode='after')
    def intervals(self):
        if self.end <= self.start:
            raise ValueError('End must be after start.')
        if self.end - self.start > 600:
            raise ValueError('Export at most 10 minutes at a time.')
        if self.duration_preset in {'20', '30', '45', '60'} and self.end - self.start > float(self.duration_preset) + .000001:
            raise ValueError(f'Potongan melampaui batas maksimal {self.duration_preset} detik. Sesuaikan in/out tanpa memotong kalimat.')
        return self


class IngestOutput(StrictModel):
    aspect: Literal['original', 'vertical'] = 'original'
    crop_x: float = Field(default=.5, ge=0, le=1)
    caption_style: Literal['none', 'poppins_highlight'] = 'poppins_highlight'
    subtitle_style: CaptionStyle = Field(default_factory=CaptionStyle)
    reframe_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


class IngestSettings(AnalysisSettings):
    # Existing API clients keep their review-first path; the one-link UI opts in.
    auto_export: bool = False
    output: IngestOutput = Field(default_factory=IngestOutput)


class CaptionPreviewSettings(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    aspect: Literal['original', 'vertical'] = 'original'
    style: CaptionStyle = Field(default_factory=CaptionStyle)

    @model_validator(mode='after')
    def interval(self):
        if not 0 < self.end - self.start <= 600:
            raise ValueError('Pilih rentang subtitle maksimal 10 menit dengan akhir setelah awal.')
        return self


class CaptionWordChange(StrictModel):
    word_id: int = Field(ge=0, strict=True)
    text: str = Field(min_length=1, max_length=80)

    @field_validator('text')
    @classmethod
    def single_word(cls, value):
        value = value.strip()
        if not value or re.search(r'\s', value):
            raise ValueError('Edit satu kata per entri; waktu ucapan tidak berubah.')
        return value


class CaptionCorrections(StrictModel):
    changes: list[CaptionWordChange] = Field(min_length=1, max_length=2000)


def validate_transcript(payload: dict | list, duration: float) -> dict:
    if isinstance(payload, list):
        payload = {'segments': payload}
    if not isinstance(payload, dict) or not isinstance(payload.get('segments'), list):
        raise ValueError('JSON must contain a segments array with start, end and text.')
    segments, previous = [], -1.0
    for s in payload['segments']:
        try:
            a, b, t = float(s['start']), float(s['end']), s['text']
        except (KeyError, TypeError, ValueError):
            raise ValueError('Each transcript segment needs numeric start/end and text.') from None
        if (not math.isfinite(a + b) or a < 0 or b <= a or b > duration + .01 or a < previous
                or not isinstance(t, str) or not t.strip() or len(t) > 10000):
            raise ValueError('Transcript segments must be ordered, nonempty and within source duration.')
        words, word_previous = [], a
        for w in s.get('words', []):
            try:
                wa, wb, wt = float(w['start']), float(w['end']), w.get('word', w.get('text', ''))
            except (KeyError, TypeError, ValueError):
                raise ValueError('Invalid word timestamps.') from None
            if not math.isfinite(wa + wb) or not a <= wa < wb <= b + .02 or wa < word_previous - .02 or not isinstance(wt, str) or not wt.strip() or len(wt) > 200:
                raise ValueError('Word timestamps must be ordered within their segment.')
            words.append({'start': wa, 'end': wb, 'word': wt})
            word_previous = wb
        segments.append({'start': a, 'end': b, 'text': t.strip(), 'words': words,
                         'speaker': str(s['speaker'])[:100] if s.get('speaker') else None})
        previous = a
        if len(segments) > 100000:
            raise ValueError('Transcript contains too many segments.')
    if not segments:
        raise ValueError('Transcript is empty. Check that the source contains audible speech.')
    return {'segments': segments, 'language': str(payload.get('language', 'auto'))[:30],
            'word_timestamps': any(s['words'] for s in segments)}
