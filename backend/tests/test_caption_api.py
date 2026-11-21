"""Canonical caption API, persistent edits, and local export integration.

These checks never contact a speech service or download a transcription model.
"""
import copy
import json
import subprocess
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import config, media
from app.main import create_app


MEDIA = {'duration': 60.0, 'width': 641, 'height': 359, 'fps': 30.0,
         'video_codec': 'h264', 'audio_codec': 'aac', 'has_audio': True}
TIMED = {'language': 'id', 'segments': [
    {'start': 0, 'end': 5, 'text': 'Ini konteks.', 'words': [
        {'start': .2, 'end': .6, 'word': ' Ini'},
        {'start': .7, 'end': 1.4, 'word': ' konteks.'}]},
    {'start': 10, 'end': 15, 'text': 'Ternyata biaya besar.', 'words': [
        {'start': 10, 'end': 10.2, 'word': ' Ternyata'},
        {'start': 10.3, 'end': 10.6, 'word': ' biaya'},
        {'start': 11.1, 'end': 12, 'word': ' besar.'}]}]}
PLAIN = {'language': 'id', 'segments': [
    {'start': 0, 'end': 15, 'text': 'Ini konteks. Ternyata biaya besar.'}]}


@pytest.fixture
def caption_app(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(media, 'probe', Mock(return_value=dict(MEDIA)))
    monkeypatch.setattr(media, 'make_poster', Mock())
    monkeypatch.setattr(media, 'transcribe', Mock(side_effect=media.MediaError('Pasang Whisper lokal atau impor waktu kata asli.')))
    return create_app(tmp_path / 'jobs')


@pytest.fixture
def client(caption_app):
    with TestClient(caption_app) as instance:
        yield instance


def prepare(client, transcript=TIMED):
    response = client.post('/api/jobs', json={})
    assert response.status_code == 201, response.text
    ident = response.json()['id']
    response = client.post(f'/api/jobs/{ident}/source', files={'file': ('owned.mp4', b'owned-local-source', 'video/mp4')})
    assert response.status_code == 200, response.text
    if transcript is not None:
        response = import_transcript(client, ident, transcript)
        assert response.status_code == 200, response.text
    return ident


def import_transcript(client, ident, transcript):
    return client.post(f'/api/jobs/{ident}/transcript', files={
        'file': ('speech.json', json.dumps(transcript, ensure_ascii=False).encode(), 'application/json')})


def preview(client, ident, **changes):
    return client.post(f'/api/jobs/{ident}/caption-preview', json={'start': 10.1, 'end': 11.8, **changes})


def wait_idle(client, ident):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f'/api/jobs/{ident}').json()
        if result['status'] not in {'queued', 'transcribing', 'analyzing', 'exporting'}:
            return result
        time.sleep(.01)
    pytest.fail(f'Caption task did not finish: {result}')


def test_preview_keeps_global_ids_and_rebases_exactly_once(client):
    ident = prepare(client)
    response = preview(client, ident)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document['start'] == 10.1 and document['duration'] == 1.7
    assert (document['width'], document['height']) == (642, 360)
    assert document['words'] == [
        {'id': 2, 'start': 0.0, 'end': .1, 'text': 'Ternyata'},
        {'id': 3, 'start': .2, 'end': .5, 'text': 'biaya'},
        {'id': 4, 'start': 1.0, 'end': 1.7, 'text': 'besar.'}]
    assert [word for group in document['groups'] for word in group['word_ids']] == [2, 3, 4]
    assert all(0 <= group['start'] < group['end'] <= 1.7 for group in document['groups'])
    vertical = preview(client, ident, aspect='vertical', style={'font_weight': 700, 'font_size_pct': 5.5, 'position_y_pct': 65}).json()
    assert (vertical['width'], vertical['height']) == (1080, 1920)
    assert vertical['style']['font_weight'] == 700 and vertical['style']['position_y_pct'] == 65
    assert vertical['words'] == document['words']
    media.transcribe.assert_not_called()


