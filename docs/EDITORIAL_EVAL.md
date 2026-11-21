# Editorial evaluation protocol

PODCUT AI's score is an internal editorial ranking. It is not a prediction of views, reach, or virality. Deterministic tests verify grounding and selection mechanics; a person must assess meaning and usefulness against the source video.

## Ground truth fixture

Use a transcript at least 15 minutes long with deliberately distributed strong and weak moments. Keep the speech verbatim, with real segment/word timestamps where available. Include both Bahasa Indonesia and English, plus a mixed-language passage. Keep one useful moment near the end so a first-section-only implementation fails the review.

The `editorial_fixture()` fixture in `backend/tests/test_analysis.py` spans **18 minutes** as 108 ten-second transcript segments. It places known strong material at **90–130 seconds** (an Indonesian explanation of watering a garden), **510–550 seconds** (an English lesson about freelance scope), and **990–1030 seconds** (an Indonesian personal story about fear of public speaking). The remaining material is deliberately filler. Assertions require three distinct clips spanning all three regions, including the late passage, and check exact quotations, scoring, and duplicate suppression.

Treat these known strengths as test labels, not audience-performance data. This reproducible fixture is not a real episode or a human editorial benchmark. Do not use access to YouTube or a live API key as a prerequisite for the deterministic test suite.

Recommended labels for a human-reviewed fixture:

| Moment | Expected judgment | Reason |
| --- | --- | --- |
| Specific personal story with a result | Strong candidate | Hook, development, and payoff all occur within the interval. |
| Surprising observation with its explanation | Strong candidate | The explanation supports the hook and stands alone. |
| Useful step-by-step advice with a takeaway | Strong candidate | A viewer can use it without the rest of the episode. |
| Contrasting opinions resolved by context | Candidate with careful review | Preserve the qualifying statement so the argument stays accurate. |
| Laughter tied to a clear setup/payoff | Strong candidate if understandable | Humor survives the shorter edit. |
| Greeting, sponsorship, filler, or housekeeping | Weak candidate | Little independent audience value. |
| Cliffhanger with no answer in the interval | Reject | It promises a payoff that the clip does not deliver. |
| A claim detached from its caveat or correction | Reject or extend | The short edit would mislead. |
| An unsupported claim stated confidently | Flag/reject as a factual hook | Transcript grounding proves the words were said, not that the claim is true. |
| A moment late in the episode | Evaluate equally | Its position must not exclude it from discovery. |

## Review each recommendation

Check the video from at least 15 seconds before the in-point to 15 seconds after the out-point. Confirm the exact quote exists, no words are falsely attributed, and the clip does not omit a correction or important qualification. Verify that the in-point starts a complete thought and the out-point includes the payoff. Test the preview and exported clip against the selected interval.

Score each component from 0 to 100 using these anchors:

| Component | Weight | High score | Low score |
| --- | --- | --- | --- |
| Hook Strength | 30% | Immediate, specific reason to keep listening | Generic setup, vague teaser, or missing context |
| Content Payoff | 25% | Clear result, explanation, useful insight, or resolved story | Filler or promise without delivery |
| Standalone Clarity | 20% | A new viewer understands who/what the speaker means | Unresolved pronouns, missing premise, or misleading omission |
| Emotional Impact | 15% | Earned surprise, humor, empathy, or conviction | Manufactured outrage or flat material |
| Edit Readiness | 10% | Natural speech boundaries, manageable duration, clean transitions | Mid-word cuts, abrupt ending, or excessive repair needed |

The server recomputes the weighted score from the five components. A high numerical score must never override a failed grounding/context check.

## Measure selection quality

For each source, label proposed intervals **useful**, **needs adjustment**, or **reject** before viewing their scores. Record a short explanation and the recommended edit. Compute:

- Precision at requested count: useful recommendations divided by recommendations returned.
- Adjustment rate: recommendations needing boundary/context repair divided by recommendations returned.
- Grounding failures: invented quote, invalid timestamp, missing evidence, or evidence outside the chosen clip. Target zero.
- Duplicate rate: repeated coverage of substantially the same moment. Target zero after overlap suppression.
- Episode coverage: inspect late-episode labels and section coverage; equal time distribution is not itself proof of good selection.

Keep AI and heuristic results separate. Re-run the same fixture when changing prompts, chunk overlap, duration settings, or ranking. Record the model name, configuration, transcript version, reviewer, and date. Add real owned/licensed episodes for editorial review before relying on the tool for production publishing.

No human-reviewed precision percentage has been established merely by passing unit tests. Live semantic review also requires a configured OpenAI credential; mocked AI tests establish validation and orchestration behavior only.
