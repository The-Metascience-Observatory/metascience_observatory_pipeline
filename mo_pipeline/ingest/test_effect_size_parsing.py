"""
Tests for parse_test_statistic() in data_ingestor.py — parsing APA-formatted
test statistics into Pearson's r.

Run:  python3 -m unittest mo_pipeline.ingest.test_effect_size_parsing -v
"""

import math
import unittest

from mo_pipeline.ingest.data_ingestor import parse_test_statistic as p


class TestPreviouslyParsing(unittest.TestCase):
    """Canonical shapes that parsed before the parser was widened must still
    parse to the same values (no regression)."""

    def test_t(self):
        self.assertAlmostEqual(p("t(10) = 2.5"), 2.5 / math.sqrt(2.5 ** 2 + 10))

    def test_f_df1_is_one(self):
        # F(1, df2) routes through t = sqrt(F)
        self.assertAlmostEqual(p("F(1, 20) = 4.5"),
                               math.sqrt(4.5) / math.sqrt(4.5 + 20))

    def test_z_with_n(self):
        self.assertAlmostEqual(p("z = 2.81, N = 34"), 2.81 / math.sqrt(2.81 ** 2 + 34))

    def test_chi2_with_n_prefix(self):
        self.assertAlmostEqual(p("χ2(1, N = 12) = 5"), math.sqrt(5 / 12))

    def test_negative_t_sign_preserved(self):
        self.assertLess(p("t(64) = -.88"), 0)


class TestNewlyRecovered(unittest.TestCase):
    """Formatting variants that previously failed and should now parse."""

    def test_leading_dot_decimal_t(self):
        self.assertAlmostEqual(p("t(84) = .27"), 0.27 / math.sqrt(0.27 ** 2 + 84))

    def test_leading_dot_decimal_f(self):
        self.assertAlmostEqual(p("F(1, 94) = .13"),
                               math.sqrt(0.13) / math.sqrt(0.13 + 94))

    def test_float_df_t(self):
        # Welch / mixed-model fractional df
        self.assertAlmostEqual(p("t(173.36) = 5.37"),
                               5.37 / math.sqrt(5.37 ** 2 + 173.36))

    def test_float_df_f(self):
        self.assertAlmostEqual(p("F(1, 62.9) = 8.5"),
                               math.sqrt(8.5) / math.sqrt(8.5 + 62.9))

    def test_chi2_without_n_prefix(self):
        self.assertAlmostEqual(p("X2(1, 85) = 7.873"), math.sqrt(7.873 / 85))

    def test_z_with_trailing_punctuation(self):
        self.assertAlmostEqual(p("Z = -8.31, N = 1137 ,"),
                               -8.31 / math.sqrt(8.31 ** 2 + 1137))


class TestNotConvertible(unittest.TestCase):
    """Cases that must remain None: multi-df omnibus stats, inequalities,
    unsupported statistics, and malformed input."""

    def test_multi_df_f(self):
        self.assertIsNone(p("F(2, 154) = 12.47"))
        self.assertIsNone(p("F(3, 57) = 3.818"))

    def test_multi_df_chi2(self):
        self.assertIsNone(p("X2(2, N = 64) = 11.28"))

    def test_inequalities(self):
        self.assertIsNone(p("F < 1"))
        self.assertIsNone(p("t(97) < 1"))
        self.assertIsNone(p("W > 152"))

    def test_unsupported_statistics(self):
        for s in ("gamma = 0.075", "pi = 0.686", "U = 717 (48, 15), z = 5.761",
                  "binomial z = 3.75", "W = 360", "1.56"):
            self.assertIsNone(p(s), f"expected None for {s!r}")

    def test_non_string(self):
        self.assertIsNone(p(None))
        self.assertIsNone(p(2.5))

    def test_degenerate_df_or_n(self):
        self.assertIsNone(p("t(0) = 2.5"))
        self.assertIsNone(p("z = 2.0, N = 0"))


class TestRangeAndSign(unittest.TestCase):
    """Recovered values must be valid correlations in [-1, 1]."""

    def test_recovered_values_in_range(self):
        for s in ("t(84) = .27", "F(1, 94) = .13", "t(173.36) = 5.37",
                  "X2(1, 85) = 7.873", "Z = -8.31, N = 1137 ,"):
            r = p(s)
            self.assertIsNotNone(r, f"{s!r} should parse")
            self.assertLessEqual(abs(r), 1.0, f"{s!r} -> {r} out of range")


if __name__ == "__main__":
    unittest.main(verbosity=2)