def test_corrections_persist_without_changing_times_original_or_candidates(client, caption_app):
    ident = prepare(client)
    directory = caption_app.state.store.directory(ident)
    original_bytes = (directory / 'transcript.json').read_bytes()
    original = client.get(f'/api/jobs/{ident}/transcript').json()
    candidates = [{'id': 'preserved-card', 'start': 10, 'end': 15}]
    caption_app.state.store.update(ident, candidates=candidates, analysis_mode='heuristic')
    before = preview(client, ident).json()
    response = client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 3, 'text': 'manfaat'}]})
    assert response.status_code == 200 and response.json() == {'saved': True}
    after = preview(client, ident).json()
    assert after['words'][1] == {**before['words'][1], 'text': 'manfaat'}
    assert [{key: word[key] for key in ('id', 'start', 'end')} for word in after['words']] == [
        {key: word[key] for key in ('id', 'start', 'end')} for word in before['words']]
    assert json.loads((directory / 'caption_corrections.json').read_text()) == {'3': 'manfaat'}
    assert client.get(f'/api/jobs/{ident}/transcript').json() == original
    assert (directory / 'transcript.json').read_bytes() == original_bytes
    assert client.get(f'/api/jobs/{ident}').json()['candidates'] == candidates
    # A second, non-overlapping trim still uses the same global identifiers.
    earlier = preview(client, ident, start=0, end=2).json()
    assert [word['id'] for word in earlier['words']] == [0, 1]
    assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 0, 'text': 'Inilah'}]}).status_code == 200
    assert json.loads((directory / 'caption_corrections.json').read_text()) == {'0': 'Inilah', '3': 'manfaat'}
    media.transcribe.assert_not_called()


@pytest.mark.parametrize('changes', [
    [], [{'word_id': 99, 'text': 'kata'}], [{'word_id': -1, 'text': 'kata'}],
    [{'word_id': True, 'text': 'kata'}], [{'word_id': '3', 'text': 'kata'}],
    [{'word_id': 3, 'text': 'dua kata'}], [{'word_id': 3, 'text': ' '}],
    [{'word_id': 3, 'text': 'a' * 81}], [{'word_id': 3, 'text': 'kata', 'start': 0}],
])
def test_invalid_corrections_leave_original_and_saved_edits_untouched(client, caption_app, changes):
    ident = prepare(client)
    original = client.get(f'/api/jobs/{ident}/transcript').json()
    assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 3, 'text': 'hasil'}]}).status_code == 200
    response = client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': changes})
    assert response.status_code == 422, response.text
    assert client.get(f'/api/jobs/{ident}/transcript').json() == original
    saved = caption_app.state.store.directory(ident) / 'caption_corrections.json'
    assert json.loads(saved.read_text()) == {'3': 'hasil'}


@pytest.mark.parametrize('changes', [
    {'start': -1}, {'start': 12, 'end': 11}, {'end': 61}, {'aspect': 'square'},
    {'style': {'font_weight': 500}}, {'style': {'active_color': 'yellow'}},
    {'style': {'font_size_pct': 20}}, {'style': {'background': 'black'}}, {'motion': 'pop'},
])
def test_preview_rejects_invalid_range_style_or_extra_controls(client, changes):
    ident = prepare(client)
    response = preview(client, ident, **changes)
    assert response.status_code == 422, response.text
    media.transcribe.assert_not_called()


def test_invalid_historical_word_text_returns_actionable_error_instead_of_server_failure(client, caption_app):
    ident = prepare(client)
    directory = caption_app.state.store.directory(ident)
    path = directory / 'transcript.json'
    historical = json.loads(path.read_text(encoding='utf-8'))
    historical['segments'][1]['words'][1]['word'] = ' '
    path.write_text(json.dumps(historical), encoding='utf-8')
    response = preview(client, ident)
    assert response.status_code == 422, response.text
    assert 'kata' in response.text.lower()
    assert json.loads(path.read_text()) == historical
    media.transcribe.assert_not_called()


