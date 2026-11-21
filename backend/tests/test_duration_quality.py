"""Strict duration discovery mechanics using authored timestamped evidence.

These synthetic timings are controlled test inputs, not claimed ASR output or
measurements of live semantic quality. Real export FPS/AV tests live separately.
"""
import copy
import json
import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.analysis import (
    GlobalDecision, GlobalReview, Proposal, Scores, SectionReview, _settings,
    analyze_transcript, normalize_segments, sentence_units, validate_proposal,
    weighted_score,
)


SCORES = {'hook_strength': 75, 'content_payoff': 80, 'standalone_clarity': 70,
          'emotional_impact': 55, 'edit_readiness': 90}


def authored_segment(sentences, start=0, speaker=None):
    """Each (text, duration) is an explicitly authored synthetic speech unit."""
    words, cursor = [], float(start)
    for text, duration in sentences:
        tokens = text.split()
        for i, token in enumerate(tokens):
            words.append({'start': cursor + duration * i / len(tokens),
                          'end': cursor + duration * (i + 1) / len(tokens), 'word': token})
        cursor += duration
    result = {'start': float(start), 'end': cursor, 'text': ' '.join(t for t, _ in sentences), 'words': words}
    if speaker:
        result['speaker'] = speaker
    return result


def compact_moment(duration=17.8, start=0):
    return authored_segment([
        ('Ternyata menyiram setiap hari justru membuat akar tanaman tumbuh dangkal.', duration * .44),
        ('Karena tanah perlu diperiksa, jadi siram lebih jarang tetapi cukup dalam agar akar tetap sehat.', duration * .56),
    ], start)


def proposal(segments, **changes):
    first, last = segments[0], segments[-1]
    return {
        'start_segment': 0, 'end_segment': len(segments) - 1, 'start_word': None, 'end_word': None,
        'title': 'Periksa tanah sebelum menyiram', 'hook_exact_quote': first['text'],
        'category': 'Useful explanation', 'reason': 'A stated contrast develops into a concrete action inside the selected evidence.',
        'warning': None, 'has_payoff': True, 'self_contained': True, 'supported_by_source': True,
        'scores': SCORES, **changes,
    }


@pytest.mark.parametrize('preset,minimum,target,maximum', [
    ('20', 8, 18, 20), ('30', 10, 27, 30), ('45', 10, 40, 45), ('60', 12, 54, 60),
])
def test_numeric_preset_is_server_owned_even_when_client_sliders_conflict(preset, minimum, target, maximum):
    settings = _settings({'duration_preset': preset, 'min_duration': 1, 'target_duration': 90, 'max_duration': 90}, 1000)
    assert (settings['min_duration'], settings['target_duration'], settings['max_duration']) == (minimum, target, maximum)


@pytest.mark.parametrize('preset', ['19', '90', 'AUTO', 20, None, True])
def test_unknown_duration_preset_is_rejected(preset):
    with pytest.raises(ValueError, match='preset'):
        analyze_transcript([compact_moment()], {'duration_preset': preset})


def test_auto_keeps_v1_bounds_and_custom_accepts_a_short_valid_range():
    with pytest.raises(ValueError, match='20 <='):
        analyze_transcript([compact_moment()], {'duration_preset': 'auto', 'min_duration': 19})
    result = analyze_transcript([compact_moment(9.6)], {
        'duration_preset': 'custom', 'min_duration': 5, 'target_duration': 9, 'max_duration': 10})
    assert result['candidates'] and all(c['duration'] <= 10 for c in result['candidates'])


def test_20_second_mode_accepts_a_shorter_complete_thought_without_padding():
    source = compact_moment(17.8, 42.3)
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates']
    selected = result['candidates'][0]
    assert selected['start'] == 42.3
    assert selected['end'] == source['words'][-1]['end']
    assert selected['duration'] == pytest.approx(17.8)
    assert selected['end'] - selected['start'] < 20
    assert selected['score'] == weighted_score(selected['scores'])
    assert 'Heuristic fallback' in selected['warning']


def test_long_whisper_segment_exposes_internal_sentence_endpoints_and_original_word_provenance():
    source = authored_segment([
        ('Um okay yeah hmm.', 5),
        ('Ternyata hasil kerja bukan soal sibuk, karena satu langkah jelas membuat pekerjaan selesai.', 14.5),
        ('Um okay yeah hmm.', 18),
    ], 180)
    before = copy.deepcopy(source)
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates']
    candidate = result['candidates'][0]
    assert candidate['start'] == 185
    assert candidate['end'] == 199.5
    assert candidate['source_evidence'][0]['source_segment_index'] == 0
    assert candidate['source_evidence'][0]['source_word_start'] == 4
    assert candidate['source_evidence'][0]['source_word_end'] == 16
    assert source == before


