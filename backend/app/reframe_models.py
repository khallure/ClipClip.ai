"""Local reframing metadata. All times refer to the unchanged source timeline."""
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel


class ReframeInterval(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode='after')
    def interval(self):
        if not 0 < self.end - self.start <= 600:
            raise ValueError('Analisis framing mencakup satu klip, maksimal 10 menit.')
        return self


class ReframeRequest(ReframeInterval):
    mode: Literal['auto_face', 'active_speaker'] = 'auto_face'


class FaceObservation(StrictModel):
    track_id: int = Field(ge=0)
    bbox: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode='after')
    def bounds(self):
        x, y, w, h = self.bbox
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
                and x+w <= 1.00001 and y+h <= 1.00001):
            raise ValueError('Kotak wajah harus berada di dalam sumber.')
        return self


class FaceSample(StrictModel):
    time: float = Field(ge=0)
    faces: list[FaceObservation] = Field(default_factory=list, max_length=12)


class ReframeKeyframe(StrictModel):
    time: float = Field(ge=0)
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=3)
    manual: bool = False


class SpeakerOverride(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    focus: Literal['left', 'right', 'both', 'locked']
    track_id: int | None = Field(default=None, ge=0)


class SpeakerInterval(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    track_id: int | None = Field(default=None, ge=0)
    focus: Literal['auto', 'left', 'right', 'both', 'locked']
    confidence: float | None = Field(default=None, ge=0, le=1)
    score: float | None = None
    source: Literal['model', 'manual', 'fallback']
    reason: str = Field(max_length=1000)
    review_required: bool

    @model_validator(mode='after')
    def evidence(self):
        if self.source == 'model':
            if self.confidence is None or self.score is None or self.track_id is None:
                raise ValueError('Keputusan model memerlukan bukti dan track wajah.')
        elif self.confidence is not None or self.score is not None:
            raise ValueError('Pilihan manual/fallback tidak memiliki confidence model.')
        return self


class ReframeShot(StrictModel):
    id: str = Field(pattern=r'^shot-[A-Za-z0-9_-]{1,48}$')
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    focus: Literal['auto', 'left', 'right', 'both', 'locked', 'active'] = 'auto'
    review_required: bool = False
    reason: str | None = Field(default=None, max_length=1000)
    decision: Literal['single_face', 'ambiguous', 'no_face', 'manual', 'active_speaker'] = 'manual'
    # Uncalibrated ASD output, distinct from face detection or sync-distance margin.
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    speaker_intervals: list[SpeakerInterval] = Field(default_factory=list, max_length=4000)
    speaker_overrides: list[SpeakerOverride] = Field(default_factory=list, max_length=1000)
    face_samples: list[FaceSample] = Field(default_factory=list, max_length=4000)
    keyframes: list[ReframeKeyframe] = Field(min_length=1, max_length=4000)
    auto_keyframes: list[ReframeKeyframe] = Field(default_factory=list,max_length=4000)

    @model_validator(mode='after')
    def timeline(self):
        if self.end <= self.start:
            raise ValueError('Akhir shot harus setelah awal.')
        for values in (self.keyframes, self.auto_keyframes, self.face_samples):
            previous = -1.0
            for item in values:
                if not self.start-.00001 <= item.time <= self.end+.00001 or item.time <= previous:
                    raise ValueError('Waktu keyframe/deteksi harus urut, unik, dan di dalam shot.')
                previous = item.time
        for intervals, complete in ((self.speaker_intervals, True), (self.speaker_overrides, False)):
            previous = self.start
            for item in intervals:
                if not self.start <= item.start < item.end <= self.end+.00001 or item.start < previous-.00001:
                    raise ValueError('Interval speaker harus urut, di dalam shot dan tidak tumpang tindih.')
                if complete and abs(item.start-previous) > .00001:
                    raise ValueError('Keputusan speaker harus menutup shot tanpa celah.')
                previous = item.end
            if complete and intervals and abs(previous-self.end) > .00001:
                raise ValueError('Keputusan speaker harus menutup seluruh shot.')
        if self.speaker_confidence is not None and not any(i.source == 'model' for i in self.speaker_intervals):
            raise ValueError('Speaker confidence memerlukan hasil model sungguhan.')
        return self


class ReframePlan(StrictModel):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: Literal['1.0'] = '1.0'
    source_signature: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_width: int = Field(ge=2, le=16384)
    source_height: int = Field(ge=2, le=16384)
    source_fps: float = Field(gt=0, le=240)
    source_duration: float = Field(gt=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    mode: Literal['manual', 'auto_face', 'active_speaker'] = 'manual'
    smoothing: float = Field(default=.65, ge=0, le=1)
    shots: list[ReframeShot] = Field(min_length=1, max_length=1200)
    analysis: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def coverage(self):
        if not 0 < self.end-self.start <= 600 or self.end > self.source_duration+.01:
            raise ValueError('Rentang framing harus berada dalam video sumber, maksimal 10 menit.')
        previous = self.start
        total = 0
        for shot in self.shots:
            if abs(shot.start-previous) > .002 or shot.end > self.end+.002:
                raise ValueError('Shot harus berurutan dan menutup rentang tanpa celah/tumpang tindih.')
            previous = shot.end
            total += len(shot.keyframes)+len(shot.auto_keyframes)+len(shot.face_samples)+len(shot.speaker_intervals)+len(shot.speaker_overrides)
        if abs(previous-self.end) > .002 or total > 24000:
            raise ValueError('Rentang atau jumlah metadata framing tidak valid.')
        if len({shot.id for shot in self.shots}) != len(self.shots):
            raise ValueError('ID shot harus unik.')
        return self
