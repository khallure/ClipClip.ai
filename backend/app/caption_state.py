"""Caption corrections/alignment are separate from original editorial evidence."""
import json
from pathlib import Path


def has_words(transcript):
    return bool(transcript and any(s.get('words') for s in transcript.get('segments', [])))


def caption_transcript(directory: Path):
    original = directory / 'transcript.json'
    transcript = json.loads(original.read_text(encoding='utf-8')) if original.is_file() else None
    if has_words(transcript):
        return transcript
    alignment = directory / 'caption_alignment.json'
    return json.loads(alignment.read_text(encoding='utf-8')) if alignment.is_file() else transcript


def corrections(directory: Path):
    path = directory / 'caption_corrections.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
