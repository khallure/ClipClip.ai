"""Real Poppins fonts, shared phrase layout and word-boundary subtitle tests."""
import copy
import hashlib
import math
import re
import shutil
import subprocess

import pytest

from app import config
from app.captions import (CaptionStyle, _join, _metrics, build_caption_document,
                          font_directory, to_ass, to_srt, to_vtt)


def words_fixture(offset=0):
    texts = ['our', 'cost', 'of', 'day.']
    bounds = [(.1, .4), (.45, .8), (.85, 1.1), (1.2, 1.5)]
    return [{'start': offset, 'end': offset+1.7, 'text': 'our cost of day.', 'words': [
        {'start': offset+a, 'end': offset+b, 'word': text} for text, (a, b) in zip(texts, bounds)]}]


def doc(style=None):
    return build_caption_document(words_fixture(), 0, 1.8, 640, 360, style)


def active_at(document, seconds):
    group = next((g for g in document['groups'] if g['start'] <= seconds < g['end']), None)
    if group is None:
        return None
    candidates = [w for w in document['words'] if w['id'] in group['word_ids'] and w['start'] <= seconds < w['end']]
    return max(candidates, key=lambda w: (w['start'], w['id']))['id'] if candidates else None


def test_real_font_files_and_licenses_are_bundled_identically():
    expected = {600: ('Poppins-SemiBold.ttf', 'd3bf1bdaf0550e83da9ac0b1d1d9fe6db086835a83aa28578e609a394b9a0286'),
                700: ('Poppins-Bold.ttf', '983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c')}
    directory = font_directory()
    for weight, (name, digest) in expected.items():
        assert hashlib.sha256((directory/name).read_bytes()).hexdigest() == digest
        assert _metrics(weight).weight == weight
        public = config.PROJECT / 'frontend/public/fonts' / name
        assert public.read_bytes() == (directory/name).read_bytes()
    assert 'SIL OPEN FONT LICENSE' in (directory/'OFL.txt').read_text(encoding='utf-8')


def test_ass_font_names_and_css_metric_compensation():
    for weight, family in [(600, 'Poppins SemiBold'), (700, 'Poppins')]:
        document = doc({'font_weight': weight})
        size = min(document['width'], document['height']) * 4.5 / 100
        info = document['render_info']
        assert info['font_size_px'] == size
        assert math.isclose(info['ass_font_size_px'], size*1.762, abs_tol=1e-6)
        assert info['ascent_override_pct'] == 113.5
        assert info['descent_override_pct'] == 62.7
        assert info['line_height'] == 1.15
        output = to_ass(document)
        assert f'Style: PoppinsHighlight,{family},' in output
        assert f'\\b{weight}' in output
        assert 'Kerning: no' in output


@pytest.mark.parametrize('style', [
    {'font_weight': 400}, {'font_size_pct': 1.9}, {'font_size_pct': 10}, {'font_size_pct': float('nan')},
    {'position_y_pct': 86}, {'active_color': 'url(evil)'}, {'max_words': 6}, {'max_words': True},
    {'stroke_px': -1}, {'shadow_px': 4}, {'background': '#123456'}, {'font_size_pct': True},
])
def test_style_schema_rejects_unknown_or_unsafe_inputs(style):
    with pytest.raises(ValueError):
        CaptionStyle.model_validate(style)


def test_clip_relative_timing_and_global_word_ids_survive_trim():
    segments = [{'start': 0, 'end': 1, 'text': 'Earlier.', 'words': [{'start': 0, 'end': .8, 'word': 'Earlier.'}]}] + words_fixture(10)
    document = build_caption_document(segments, 10, 11.6, 1080, 1920)
    assert [w['id'] for w in document['words']] == [1, 2, 3, 4]
    assert document['words'][0] == {'id': 1, 'start': .1, 'end': .4, 'text': 'our'}
    assert document['words'][-1]['end'] == 1.5
    assert document['start'] == 10 and document['duration'] == 1.6
    assert '00:00:00,100' in to_srt(document) and '00:00:10,100' not in to_srt(document)
    assert '0:00:00.10' in to_ass(document)


def test_partial_boundary_clamps_real_word_interval_without_interpolation():
    document = build_caption_document(words_fixture(10), 10.2, 11.35, 640, 360)
    assert document['words'][0]['start'] == 0 and document['words'][0]['end'] == .2
    assert document['words'][-1]['start'] == 1 and document['words'][-1]['end'] == 1.15
    assert all(0 <= w['start'] < w['end'] <= document['duration'] for w in document['words'])


