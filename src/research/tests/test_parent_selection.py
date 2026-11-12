"""Synthetic mechanics, separate from the historical nine-slot archive."""
import copy
from fractions import Fraction
import unittest

from ceres_research import parent_selection as replay


def candidate(name, bins=(2, 3), fitness=Fraction(3, 4), length=20):
    return {"id": name, "bins": bins, "fitness": fitness, "source_characters": length,
            "code_sha256": name, "ast_sha256": name, "trace": bins,
            "bf_class": fitness == Fraction(3, 4), "worse_than_bf": fitness < Fraction(3, 4),
            "strictly_more_bins_on_all_inputs": fitness < Fraction(3, 4),
            "relative_extra_bins_mean": Fraction(0) if fitness == Fraction(3, 4) else Fraction(1, 4)}


class ParentMechanics(unittest.TestCase):
    def setUp(self):
        self.pool = [candidate("short", length=10), candidate("long", length=100),
                     candidate("other", (3, 4), Fraction(1, 2))]

    def test_permutation_invariance(self):
        self.assertEqual(replay.distributions(self.pool), replay.distributions(list(reversed(self.pool))))

    def test_identity_deduplication(self):
        duplicate = copy.deepcopy(self.pool[0])
        duplicate["id"] = "another_slot"
        self.assertEqual(len(replay.dedup(self.pool + [duplicate], "code_sha256")), 3)

    def test_exact_fraction_not_float_dedup(self):
        a, b = self.pool[:2]
        a["fitness"], b["fitness"] = Fraction(1), Fraction(10**30 + 1, 10**30)
        self.assertEqual(float(a["fitness"]), float(b["fitness"]))
        self.assertEqual(len(replay.dedup([a, b], "fitness")), 2)

    def test_scalar_collision_preserves_signature_groups(self):
        a, b = self.pool[0], self.pool[2]
        b["fitness"] = a["fitness"]
        result = replay.cluster_distribution([a, b], "softmax", "length", 0.1)
        self.assertEqual(result, {a["id"]: 0.5, b["id"]: 0.5})

    def test_length_bias(self):
        result = replay.cluster_distribution(self.pool, "softmax", "length", 0.1)
        self.assertGreater(result["short"], result["long"])

    def test_duplicate_slots_do_not_change_cluster_mass(self):
        duplicate = copy.deepcopy(self.pool[0])
        duplicate["id"] = "another_slot"
        a = replay.cluster_distribution(self.pool, "softmax", "length", 0.1)
        b = replay.cluster_distribution(self.pool + [duplicate], "softmax", "length", 0.1)
        self.assertAlmostEqual(a["other"], b["other"])

    def test_distribution_matches_exhaustive_enumeration(self):
        lookup = {record["id"]: record for record in self.pool}
        for method in replay.distributions(self.pool)[0]:
            distribution = method["distribution"]
            result = replay.exhaustive_verify(distribution, lookup, replay.summarize(distribution, lookup))
            self.assertTrue(result["passed"])

    def test_positive_temperature_required(self):
        with self.assertRaises(AssertionError):
            replay.softmax([1, 2], 0)


if __name__ == "__main__":
    unittest.main()