def test_incomplete_sentence_over_cap_is_not_truncated_to_20_seconds():
    source = authored_segment([('Ternyata persoalannya bukan kemauan karena pekerjaan tanpa langkah awal terus membuat orang menunda dan kehilangan kesempatan menyelesaikan targetnya.', 20.001)])
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates'] == []
    assert any('20.00' in warning and 'kalimat tidak dipotong' in warning for warning in result['warnings'])
    assert result['coverage'][0]['end'] == 20.001


def test_ai_duration_validation_independently_rejects_even_a_small_cap_overrun():
    segments = normalize_segments([compact_moment(20.001)])
    settings = _settings({'duration_preset': '20'}, 20.001)
    with pytest.raises(ValueError, match='requested duration'):
        validate_proposal(proposal(segments), segments, settings)


def test_a_small_complete_qualified_moment_is_explicitly_lower_evidence_not_filler():
    source = authored_segment([('Ternyata keberhasilan datang karena konsistensi, bukan karena motivasi sesaat.', 8.4)])
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates']
    candidate = result['candidates'][0]
    assert candidate['duration'] == 8.4
    assert candidate['scores']['standalone_clarity'] < 60
    assert 'Momen ini singkat' in candidate['warning']
    assert candidate['review_mode'] == 'heuristic'


def test_partial_unpunctuated_continuation_cannot_be_a_short_clip_start():
    source = [
        {'start': 0, 'end': 8, 'text': 'The explanation begins with an unfinished clause'},
        {'start': 8, 'end': 18, 'text': 'because the actual result was unexpected, but the lesson is to check the work before sending it.'},
    ]
    normalized = sentence_units(normalize_segments(source))
    assert normalized[1]['_natural_start'] is False
    settings = _settings({'duration_preset': '20'}, 18)
    with pytest.raises(ValueError, match='sentence boundaries'):
        validate_proposal(proposal([source[1]], start_segment=1, end_segment=1,
                                   hook_exact_quote=source[1]['text']), normalized, settings)


def test_abbreviations_and_decimals_do_not_create_fake_sentence_units():
    source = authored_segment([('Dr. Maya measured 3.14 units because the old estimate was wrong. Jadi periksa ukurannya sebelum mengambil keputusan.', 17)])
    units = sentence_units(normalize_segments([source]))
    assert len(units) == 2
    assert units[0]['text'].startswith('Dr. Maya measured 3.14')
    assert units[0]['text'].endswith('wrong.')


def test_overlapping_short_windows_are_deduplicated_and_late_episode_moments_are_found():
    segments = [{'start': float(t), 'end': float(t + 30), 'text': 'Um okay yeah hmm. Um okay yeah hmm.'}
                for t in range(0, 1080, 30)]
    segments[3] = authored_segment([
        ('Ternyata menyiram setiap hari justru membuat akar tanaman dangkal.', 7.5),
        ('Karena tanah menyimpan air, jadi periksa kelembapan sebelum menyiram agar tanaman sehat.', 9.5),
        ('Um okay yeah hmm.', 13),
    ], 90)
    segments[17] = authored_segment([
        ('I remember losing my first contract because I promised a deadline before listening.', 8),
        ('The lesson was to define one acceptance example, so that both people can identify a finished result.', 9.5),
        ('Um okay yeah hmm.', 12.5),
    ], 510)
    segments[33] = authored_segment([
        ('Saya pernah takut berbicara di depan penonton padahal latihan sendiri terasa mudah.', 8),
        ('Akhirnya saya mencoba satu cerita kepada teman, karena latihan singkat membuat rasa takut berkurang.', 9.8),
        ('Um okay yeah hmm.', 12.2),
    ], 990)
    result = analyze_transcript(segments, {'duration_preset': '20', 'clip_count': 3})
    assert len(result['candidates']) == 3
    assert any(90 <= c['start'] <= 110 for c in result['candidates'])
    assert any(510 <= c['start'] <= 530 for c in result['candidates'])
    assert any(990 <= c['start'] <= 1010 for c in result['candidates'])
    assert [(c['start'], c['end']) for c in result['coverage']] == [(0, 480), (420, 900), (840, 1080)]
    for candidate in result['candidates']:
        assert 8 <= candidate['duration'] <= 20
        assert candidate['hook_exact_quote'] in ' '.join(e['text'] for e in candidate['source_evidence'])
    for a, left in enumerate(result['candidates']):
        for right in result['candidates'][a + 1:]:
            assert max(0, min(left['end'], right['end']) - max(left['start'], right['start'])) <= .3 * min(left['duration'], right['duration'])


