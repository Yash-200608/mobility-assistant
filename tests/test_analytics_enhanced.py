import unittest

from analytics_enhanced import (
    EnergyExpenditure,
    FallDirectionPredictor,
    GaitFingerprint,
    PhaseDetector,
)

class TestGaitFingerprint(unittest.TestCase):
    def test_deviation_increases_with_shift(self):
        g = GaitFingerprint(window=20)
        for _ in range(15):
            g.update(90, 60)
        g.update(50, 60)
        self.assertGreater(g.deviation_score(), 0.5)

class TestEnergy(unittest.TestCase):
    def test_resting_met(self):
        self.assertEqual(EnergyExpenditure.estimate_met(0, True), 1.0)

    def test_walk_met(self):
        self.assertGreater(EnergyExpenditure.estimate_met(80, True), 2.0)

class TestFallDirection(unittest.TestCase):
    def test_pitch_forward(self):
        self.assertIn("forward", FallDirectionPredictor.predict(40, 10))

class TestPhase(unittest.TestCase):
    def test_asymmetry(self):
        p = PhaseDetector()
        for _ in range(3):
            p.ingest_events(["L_FOOT"])
        for _ in range(9):
            p.ingest_events(["R_FOOT"])
        self.assertGreater(p.asymmetry_ratio(), 0.3)

if __name__ == "__main__":
    unittest.main()
