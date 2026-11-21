"""Deterministic editorial mechanics; live semantic quality needs human review."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.analysis import (
    AnalysisCancelled,
    GlobalDecision,
    GlobalReview,
    Proposal,
    Scores,
    SectionReview,
    analyze_transcript,
    deduplicate_candidates,
    normalize_segments,
    transcript_contexts,
    validate_proposal,
    weighted_score,
)


SETTINGS = {"clip_count": 3, "min_duration": 20, "max_duration": 60, "target_duration": 40}
BASE_SCORES = {"hook_strength": 80, "content_payoff": 70, "standalone_clarity": 60,
               "emotional_impact": 50, "edit_readiness": 90}


def editorial_fixture():
    """18 minutes, three authored moments and explicitly weak filler elsewhere.

    This is synthetic evidence for indexing/coverage, not a real podcast or a
    claim that an LLM agrees with its intended editorial labels.
    """
    filler = "Um, okay, yeah, hmm. Um, okay, yeah, hmm. Um, okay, yeah, hmm."
    segments = [{"start": float(t), "end": float(t + 10), "text": filler}
                for t in range(0, 1080, 10)]
    moments = {
        90: [
            "Ternyata menyiram kebun setiap hari justru membuat akar dangkal dan tanaman cepat layu ketika musim kemarau panjang datang.",
            "Karena air hanya membasahi permukaan tanah, akar tidak tumbuh turun mencari cadangan kelembapan yang lebih stabil di bawah.",
            "Jadi saya mengukur kelembapan dengan jari dan menyiram lebih jarang tetapi cukup dalam sampai lapisan akar benar benar basah.",
            "Intinya perhatikan tanah sebelum mengambil selang; jadwal tetap tidak bisa menggantikan pengamatan terhadap kebutuhan tanaman di kebun rumah.",
        ],
        510: [
            "I remember losing my first freelance contract because I promised a deadline before asking what the client actually needed.",
            "We tried adding more late nights, but the extra hours only created revisions because nobody had agreed on the deliverable.",
            "The lesson was to write one acceptance example before estimating a project, so that both people can identify a finished result.",
            "Now a small paid trial replaces vague promises, and the result is fewer surprises for the designer and the customer.",
        ],
        990: [
            "Saya pernah takut berbicara di panggung, padahal latihan pidato sendirian di kamar terasa sangat lancar dan mudah bagi saya.",
            "Karena tubuh saya menganggap penonton sebagai ancaman, saya belajar menyapa satu orang sebelum mulai dan menurunkan kecepatan bernapas.",
            "Akhirnya saya bangga bisa menyampaikan cerita pertama tanpa membaca seluruh catatan; rasa takut tetap ada tetapi tidak mengambil kendali.",
            "Pelajarannya bukan menunggu berani, melainkan mencoba presentasi singkat kepada teman yang mendukung lalu meningkatkan tantangan secara bertahap setiap minggu.",
        ],
    }
    for start, texts in moments.items():
        for offset, text in enumerate(texts):
            segments[start // 10 + offset]["text"] = text
    return segments


def proposal(start=0, end=3, quote=None, **overrides):
    value = {
        "start_segment": start, "end_segment": end, "start_word": None, "end_word": None,
        "title": "A useful authored moment", "hook_exact_quote": quote or "Ternyata penyebabnya bukan kurang waktu.",
        "category": "Useful explanation", "reason": "The explanation develops a clear contrast and ends with a concrete action.",
        "warning": None, "has_payoff": True, "self_contained": True, "supported_by_source": True,
        "scores": dict(BASE_SCORES),
    }
    return {**value, **overrides}


def small_transcript():
    return [
        {"start": 0, "end": 10, "text": "Ternyata penyebabnya bukan kurang waktu."},
        {"start": 10, "end": 20, "text": "Karena tugas besar tidak mempunyai langkah pertama yang jelas, kita terus menunda untuk memulainya."},
        {"start": 20, "end": 30, "text": "Jadi pecah pekerjaan menjadi satu tindakan kecil yang bisa dimulai sebelum kopi selesai diseduh."},
        {"start": 30, "end": 40, "text": "Intinya tuliskan satu kalimat pertama sekarang dan periksa hasilnya sebelum memperbesar target pekerjaan."},
    ]


def test_exact_documented_score_weights():
    assert weighted_score(BASE_SCORES) == 70.0
    assert weighted_score({key: 100 for key in BASE_SCORES}) == 100
    for key, weight in {"hook_strength": .30, "content_payoff": .25,
                        "standalone_clarity": .20, "emotional_impact": .15, "edit_readiness": .10}.items():
        assert weighted_score({name: 100 if name == key else 0 for name in BASE_SCORES}) == weight * 100


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101, True])
def test_invalid_component_scores_rejected(value):
    with pytest.raises(ValueError):
        weighted_score({**BASE_SCORES, "hook_strength": value})


def test_contexts_cover_entire_18_minutes_with_overlap_and_tail():
    segments = editorial_fixture()
    contexts = transcript_contexts(segments)
    assert [(c["start"], c["end"]) for c in contexts] == [(0, 480), (420, 900), (840, 1080)]
    assert set().union(*(set(c["indices"]) for c in contexts)) == set(range(len(segments)))
    assert all(contexts[i]["end"] > contexts[i + 1]["start"] for i in range(len(contexts) - 1))


def test_heuristic_fallback_has_real_evidence_and_late_episode_results():
    result = analyze_transcript(editorial_fixture(), SETTINGS)
    assert result["mode"] == "heuristic"
    assert len(result["candidates"]) == 3
    assert any(c["start"] >= 980 for c in result["candidates"])
    assert any(490 <= c["start"] <= 550 for c in result["candidates"])
    assert any(80 <= c["start"] <= 130 for c in result["candidates"])
    for candidate in result["candidates"]:
        evidence_text = " ".join(e["text"] for e in candidate["source_evidence"])
        assert candidate["hook_exact_quote"] in evidence_text
        assert 20 <= candidate["duration"] <= 60
        assert candidate["review_mode"] == "heuristic"
        assert candidate["score"] == weighted_score(candidate["scores"])
        assert "Heuristic fallback" in candidate["warning"]
    assert any("No OpenAI credentials" in warning for warning in result["warnings"])


def test_pure_filler_returns_no_fake_cards():
    result = analyze_transcript([{"start": t, "end": t + 10, "text": "Um, okay, yeah, hmm, iya, ya."} for t in range(0, 120, 10)], SETTINGS)
    assert result["candidates"] == []
    assert any("No filler results" in warning for warning in result["warnings"])


def test_none_manual_settings_match_ordinary_analysis():
    result = analyze_transcript(editorial_fixture(), {**SETTINGS, "manual_start": None, "manual_end": None})
    assert len(result["candidates"]) == 3


def test_manual_timestamp_assist_never_leaves_requested_range():
    result = analyze_transcript(editorial_fixture(), {**SETTINGS, "manual_start": 980, "manual_end": 1040})
    assert result["candidates"]
    assert all(980 <= c["start"] < c["end"] <= 1040 for c in result["candidates"])
    assert result["coverage"] == [{"start": 980.0, "end": 1040.0, "segment_count": 6}]


@pytest.mark.parametrize("changes", [
    {"clip_count": 2}, {"clip_count": 11}, {"clip_count": True},
    {"min_duration": 19}, {"target_duration": 91}, {"max_duration": 91},
    {"min_duration": 50, "target_duration": 40},
    {"manual_start": 5}, {"manual_start": -1, "manual_end": 50},
    {"manual_start": 20, "manual_end": 30}, {"manual_start": 0, "manual_end": float("inf")},
])
def test_invalid_settings_rejected(changes):
    with pytest.raises(ValueError):
        analyze_transcript(small_transcript(), {**SETTINGS, **changes})


def test_fabricated_quote_is_rejected_not_repaired():
    with pytest.raises(ValueError, match="verbatim"):
        validate_proposal(proposal(quote="This sentence was never spoken by anybody."), small_transcript(), SETTINGS)


def test_source_punctuation_and_language_are_not_rewritten():
    segments = small_transcript()
    candidate = validate_proposal(proposal(), segments, SETTINGS)
    assert candidate["hook_exact_quote"] == segments[0]["text"]
    with pytest.raises(ValueError, match="verbatim"):
        validate_proposal(proposal(quote="Ternyata penyebabnya bukan kurang waktu!"), segments, SETTINGS)


@pytest.mark.parametrize("changes", [
    {"start_segment": -1}, {"end_segment": 999}, {"has_payoff": False},
    {"self_contained": False}, {"supported_by_source": False}, {"start_word": 4},
    {"start_segment": 3, "end_segment": 1},
])
def test_nonexistent_anchor_or_incomplete_clip_rejected(changes):
    with pytest.raises(ValueError):
        validate_proposal(proposal(**changes), small_transcript(), SETTINGS)


def test_context_indices_limit_ai_proposals():
    with pytest.raises(ValueError, match="context"):
        validate_proposal(proposal(), small_transcript(), SETTINGS, allowed_indices={0, 1})


def test_segment_timestamps_cannot_be_invented_by_model():
    candidate = validate_proposal(proposal(), small_transcript(), SETTINGS)
    assert candidate["start"] == 0
    assert candidate["end"] == 40
    assert candidate["duration"] == 40
    assert candidate["source_evidence"] == small_transcript()


def test_real_word_anchors_are_used_without_timestamp_interpolation():
    words = [
        {"start": 0, "end": 4, "word": "Before."},
        {"start": 5, "end": 9, "word": "Actual"},
        {"start": 10, "end": 14, "word": "hook."},
        {"start": 15, "end": 19, "word": "Because"},
        {"start": 20, "end": 24, "word": "details"},
        {"start": 25, "end": 29, "word": "matter."},
        {"start": 30, "end": 34, "word": "After."},
    ]
    segments = normalize_segments([{"start": 0, "end": 35, "text": "Before. Actual hook. Because details matter. After.", "words": words}])
    candidate = validate_proposal(proposal(start=0, end=0, quote="Actual hook.", start_word=1, end_word=5), segments, SETTINGS)
    assert (candidate["start"], candidate["end"], candidate["duration"]) == (5, 29, 24)
    assert candidate["source_evidence"] == [{"start": 5, "end": 29, "text": "Actual hook. Because details matter."}]


def test_invalid_word_timestamps_rejected_instead_of_repaired():
    with pytest.raises(ValueError, match="inside"):
        normalize_segments([{"start": 0, "end": 20, "text": "A transcript.", "words": [{"start": 10, "end": 25, "word": "transcript"}]}])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_transcript_times_rejected(value):
    with pytest.raises(ValueError):
        normalize_segments([{"start": value, "end": 40, "text": "The transcript."}])


def test_overlap_dedup_keeps_higher_quality_and_removes_repeat_hook():
    candidate = validate_proposal(proposal(), small_transcript(), SETTINGS)
    lower = {**candidate, "id": "lower", "score": 10, "start": 10, "end": 50}
    repeated = {**candidate, "id": "repeat", "start": 100, "end": 140}
    assert deduplicate_candidates([lower, repeated, candidate]) == [candidate]


class FakeResponses:
    def __init__(self, transcript):
        self.transcript = transcript
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["input"])
        if kwargs["text_format"] is SectionReview:
            candidates = []
            for start in (90, 510, 990):
                index = start // 10
                supplied = {entry["index"] for entry in payload["transcript"]}
                if index in supplied and index + 3 in supplied:
                    candidates.append(Proposal.model_validate(proposal(start=index, end=index + 3, quote=self.transcript[index]["text"])))
            return SimpleNamespace(output_parsed=SectionReview(candidates=candidates))
        return SimpleNamespace(output_parsed=GlobalReview(decisions=[
            GlobalDecision(candidate_id=c["id"], keep=True,
                           reason="The full surrounding conversation supports a complete example with a concrete lesson.",
                           warning=None, scores=Scores(**BASE_SCORES))
            for c in payload["candidates"]
        ]))


def test_ai_runs_full_section_pass_then_global_review_and_validates_quotes():
    segments = editorial_fixture()
    fake = FakeResponses(segments)
    progress = []
    with patch("app.analysis._make_client", return_value=SimpleNamespace(responses=fake)):
        result = analyze_transcript(segments, SETTINGS, api_key="test-not-real", progress=lambda fraction, stage: progress.append((fraction, stage)))
    assert result["mode"] == "ai"
    assert len(result["candidates"]) == 3
    assert sum(call["text_format"] is SectionReview for call in fake.calls) == 3
    assert sum(call["text_format"] is GlobalReview for call in fake.calls) == 1
    assert all(call["store"] is False for call in fake.calls)
    assert all("untrusted" in call["instructions"] for call in fake.calls)
    assert any(c["start"] == 990 for c in result["candidates"])
    assert all(c["review_mode"] == "ai" and c["score"] == 70 for c in result["candidates"])
    global_payload = json.loads(fake.calls[-1]["input"])
    assert global_payload["candidates"][0]["surrounding_context"]
    assert progress[-1][0] == 1


def test_ai_failure_is_visibly_heuristic_and_does_not_leak_exception():
    with patch("app.analysis._make_client", side_effect=RuntimeError("secret-test-api-key")):
        result = analyze_transcript(editorial_fixture(), SETTINGS, api_key="test-not-real")
    assert result["mode"] == "heuristic"
    assert all(c["review_mode"] == "heuristic" for c in result["candidates"])
    assert any("unavailable" in warning for warning in result["warnings"])
    assert "secret-test-api-key" not in json.dumps(result)


def test_ai_fabrications_are_rejected_without_publishing_false_quotes():
    class Fabricator:
        def parse(self, **kwargs):
            assert kwargs["text_format"] is SectionReview
            return SimpleNamespace(output_parsed=SectionReview(candidates=[Proposal.model_validate(proposal(quote="This was fabricated by the language model."))]))
    with patch("app.analysis._make_client", return_value=SimpleNamespace(responses=Fabricator())):
        result = analyze_transcript(small_transcript(), SETTINGS, api_key="test-not-real")
    assert result["mode"] == "ai"
    assert result["candidates"] == []
    assert any("Rejected 1 AI proposal" in warning for warning in result["warnings"])


def test_cancel_never_turns_into_fallback_or_publishes_results():
    with pytest.raises(AnalysisCancelled):
        analyze_transcript(editorial_fixture(), SETTINGS, cancelled=lambda: True)


def test_heuristic_preferred_duration_changes_without_transcript_mutation():
    segments = editorial_fixture()
    before = json.dumps(segments, ensure_ascii=False)
    result = analyze_transcript(segments, {**SETTINGS, "target_duration": 30})
    assert result["candidates"]
    assert any(c["duration"] == 30 for c in result["candidates"])
    assert json.dumps(segments, ensure_ascii=False) == before