@pytest.mark.parametrize('fps', [24, 25, 30, 60])
def test_real_frame_boundary_evidence_never_exceeds_the_strict_20_second_cap(fps):
    end = math.floor(19.8 * fps) / fps
    source = compact_moment(end)
    result = analyze_transcript([source], {'duration_preset': '20', 'max_duration': 90})
    assert result['candidates']
    assert all(candidate['end'] - candidate['start'] <= 20 for candidate in result['candidates'])
    assert all(candidate['end'] in {w['end'] for w in source['words']} for candidate in result['candidates'])


def test_irregular_vfr_word_timestamps_are_used_directly_not_interpolated():
    source = compact_moment()
    # Controlled irregular capture/recognition times, while preserving actual order.
    for index, word in enumerate(source['words']):
        word['start'] = round(word['start'] + (index % 3) * .001, 6)
        word['end'] = round(word['end'] - ((index + 1) % 3) * .001, 6)
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates']
    assert all(c['start'] in {w['start'] for w in source['words']} and c['end'] in {w['end'] for w in source['words']}
               for c in result['candidates'])


@pytest.mark.parametrize('word_change', [{'start_word': 1}, {'end_word': 15}])
def test_ai_cannot_force_a_mid_sentence_trim_to_meet_the_cap(word_change):
    segments = normalize_segments([compact_moment(17.8)])
    settings = _settings({'duration_preset': '20'}, 17.8)
    with pytest.raises(ValueError, match='sentence boundaries'):
        validate_proposal(proposal(segments, **word_change), segments, settings)


def test_numeric_title_claim_absent_from_clip_is_rejected():
    segments = sentence_units(normalize_segments([compact_moment()]))
    with pytest.raises(ValueError, match='number absent'):
        validate_proposal(proposal(segments, title='Hasil tanaman meningkat 300% dalam 2 hari'), segments,
                          _settings({'duration_preset': '20'}, 17.8))


def test_short_hook_must_begin_within_three_seconds_of_real_word_time():
    segments = normalize_segments([authored_segment([
        ('Here is some context.', 4.1),
        ('Ternyata cara lama salah, karena pekerjaan perlu satu langkah awal yang jelas.', 10.2),
    ])])
    quote = 'Ternyata cara lama salah,'
    with pytest.raises(ValueError, match='first three seconds'):
        validate_proposal(proposal(segments, hook_exact_quote=quote), segments,
                          _settings({'duration_preset': '20'}, 14.3))


def test_ai_receives_refined_units_and_global_review_while_caps_remain_server_validated():
    source = authored_segment([
        ('Ternyata kesalahan bukan kurang waktu karena satu langkah jelas membantu pekerjaan selesai.', 17.8),
        ('Um okay yeah hmm.', 12.2),
    ], 990)
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        payload = json.loads(kwargs['input'])
        if kwargs['text_format'] is SectionReview:
            selected = next((s for s in payload['transcript'] if s['text'].startswith('Ternyata')), None)
            entries = []
            if selected:
                entries = [Proposal.model_validate(proposal([selected], start_segment=selected['index'], end_segment=selected['index'],
                                                           hook_exact_quote=selected['text']))]
            return SimpleNamespace(output_parsed=SectionReview(candidates=entries))
        return SimpleNamespace(output_parsed=GlobalReview(decisions=[GlobalDecision(candidate_id=c['id'], keep=True,
            reason='The complete source explanation delivers its practical payoff within the limit.', warning=None, scores=Scores(**SCORES))
            for c in payload['candidates']]))
    with patch('app.analysis._make_client', return_value=SimpleNamespace(responses=SimpleNamespace(parse=parse))):
        result = analyze_transcript([source], {'duration_preset': '20', 'max_duration': 90}, api_key='test-not-real')
    assert result['mode'] == 'ai' and len(result['candidates']) == 1
    assert result['candidates'][0]['duration'] == pytest.approx(17.8)
    section_payload = json.loads(next(c['input'] for c in calls if c['text_format'] is SectionReview))
    assert section_payload['duration_settings']['max_duration'] == 20
    assert section_payload['duration_settings']['hook_onset_max_sec'] == 3
    assert section_payload['transcript'][0]['_source_segment_index'] == 0
    assert sum(c['text_format'] is SectionReview for c in calls) == 1  # No paid calls for two silent contexts.
    assert any(c['text_format'] is GlobalReview for c in calls)


def test_a_word_fragment_is_not_a_verbatim_spoken_hook():
    segments = normalize_segments([{'start': 0, 'end': 30,
        'text': 'Dissatisfaction can be useful because it reveals which part of a process needs attention. The lesson is to inspect the work before changing everything.'}])
    with pytest.raises(ValueError, match='verbatim'):
        validate_proposal(proposal(segments, hook_exact_quote='satisfaction'), segments,
                          _settings({'duration_preset': '30'}, 30))