def test_text_corrections_keep_source_and_word_timing_unchanged():
    source = words_fixture()
    original = copy.deepcopy(source)
    document = build_caption_document(source, 0, 1.8, 640, 360, corrections={'1': 'price'})
    assert document['words'][1] == {'id': 1, 'start': .45, 'end': .8, 'text': 'price'}
    assert source == original
    assert 'price' in to_srt(document) and 'cost' not in to_srt(document)


@pytest.mark.parametrize('changes', [{'4': 'missing'}, {'01': 'bad'}, {'-1': 'bad'}, {'1': 'two words'}, {'1': ''}, {'1': 'x'*81}, {True: 'bad'}])
def test_bad_corrections_rejected(changes):
    with pytest.raises(ValueError):
        build_caption_document(words_fixture(), 0, 1.8, 640, 360, corrections=changes)


def test_missing_word_timing_never_creates_estimated_words():
    document = build_caption_document([{'start': 0, 'end': 2, 'text': 'Hello world.'}], 0, 2, 640, 360)
    assert document['words'] == [] and document['groups'] == []
    assert document['warnings']
    assert to_srt(document) == '' and to_vtt(document) == 'WEBVTT\n\n'
    assert 'Dialogue:' not in to_ass(document)


def test_semantic_punctuation_gaps_and_numeric_units():
    texts = ['Biayanya', '30', '%', 'saja.', 'Lalu', 'kita', 'lanjut.']
    times = [(0,.2),(.2,.4),(.4,.5),(.5,.8),(1.8,2),(2,2.2),(2.2,2.5)]
    source = [{'start': 0, 'end': 3, 'text': '', 'words': [{'start': a,'end': b,'word': t} for t,(a,b) in zip(texts,times)]}]
    document = build_caption_document(source, 0, 3, 1080, 1920)
    assert document['groups'][0]['word_ids'] == [0, 1, 2, 3]
    assert document['groups'][1]['word_ids'] == [4, 5, 6]
    assert 'Biayanya 30% saja.' in to_srt(document)
    assert active_at(document, 1.3) is None
    assert active_at(document, .3) == 1


def test_phrase_lines_are_shared_and_never_exceed_two():
    texts = ['extraordinary', 'international', 'infrastructure', 'development', 'benefits.']
    source = [{'start': 0, 'end': 3, 'text': '', 'words': [{'start': i*.5, 'end': i*.5+.4, 'word': t} for i,t in enumerate(texts)]}]
    document = build_caption_document(source, 0, 3, 640, 360, {'font_size_pct': 9, 'max_words': 5})
    assert any(len(g['lines']) == 2 for g in document['groups'])
    by_id = {w['id']: w for w in document['words']}
    for group in document['groups']:
        assert 1 <= len(group['lines']) <= 2
        assert [i for line in group['lines'] for i in line] == group['word_ids']
        for line in group['lines']:
            assert _metrics(600).width(_join([by_id[i] for i in line]), document['render_info']['font_size_px']) <= 640*.82 + .01
    ass = to_ass(document)
    assert '\\N' not in ass  # Explicit per-line positions keep CSS1.15 line spacing identical.
    assert len(set(re.findall(r'\\pos\([^,]+,([^)]+)\)', ass))) >= 2


def test_overlap_at_phrase_boundary_never_displays_two_active_groups():
    source = words_fixture()
    source[0]['words'][1]['end'] = .86
    document = build_caption_document(source, 0, 1.8, 640, 360, {'max_words': 2})
    assert document['groups'][0]['end'] <= document['groups'][1]['start']
    ass = to_ass(document)
    for seconds in [i/100 for i in range(180)]:
        active_events = []
        for line in ass.splitlines():
            if not line.startswith('Dialogue: '):
                continue
            fields = line.split(',', 9)
            def sec(stamp):
                h,m,s = stamp.split(':'); return int(h)*3600+int(m)*60+float(s)
            if sec(fields[1]) <= seconds < sec(fields[2]):
                active_events.append(fields[9])
        assert sum(text.count('\\c&H5CF3D7&') for text in active_events) <= 1


def test_ass_never_uses_a_box_animation_or_injected_tags():
    source = words_fixture()
    source[0]['words'][1]['word'] = r'{\pos(1,1)}cost'
    document = build_caption_document(source, 0, 1.8, 640, 360)
    output = to_ass(document)
    assert r'\pos(1,1)' not in output
    assert r'\t(' not in output and r'\move(' not in output and r'\fad(' not in output
    style = next(line for line in output.splitlines() if line.startswith('Style:'))
    fields = next(line for line in output.splitlines() if line.startswith('Format: Name,Fontname')).removeprefix('Format: ').split(',')
    assert dict(zip(fields, style.removeprefix('Style: ').split(',')))['BorderStyle'] == '1'
    assert document['warnings']


