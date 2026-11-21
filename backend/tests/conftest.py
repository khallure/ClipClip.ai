"""Offline tests never inherit live provider credentials or download approval."""
import pytest


@pytest.fixture(autouse=True)
def isolate_external_provider_configuration(monkeypatch):
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'AI_PROVIDER',
                 'YOUTUBE_INGEST_ENABLED', 'YOUTUBE_APPROVAL_REFERENCE', 'YOUTUBE_APPROVED_VIDEO_IDS'):
        monkeypatch.delenv(name, raising=False)