def test_dangling_or_cliffhanger_20_second_moments_are_not_padded_into_recommendations():
    source = authored_segment([
        ('Ternyata jawabannya bukan sederhana, karena kita masih perlu membahas banyak hal dan jawabannya adalah.', 17),
    ])
    result = analyze_transcript([source], {'duration_preset': '20'})
    assert result['candidates'] == []
    assert any('20.00' in warning for warning in result['warnings'])


def test_hook_joins_real_recognition_segments_until_opening_sentence_completes():
    segments = [
        {'start': 0, 'end': 3, 'text': 'Actually, I learned'},
        {'start': 3, 'end': 9, 'text': 'that waiting for motivation fails because a clear action makes the work easier.'},
        {'start': 9, 'end': 18, 'text': 'The lesson is to write one sentence first, so that the next step becomes visible.'},
    ]
    result = analyze_transcript(segments, {'duration_preset': '20', 'clip_count':3})
    assert result['candidates']
    assert any(c['hook_exact_quote'].startswith('Actually, I learned that waiting') for c in result['candidates'])
    assert all(c['hook_exact_quote'] != 'Actually, I learned' for c in result['candidates'])


def test_20_second_causal_consequence_survives_split_recognition_without_a_keyword():
    source = [
        authored_segment([('The software checklist was so large that our team', 6)], 27),
        authored_segment([('overlooked the one audio check that mattered for the recording.', 11)], 33),
    ]
    result = analyze_transcript(source, {'duration_preset': '20'})
    [candidate] = result['candidates']
    assert (candidate['start'], candidate['end']) == (27, 44)
    assert candidate['source_evidence'][1]['source_segment_index'] == 1
    assert 'consequence' in candidate['reason']
    assert candidate['review_mode'] == 'heuristic'
    assert result['diagnostics']['selected'] == 1
    assert result['diagnostics']['windows_evaluated'] == 1
    assert result['diagnostics']['continuation_starts'] == 1


def test_20_second_causal_consequence_accepts_indonesian_in_its_original_words():
    text = 'Daftar tugas terlalu panjang sehingga saya melewatkan pemeriksaan audio di awal penyuntingan.'
    result = analyze_transcript([authored_segment([(text, 12)])], {'duration_preset': '20'})
    assert [c['hook_exact_quote'] for c in result['candidates']] == [text]


@pytest.mark.parametrize('text', [
    'It was so detailed that we missed the entire deadline for the first audio check.',
    'The lesson became so clear that I will explain how you can miss the key point next episode.',
    'The software checklist was so long that I missed it for much of the afternoon.',
    'The recording process was so stressful that I will stay tuned for news of the final outcome.',
])
def test_context_reference_teaser_or_vague_consequences_do_not_qualify_as_shortcuts(text):
    result = analyze_transcript([authored_segment([(text, 14)])], {'duration_preset': '20'})
    assert result['candidates'] == []
    assert result['diagnostics']['windows_evaluated'] == 1
    assert sum(result['diagnostics']['rejected'].values()) == 1


def test_diagnostics_distinguish_out_of_cap_starts_from_rejected_editorial_windows():
    segments = [
        authored_segment([('The recording checklist was so detailed that our team missed the first audio test.', 20.001)]),
        authored_segment([('The conference happens every October and the meeting schedules are published online.', 9)], 25),
    ]
    result = analyze_transcript(segments, {'duration_preset': '20'})
    assert not result['candidates']
    assert result['diagnostics'] == {
        'max_duration': 20, 'windows_evaluated': 1, 'rejected': {'no_payoff_evidence': 1},
        'continuation_starts': 0, 'starts_without_complete_window': 1, 'accepted_before_selection': 0, 'selected': 0,
    }


def test_short_clip_rejects_a_pronominal_answer_even_if_it_matches_the_lexical_gate():
    source = [
        authored_segment([('The workshop schedule mattered to the crew.', 6)]),
        authored_segment([('Well, because it actually changed when everyone could arrive at the studio.', 8.8)], 6.6),
    ]
    result = analyze_transcript(source, {'duration_preset': '20'})
    assert [c['start'] for c in result['candidates']] == [0]
    assert result['diagnostics']['rejected']['context_dependent_start'] == 1

    settings = _settings({'duration_preset': '20'}, 16)
    units = sentence_units(normalize_segments(source))
    with pytest.raises(ValueError, match='missing subject'):
        validate_proposal(proposal(units[1:], start_segment=1, end_segment=1,
                                   hook_exact_quote=units[1]['text']), units, settings)