def test_word_too_long_to_fit_returns_actionable_preview_error(client):
    ident = prepare(client)
    response = client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 3, 'text': 'W' * 80}]})
    assert response.status_code == 200
    response = preview(client, ident, aspect='vertical')
    assert response.status_code == 422, response.text
    assert 'panjang' in response.text.lower()
    media.transcribe.assert_not_called()


def test_missing_word_timing_blocks_highlight_without_inventing_times(client, caption_app):
    ident = prepare(client, PLAIN)
    directory = caption_app.state.store.directory(ident)
    original = (directory / 'transcript.json').read_bytes()
    response = preview(client, ident)
    assert response.status_code == 409 and 'per kata' in response.text
    assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 0, 'text': 'Edit'}]}).status_code == 409
    response = client.post(f'/api/jobs/{ident}/exports', json={'start': 10, 'end': 12, 'caption_style': 'poppins_highlight'})
    assert response.status_code == 409
    assert client.get(f'/api/jobs/{ident}').json()['exports'] == []
    assert (directory / 'transcript.json').read_bytes() == original
    assert not (directory / 'caption_alignment.json').exists()
    assert not (directory / 'caption_corrections.json').exists()
    media.transcribe.assert_not_called()


def test_existing_word_timing_is_reused_without_queueing_or_retranscription(client):
    ident = prepare(client)
    response = client.post(f'/api/jobs/{ident}/captions/align')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'uploaded'
    assert 'tidak diulang' in response.json()['message']
    assert preview(client, ident).status_code == 200
    media.transcribe.assert_not_called()


def test_alignment_stores_separate_evidence_and_reuses_its_cache(client, caption_app):
    ident = prepare(client, PLAIN)
    directory = caption_app.state.store.directory(ident)
    original = (directory / 'transcript.json').read_bytes()
    media.transcribe.side_effect = None
    media.transcribe.return_value = copy.deepcopy(TIMED)
    response = client.post(f'/api/jobs/{ident}/captions/align')
    assert response.status_code == 200, response.text
    job = wait_idle(client, ident)
    assert job['status'] == 'ready', job
    assert (directory / 'transcript.json').read_bytes() == original
    assert json.loads((directory / 'caption_alignment.json').read_text()) == TIMED
    assert preview(client, ident).json()['words'][0]['id'] == 2
    assert media.transcribe.call_count == 1
    assert client.post(f'/api/jobs/{ident}/captions/align').status_code == 200
    assert media.transcribe.call_count == 1
    assert client.get(f'/api/jobs/{ident}/transcript').json()['word_timestamps'] is False


@pytest.mark.parametrize('result', ['unavailable', 'no_words', 'no_audio'])
def test_failed_alignment_never_synthesizes_or_replaces_source_transcript(client, caption_app, result):
    ident = prepare(client, PLAIN)
    directory = caption_app.state.store.directory(ident)
    original = (directory / 'transcript.json').read_bytes()
    if result == 'no_words':
        media.transcribe.side_effect = None
        media.transcribe.return_value = copy.deepcopy(PLAIN)
    elif result == 'no_audio':
        caption_app.state.store.update(ident, media={**MEDIA, 'has_audio': False, 'audio_codec': None})
    assert client.post(f'/api/jobs/{ident}/captions/align').status_code == 200
    job = wait_idle(client, ident)
    assert job['status'] == 'blocked', job
    assert job['error']
    assert (directory / 'transcript.json').read_bytes() == original
    assert not (directory / 'caption_alignment.json').exists()
    assert preview(client, ident).status_code == 409
    if result == 'no_audio':
        media.transcribe.assert_not_called()


