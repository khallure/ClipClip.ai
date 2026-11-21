"""Optional-runtime checks. Run only with .venv-asd, not the core V1 interpreter."""
import hashlib
from pathlib import Path
import unittest

import numpy as np
import torch

from .runner import LightASD, KNOWN_WEIGHTS, _crop, dense_track


class TrackSafetyTests(unittest.TestCase):
    def test_missing_face_is_not_invented_across_long_gap(self):
        rows = [(0., [.1, .1, .2, .3], .95), (.32, [.2, .1, .2, .3], .95), (1., [.3, .1, .2, .3], .95)]
        boxes, valid = dense_track(rows, np.array([-.24, -.08, 0., .16, .4, .6, .8, 1., 1.12, 1.24]))
        self.assertEqual(valid.tolist(), [False, True, True, True, False, False, False, True, True, False])
        self.assertAlmostEqual(float(boxes[3, 0]), .15, places=5)

    def test_low_face_detection_confidence_stays_invalid(self):
        _, valid = dense_track([(0., [.1, .1, .2, .2], .4)], np.array([0., .04]))
        self.assertFalse(valid.any())

    def test_offscreen_crop_has_padding_and_finite_shape(self):
        frame = np.full((360, 640), 220, dtype=np.uint8)
        crop = _crop(frame, np.array([5., 5., 50.]))
        self.assertEqual(crop.shape, (112, 112))
        self.assertTrue((crop == 110).any())
        self.assertTrue((crop == 220).any())


class AuditedModelTests(unittest.TestCase):
    def test_official_checkpoint_load_and_aligned_audio_visual_logits(self):
        torch.set_num_threads(2)
        path = Path(__file__).resolve().parents[2] / "assets/active_speaker/light-asd-talkset.model"
        self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), KNOWN_WEIGHTS)
        model = LightASD().eval()
        model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True), strict=True)
        with torch.inference_mode():
            features, _ = model.model(torch.zeros(1, 100, 13), torch.full((1, 25, 112, 112), 110.))
            logits = model.lossAV.FC(features)
        self.assertEqual(tuple(logits.shape), (25, 2))
        self.assertTrue(torch.isfinite(logits).all())


if __name__ == "__main__":
    unittest.main()
