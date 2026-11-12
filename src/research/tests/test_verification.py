import unittest
from ceres_research.archive import ArchiveError
from ceres_research.verification import without_keys, require_equal, TURN_TIMING_KEYS


class VerificationContract(unittest.TestCase):
    def test_timing_removed_but_cost_and_witness_preserved(self):
        value = {"wall_s": 1.0, "mode": {"cost": 3, "cpu_s": 0.1, "routes": [[0, 1, 2, 0]]}}
        self.assertEqual(without_keys(value, TURN_TIMING_KEYS),
                         {"mode": {"cost": 3, "routes": [[0, 1, 2, 0]]}})

    def test_changed_result_fails(self):
        with self.assertRaises(ArchiveError):
            require_equal({"cost": 3}, {"cost": 2}, "changed cost")