def test_reimport_archives_old_caption_ids_and_does_not_apply_stale_edits(client, caption_app):
    ident = prepare(client, PLAIN)
    media.transcribe.side_effect = None
    media.transcribe.return_value = copy.deepcopy(TIMED)
    client.post(f'/api/jobs/{ident}/captions/align')
    assert wait_idle(client, ident)['status'] == 'ready'
    assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 3, 'text': 'sebelumnya'}]}).status_code == 200
    replacement = copy.deepcopy(TIMED)
    replacement['segments'][1]['words'][1]['word'] = ' sesudahnya'
    assert import_transcript(client, ident, replacement).status_code == 200
    document = preview(client, ident).json()
    assert document['words'][1]['text'] == 'sesudahnya'
    directory = caption_app.state.store.directory(ident)
    assert not (directory / 'caption_alignment.json').exists()
    assert not (directory / 'caption_corrections.json').exists()
    assert len(list((directory / 'caption_history').glob('*'))) == 2


def test_ass_sidecar_requires_verified_export_and_existing_file(client, caption_app):
    ident = prepare(client)
    export_id = 'b' * 32
    path = f'/api/jobs/{ident}/exports/{export_id}/captions.ass'
    assert client.get(path).status_code == 409
    caption_app.state.store.update(ident, exports=[{'id': export_id, 'status': 'processing'}])
    assert client.get(path).status_code == 409
    caption_app.state.store.update(ident, exports=[{'id': export_id, 'status': 'ready'}])
    assert client.get(path).status_code == 404
    directory = caption_app.state.store.directory(ident) / 'exports' / export_id
    directory.mkdir(parents=True)
    (directory / 'captions.ass').write_text('[Script Info]\n; verified styled captions', encoding='utf-8')
    response = client.get(path)
    assert response.status_code == 200 and response.text.startswith('[Script Info]')
    assert 'captions.ass' in response.headers['content-disposition']


@pytest.mark.parametrize('style', ['none', 'clean', 'bold'])
def test_legacy_caption_styles_still_export_segment_only_transcripts(client, monkeypatch, style):
    ident = prepare(client, PLAIN)
    def encode(source, directory, settings, transcript, source_info, cancelled, **kwargs):
        assert settings['caption_style'] == style
        assert transcript['segments'][0]['text'] == PLAIN['segments'][0]['text']
        directory.mkdir(parents=True)
        (directory / 'clip.mp4').write_bytes(b'verified-placeholder')
        (directory / 'captions.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nIni konteks.\n', encoding='utf-8')
        return {**MEDIA, 'duration': 2, 'width': 642, 'height': 360}
    monkeypatch.setattr(media, 'export_clip', encode)
    response = client.post(f'/api/jobs/{ident}/exports', json={'start': 10, 'end': 12, 'caption_style': style})
    assert response.status_code == 202, response.text
    assert wait_idle(client, ident)['status'] == 'ready'
    media.transcribe.assert_not_called()


def test_preview_and_export_use_same_canonical_document_and_corrections(client, caption_app, monkeypatch):
    ident = prepare(client)
    assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 3, 'text': 'manfaat'}]}).status_code == 200
    style = {'font_weight': 700, 'font_size_pct': 6, 'position_y_pct': 68, 'max_words': 3}
    document = preview(client, ident, start=10, end=12, style=style).json()
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        # Only codec invocation is mocked; media.export_clip builds real sidecars.
        from pathlib import Path
        Path(command[-1]).write_bytes(b'verified-output-placeholder')
        return ''
    monkeypatch.setattr(media, 'run', run)
    monkeypatch.setattr(media, 'tool', lambda name: name)
    media.probe.return_value = {**MEDIA, 'duration': 2, 'width': 642, 'height': 360}
    response = client.post(f'/api/jobs/{ident}/exports', json={
        'start': 10, 'end': 12, 'caption_style': 'poppins_highlight', 'subtitle_style': style})
    assert response.status_code == 202, response.text
    job = wait_idle(client, ident)
    assert job['status'] == 'ready', job
    export_id = job['exports'][-1]['id']
    directory = caption_app.state.store.directory(ident) / 'exports' / export_id
    assert json.loads((directory / 'caption_document.json').read_text(encoding='utf-8')) == document
    ass = client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.ass')
    assert ass.status_code == 200 and 'manfaat' in ass.text and 'biaya' not in ass.text
    assert 'Poppins' in ass.text and '&H5CF3D7&' in ass.text
    srt = client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.srt')
    assert srt.status_code == 200 and 'manfaat' in srt.text
    assert '00:00:00,000' in srt.text
    filters = commands[0][commands[0].index('-vf') + 1]
    assert filters.index('pad=') < filters.index('subtitles=filename=captions.ass:fontsdir=fonts')
    assert (directory / 'fonts' / 'Poppins-SemiBold.ttf').is_file()
    assert (directory / 'fonts' / 'Poppins-Bold.ttf').is_file()
    media.transcribe.assert_not_called()


