"""Chained export remains active and preserves earlier verified results on failure."""
import json
import time
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app import media
from app.main import Pipeline
from app.models import IngestSettings
from app.store import ACTIVE, JobStore


def prepared(tmp_path):
    store = JobStore(tmp_path / 'data')
    job = store.create('Owned automatic clipping fixture', 'https://www.youtube.com/watch?v=abcdefghijk')
    directory = store.directory(job['id'])
    (directory / 'source.mp4').write_bytes(b'owned source retained')
    (directory / 'transcript.json').write_text(json.dumps({'language': 'id', 'segments': [
        {'start': 1, 'end': 15, 'text': 'Mulai dengan langkah kecil karena hasilnya lebih mudah diperiksa.'}
    ]}), encoding='utf-8')
    job.update(_source='source.mp4', media={'duration': 42, 'width': 320, 'height': 180, 'has_audio': True},
               status='ready', candidates=[{'start': 1, 'end': 15}], transcript_ready=True)
    store.save(job)
    return store, Pipeline(store), job['id']


def wait_idle(store, ident):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = store.get(ident)
        if job['status'] not in ACTIVE:
            return job
        time.sleep(.01)
    pytest.fail('Worker did not leave its active state.')


def test_chained_caption_alignment_never_opens_job_to_a_second_operation(tmp_path, monkeypatch):
    store, pipeline, ident = prepared(tmp_path)
    store.update(ident, status='analyzing', auto_export={'status': 'queued'})
    transcript = {'language': 'id', 'word_timestamps': True, 'segments': [
        {'start': 1, 'end': 15, 'text': 'Mulai sekarang.', 'words': [
            {'word': 'Mulai', 'start': 1, 'end': 6}, {'word': 'sekarang.', 'start': 6, 'end': 15}]}]}
    monkeypatch.setattr(media, 'transcribe', Mock(return_value=transcript))
    statuses = []
    original_update = store.update

    def capture(ident, **fields):
        statuses.append(fields.get('status'))
        return original_update(ident, **fields)

    monkeypatch.setattr(store, 'update', capture)
    checked = []

    def export(ident, task, cancelled):
        assert store.get(ident)['status'] in ACTIVE
        with pytest.raises(ValueError, match='Cancel processing'):
            store.delete(ident)
        with pytest.raises(HTTPException) as denied:
            pipeline.enqueue(ident, {'type': 'analysis', 'settings': {}})
        assert denied.value.status_code == 409
        checked.append(task['export_id'])

    monkeypatch.setattr(pipeline, 'export', export)
    pipeline.auto_export(ident, IngestSettings(auto_export=True, duration_preset='20').model_dump(), lambda: False)
    assert checked and 'ready' not in statuses
    assert (store.directory(ident) / 'caption_alignment.json').is_file()


@pytest.mark.parametrize('task_type', ['analysis', 'align_captions'])
def test_later_failed_operation_preserves_completed_automatic_mp4(tmp_path, monkeypatch, task_type):
    store, pipeline, ident = prepared(tmp_path)
    previous = {'id': 'a' * 32, 'status': 'ready', 'filename': 'clip.mp4', 'error': None}
    store.update(ident, exports=[previous], auto_export={'status': 'ready', 'export_id': previous['id']})
    operation = 'analyze' if task_type == 'analysis' else 'align_captions'
    monkeypatch.setattr(pipeline, operation, Mock(side_effect=media.MediaError('Controlled later failure.')))
    pipeline.start()
    try:
        task = {'type': task_type}
        if task_type == 'analysis':
            task['settings'] = {}
        pipeline.enqueue(ident, task)
        failed = wait_idle(store, ident)
        assert failed['status'] == 'blocked'
        assert failed['exports'] == [previous]
        assert failed['auto_export'] == {'status': 'ready', 'export_id': previous['id']}
    finally:
        pipeline.close()


@pytest.mark.parametrize('automatic_status', ['queued', 'processing'])
def test_restart_marks_unfinished_automatic_export_failed_and_preserves_files(tmp_path, automatic_status):
    store, _, ident = prepared(tmp_path)
    previous = {'id': 'a' * 32, 'status': 'ready', 'filename': 'clip.mp4', 'error': None}
    pending = {'id': 'b' * 32, 'status': automatic_status, 'filename': None, 'error': None}
    store.update(ident, status='exporting', exports=[previous, pending],
                 auto_export={'status': automatic_status, 'export_id': pending['id']})
    store.recover()
    recovered = store.get(ident)
    assert recovered['status'] == 'blocked' and recovered['auto_export']['status'] == 'failed'
    assert recovered['exports'][0] == previous
    assert recovered['exports'][1]['status'] == 'failed'
    assert (store.directory(ident) / 'source.mp4').read_bytes() == b'owned source retained'
    assert (store.directory(ident) / 'transcript.json').is_file()


def test_failed_automatic_export_retries_export_without_ingestion_or_analysis(tmp_path, monkeypatch):
    store, pipeline, ident = prepared(tmp_path)
    analysis = Mock(side_effect=lambda ident, settings, cancelled, **kwargs: store.update(ident, status='analyzing'))
    monkeypatch.setattr(pipeline, 'analyze', analysis)
    transport = Mock(side_effect=AssertionError('The existing source must be reused.'))
    monkeypatch.setattr(pipeline.ingestor, 'ingest', transport)
    exports = []

    def export(ident, task, cancelled):
        exports.append(task['export_id'])
        if len(exports) == 1:
            raise media.MediaError('Controlled encode failure.')
        job = store.get(ident)
        record = next(e for e in job['exports'] if e['id'] == task['export_id'])
        record.update(status='ready', filename='clip.mp4', error=None)
        job['auto_export']['status'] = 'ready'
        job['status'] = 'ready'
        store.save(job)

    monkeypatch.setattr(pipeline, 'export', export)
    pipeline.start()
    try:
        pipeline.enqueue(ident, {'type': 'ingest', 'settings': IngestSettings(
            auto_export=True, duration_preset='20', output={'caption_style': 'none'}).model_dump()})
        failed = wait_idle(store, ident)
        assert failed['auto_export']['status'] == 'failed'
        assert failed['exports'][0]['status'] == 'failed'
        assert failed['_last_task']['type'] == 'export'
        pipeline.enqueue(ident, failed['_last_task'])
        retried = wait_idle(store, ident)
        assert retried['auto_export']['status'] == 'ready'
        assert len(retried['exports']) == 1 and retried['exports'][0]['status'] == 'ready'
        assert exports[0] == exports[1]
        assert analysis.call_count == 1 and transport.call_count == 0
    finally:
        pipeline.close()


def test_no_complete_candidate_skips_automatic_export_and_preserves_prior_mp4(tmp_path, monkeypatch):
    store, pipeline, ident = prepared(tmp_path)
    prior = {'id': 'c' * 32, 'status': 'ready', 'filename': 'clip.mp4', 'error': None}
    store.update(ident, status='analyzing', candidates=[], exports=[prior], auto_export={'status': 'queued'})
    export = Mock(side_effect=AssertionError('No filler clip may be exported.'))
    monkeypatch.setattr(pipeline, 'export', export)
    pipeline.auto_export(ident, IngestSettings(auto_export=True).model_dump(), lambda: False)
    job = store.get(ident)
    assert job['status'] == 'ready' and job['auto_export']['status'] == 'skipped'
    assert job['auto_export']['message'] and job['exports'] == [prior]
    assert job['candidates'] == [] and export.call_count == 0
