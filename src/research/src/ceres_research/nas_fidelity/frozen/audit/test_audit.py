"""Audit implementation checks; no model training or real-data scoring."""
import math
import unittest
from check_fidelity import ARCHS, aid, independent_macs, ranks, spearman, tau_b


class TestAuditStatistics(unittest.TestCase):
    def test_space(self):
        self.assertEqual(len({aid(a) for a in ARCHS}), 8)
        self.assertTrue(all(independent_macs(a) > 0 for a in ARCHS))

    def test_ties(self):
        self.assertEqual(ranks([3, 1, 3, 2]), [1.5, 4.0, 1.5, 3.0])
        self.assertAlmostEqual(spearman([1, 1, 2], [1, 2, 3]), math.sqrt(3) / 2)
        self.assertAlmostEqual(tau_b([1, 1, 2], [1, 2, 3]), math.sqrt(2 / 3))

    def test_endpoints(self):
        for fn in (spearman, tau_b):
            self.assertEqual(fn([1, 2, 3], [1, 2, 3]), 1)
            self.assertEqual(fn([1, 2, 3], [3, 2, 1]), -1)
            self.assertIsNone(fn([1, 1, 1], [1, 2, 3]))


if __name__ == "__main__":
    unittest.main()