def test_srt_vtt_are_plain_text_while_ass_has_only_one_active_color():
    document = doc()
    assert '\\c&' not in to_srt(document) and '#D7F35C' not in to_vtt(document)
    assert to_vtt(document).startswith('WEBVTT\n\n')
    assert '00:00:00.100 -->' in to_vtt(document)
    for line in to_ass(document).splitlines():
        if line.startswith('Dialogue: '):
            assert line.count('\\c&H5CF3D7&') <= 1


def test_twenty_second_clip_limits_all_caption_clocks():
    source = [{'start': 10, 'end': 31, 'text': '', 'words': [{'start': 10+i*.5, 'end': 10+i*.5+.4, 'word': f'kata{i}'} for i in range(42)]}]
    document = build_caption_document(source, 10, 30, 1080, 1920)
    assert document['duration'] == 20
    assert max(w['end'] for w in document['words']) <= 20
    assert max(g['end'] for g in document['groups']) <= 20
    assert '0:00:20.00' in to_ass(document)


@pytest.mark.parametrize('weight,family', [(600, 'Poppins-SemiBold'), (700, 'Poppins-Bold')])
def test_real_ffmpeg_font_selection_and_23_word_highlight_frames(tmp_path, weight, family):
    if not config.binary('ffmpeg'):
        pytest.skip('Real FFmpeg/libass required.')
    document = doc({'font_weight': weight, 'font_size_pct': 9, 'stroke_px': 0, 'shadow_px': 0})
    (tmp_path/'captions.ass').write_text(to_ass(document), encoding='utf-8')
    shutil.copytree(font_directory(), tmp_path/'fonts')
    target = tmp_path/'caption-test.mp4'
    result = subprocess.run([config.binary('ffmpeg'), '-hide_banner', '-v', 'verbose', '-nostdin', '-y',
                             '-f', 'lavfi', '-i', 'color=c=0x343a3c:s=640x360:r=30:d=1.8',
                             '-vf', 'subtitles=captions.ass:fontsdir=fonts', '-c:v', 'libx264', '-crf', '0',
                             '-pix_fmt', 'yuv420p', str(target)], cwd=tmp_path, capture_output=True, timeout=40)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')[-2000:]
    logs = result.stderr.decode('utf-8', 'replace')
    assert f'-> {family},' in logs
    frames = subprocess.run([config.binary('ffmpeg'), '-v', 'error', '-i', str(target), '-pix_fmt', 'rgb24',
                             '-f', 'rawvideo', '-'], capture_output=True, check=True, timeout=30).stdout
    size = 640*360*3
    assert len(frames) >= 54*size
    metric = _metrics(weight)
    font_size = document['render_info']['font_size_px']
    phrase = document['words']
    total_width = metric.width(_join(phrase), font_size)
    first_x = 320-total_width/2
    lefts, x = {}, first_x
    for i, word in enumerate(phrase):
        if i:
            x += metric.width(' ', font_size)
        lefts[word['id']] = (x, x+metric.width(word['text'], font_size))
        x = lefts[word['id']][1]
    frames_to_check = [0, 3, 4, 6, 11, 12, 13, 14, 18, 23, 24, 25, 26, 28, 31, 33, 34, 36, 37, 41, 45, 46, 48]
    for frame_id in frames_to_check:
        rgb = memoryview(frames)[frame_id*size:(frame_id+1)*size]
        colored_x = []
        for y in range(220, 285):
            for px in range(170, 475):
                at = (y*640+px)*3
                r,g,b = rgb[at:at+3]
                if g > 180 and r > 140 and b < 170 and g-b > 65:
                    colored_x.append(px)
        active = active_at(document, frame_id/30)
        if active is None:
            assert not colored_x, (frame_id, active)
        else:
            assert len(colored_x) > 8, (frame_id, active)
            lower, upper = lefts[active]
            assert min(colored_x) >= lower-3 and max(colored_x) <= upper+3, (frame_id, active, (min(colored_x), max(colored_x)), (lower, upper))
    # These samples bracket each real word start/end within one30fps frame.
    # All other pixels remain source background: no opaque subtitle rectangle.
    test_rgb = memoryview(frames)[18*size:19*size]
    baseline = memoryview(frames)[:size]
    for y in [225, 232, 270, 278]:
        for x in [175, 190, 450, 470]:
            at = (y*640+x)*3
            assert test_rgb[at:at+3] == baseline[at:at+3]