def test_real_poppins_api_export_has_audio_and_matches_preview(tmp_path, monkeypatch):
    if not config.binary('ffmpeg') or not config.binary('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe required for end-to-end Poppins export.')
    monkeypatch.setattr(media, 'make_poster', Mock())
    monkeypatch.setattr(media, 'transcribe', Mock(side_effect=AssertionError('Imported words must be reused.')))
    source = tmp_path / 'owned_synthetic.mp4'
    subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'color=c=0x202020:s=640x360:r=30:d=3',
                    '-f', 'lavfi', '-i', 'sine=f=440:sample_rate=48000:duration=3',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True, timeout=30)
    transcript = {'segments': [{'start': .4, 'end': 2.7, 'text': 'Kata asli. Selesai.', 'words': [
        {'start': .4, 'end': 1.0, 'word': ' Kata'}, {'start': 1.05, 'end': 1.7, 'word': ' asli.'},
        {'start': 2.0, 'end': 2.7, 'word': ' Selesai.'}]}]}
    app = create_app(tmp_path / 'real_jobs')
    with TestClient(app) as client:
        ident = client.post('/api/jobs', json={}).json()['id']
        with source.open('rb') as data:
            response = client.post(f'/api/jobs/{ident}/source', files={'file': ('owned.mp4', data, 'video/mp4')})
        assert response.status_code == 200, response.text
        assert import_transcript(client, ident, transcript).status_code == 200
        assert client.patch(f'/api/jobs/{ident}/caption-words', json={'changes': [{'word_id': 1, 'text': 'benar.'}]}).status_code == 200
        style = {'font_weight': 700, 'font_size_pct': 7}
        document = preview(client, ident, start=.5, end=2.5, style=style).json()
        response = client.post(f'/api/jobs/{ident}/exports', json={
            'start': .5, 'end': 2.5, 'caption_style': 'poppins_highlight', 'subtitle_style': style})
        assert response.status_code == 202, response.text
        job = wait_idle(client, ident)
        assert job['status'] == 'ready', job
        export_id = job['exports'][-1]['id']
        directory = app.state.store.directory(ident) / 'exports' / export_id
        assert json.loads((directory / 'caption_document.json').read_text(encoding='utf-8')) == document
        info = media.probe(directory / 'clip.mp4')
        assert (info['width'], info['height']) == (640, 360)
        assert info['video_codec'] == 'h264' and info['audio_codec'] == 'aac' and info['has_audio']
        assert abs(info['duration'] - 2.0) <= 1/30 + .001
        assert client.get(f'/api/jobs/{ident}/exports/{export_id}/file').status_code == 200
        assert 'benar.' in client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.srt').text
        assert 'benar.' in client.get(f'/api/jobs/{ident}/exports/{export_id}/captions.ass').text
        frame = subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-ss', '0.8', '-i', str(directory / 'clip.mp4'),
                                '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                               check=True, capture_output=True, timeout=30).stdout
        lime = sum(1 for i in range(0, len(frame), 3) if frame[i] > 120 and frame[i+1] > 150 and frame[i+2] < 130 and frame[i+1] > frame[i+2] + 40)
        assert lime > 10  # Active word is actually burned into the verified MP4.
        assert client.get(f'/api/jobs/{ident}/transcript').json()['segments'][0]['words'][1]['word'].strip() == 'asli.'
        media.transcribe.assert_not_called()
