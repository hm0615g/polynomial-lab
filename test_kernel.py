"""Behavioral checks for exact arithmetic, complete roots, and safe input."""

import importlib.util
import json
import random
import unittest
from pathlib import Path

import sympy as sp


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kernel", ROOT / "web" / "kernel.py")
kernel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel)
x = kernel.X


class KernelTests(unittest.TestCase):
    def calculation(self, polynomial, **options):
        result = kernel.calculate({"polynomial": polynomial, **options})
        self.assertTrue(result["ok"], result)
        json.dumps(result, allow_nan=False)
        return result

    def assert_error(self, polynomial, code=None, **options):
        result = kernel.calculate({"polynomial": polynomial, **options})
        self.assertFalse(result["ok"], result)
        if code:
            self.assertEqual(result["error"]["code"], code)
        return result

    def assert_finite_roots(self, result, p):
        """Verify the returned coordinates using SymPy's independent remainder."""
        polynomial = sp.Poly(kernel.parse_polynomial(result["normalized"]["text"]).as_expr(), x, modulus=p)
        extensions = {entry["id"]: entry for entry in result["extensions"]}
        self.assertEqual(sum(root["multiplicity"] for root in result["roots"]), polynomial.degree())
        self.assertTrue(result["summary"]["all_roots_found"])
        seen = set()
        for root in result["roots"]:
            key = (root["extension_id"], root["text"])
            self.assertNotIn(key, seen)
            seen.add(key)
            if root["extension_id"] is None:
                self.assertEqual(int(polynomial.eval(int(root["text"]))) % p, 0)
            else:
                extension = extensions[root["extension_id"]]
                modulus = sp.Poly(kernel.parse_polynomial(extension["modulus_text"]).as_expr(), x, modulus=p)
                coordinate = sp.Poly(kernel.parse_polynomial(root["text"].replace(extension["symbol"], "x")).as_expr(), x, modulus=p)
                self.assertLess(coordinate.degree(), modulus.degree())
                self.assertTrue(polynomial.compose(coordinate).rem(modulus).is_zero)

    def test_exact_decimal_fraction_and_implicit_multiplication(self):
        self.assertEqual(kernel.parse_polynomial("0.1x + .20x + 1/3 - 1/30").as_expr(), sp.Rational(3, 10) * x + sp.Rational(3, 10))
        self.assertEqual(kernel.parse_polynomial("2(x+1)(x-1) + x(x+2)").as_expr(), 3*x*x + 2*x - 2)
        self.assertEqual(kernel.parse_polynomial("x/(2+2)").as_expr(), x/4)

    def test_operator_precedence(self):
        cases = {"-x^2": -x**2, "(-x)^2": x**2, "x^2^3": x**8,
                 "2^-2*x": x/4, "--x": x, "-2^2": -4, "(-2)^2": 4,
                 "x/-2": -x/2, "0^0": 1}
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(kernel.parse_polynomial(source).as_expr(), expected)

    def test_deep_parentheses_do_not_depend_on_python_recursion(self):
        source = "(" * 1400 + "x" + ")" * 1400
        self.assertEqual(kernel.parse_polynomial(source).as_expr(), x)

    def test_parser_rejects_code_and_nonpolynomial_input(self):
        sources = ["__import__('os').system('echo unsafe')", "x.__class__", "sin(x)",
                   "[x]", "x;1", "x=1", "x**2", "2 3", "(x)2", "()", "(x+)",
                   "x+", "(x", "x)", "1..2", "", "   "]
        for source in sources:
            with self.subTest(source=source):
                self.assert_error(source)
        self.assert_error("1/x", "nonconstant_denominator")
        self.assert_error("x^-1", "negative_exponent")
        self.assert_error("x^(1/2)", "invalid_exponent")
        self.assert_error("x^x", "invalid_exponent")
        self.assert_error("x/(1-1)", "division_by_zero")
        self.assert_error("0^-1", "division_by_zero")

    def test_resource_limits_and_huge_exponents(self):
        self.assert_error("x" * 3001, "input_limit")
        self.assertEqual(kernel.parse_polynomial("x^50").degree(), 50)
        self.assert_error("x^51", "degree_limit")
        self.assert_error("x^50*x", "degree_limit")
        self.assert_error("x^(10^99)", "degree_limit")
        self.assert_error("2^(10^99)", "coefficient_limit")
        self.assert_error("(" + "9" * 100 + ")^300", "coefficient_limit")
        self.assert_error("9" * 101, "coefficient_limit")
        self.assert_error("1/" + "9" * 101, "coefficient_limit")
        self.assert_error("0." + "0" * 99 + "1", "coefficient_limit")
        self.assertEqual(kernel.parse_polynomial("9" * 100).degree(), 0)
        self.assertEqual(kernel.parse_polynomial("1^(10^99)").as_expr(), 1)

    def test_eisenstein_example_has_four_exact_roots(self):
        result = self.calculation("x^4-4x^2+2")
        self.assertEqual(result["irreducibility"]["status"], "irreducible")
        self.assertEqual(result["eisenstein_prime"], 2)
        self.assertEqual(result["summary"]["total_multiplicity"], 4)
        self.assertTrue(result["summary"]["all_roots_found"])
        for root in result["roots"]:
            # Parsing only trusted, engine-generated output for an independent check.
            exact = sp.sympify(root["text"].replace("^", "**"))
            self.assertEqual(sp.simplify(exact**4 - 4*exact**2 + 2), 0)
            self.assertIsNotNone(root["approx_text"])

    def test_general_quintic_returns_complete_rootof_representations(self):
        result = self.calculation("x^5-x+1", digits=25)
        self.assertEqual(result["summary"]["distinct"], 5)
        self.assertEqual(result["eisenstein_prime"], None)
        self.assertTrue(all("CRootOf" in root["text"] for root in result["roots"]))
        for root in result["roots"]:
            approximation = sp.sympify(root["approx_text"])
            self.assertLess(abs(complex((approximation**5 - approximation + 1).evalf())), 1e-20)

    def test_rational_and_real_scopes_do_not_change_irreducibility(self):
        source = "(x-1)^2*(x^2-2)*(x^2+1)"
        all_roots = self.calculation(source)
        real_roots = self.calculation(source, root_scope="real")
        rational_roots = self.calculation(source, root_scope="base")
        self.assertEqual(all_roots["summary"]["total_multiplicity"], 6)
        self.assertEqual(real_roots["summary"]["total_multiplicity"], 4)
        self.assertEqual(rational_roots["summary"]["total_multiplicity"], 2)
        self.assertEqual([r["text"] for r in rational_roots["roots"]], ["1"])
        self.assertFalse(real_roots["summary"]["all_roots_found"])
        self.assertFalse(rational_roots["summary"]["all_roots_found"])
        self.assertEqual(all_roots["irreducibility"], real_roots["irreducibility"])
        self.assertEqual(all_roots["irreducibility"], rational_roots["irreducibility"])

    def test_no_rational_roots_does_not_imply_irreducibility(self):
        result = self.calculation("x^4+3x^2+2", root_scope="base")
        self.assertEqual(result["roots"], [])
        self.assertEqual(result["irreducibility"]["status"], "reducible")
        self.assertEqual(len(result["factorization"]["factors"]), 2)
        self.assertIsNone(result["eisenstein_prime"])

    def test_repeated_rational_roots_and_scalar_are_preserved(self):
        source = "-3/7*(x-1)^3*(x+2)^2"
        result = self.calculation(source)
        self.assertEqual({root["text"]: root["multiplicity"] for root in result["roots"]}, {"1": 3, "-2": 2})
        self.assertEqual(kernel.parse_polynomial(result["factorization"]["text"]), kernel.parse_polynomial(source))
        self.assertEqual(result["summary"]["total_multiplicity"], 5)

    def test_requested_precision_and_complex_roots(self):
        result = self.calculation("x^2-2", digits=50)
        for root in result["roots"]:
            approximation = sp.Float(root["approx_text"], 50)
            self.assertLess(abs(approximation**2 - 2), sp.Float("1e-48"))
        complex_result = self.calculation("x^2+1")
        self.assertEqual({root["text"] for root in complex_result["roots"]}, {"I", "-I"})
        self.assertEqual(self.calculation("x^2+1", root_scope="real")["roots"], [])

    def test_frobenius_conjugates_are_actual_reduced_coordinates(self):
        result = self.calculation("x^3+x+1", field="Fp", p="2")
        self.assertEqual([root["text"] for root in result["roots"]], ["a1", "a1^2", "a1^2 + a1"])
        self.assertEqual(result["extensions"][0]["degree"], 3)
        self.assert_finite_roots(result, 2)

    def test_characteristic_p_zero_derivative_and_multiplicity(self):
        cases = [("x^4-4x^2+2", 2, {4}), ("(x^2+x+1)^2", 2, {2}),
                 ("(x^2+1)^3", 3, {3})]
        for source, p, multiplicities in cases:
            with self.subTest(source=source, p=p):
                result = self.calculation(source, field="Fp", p=p)
                self.assertEqual({root["multiplicity"] for root in result["roots"]}, multiplicities)
                self.assertEqual(result["irreducibility"]["status"], "reducible")
                self.assert_finite_roots(result, p)

    def test_mixed_multiplicities_in_positive_characteristic(self):
        result = self.calculation("(x+1)^3*(x^2+x+1)^2", field="Fp", p=2)
        self.assertEqual(sorted(root["multiplicity"] for root in result["roots"]), [2, 2, 3])
        self.assert_finite_roots(result, 2)

    def test_different_irreducible_factors_get_separate_extension_fields(self):
        result = self.calculation("(x^2+1)*(x^2+x+2)", field="Fp", p=3)
        self.assertEqual(len(result["extensions"]), 2)
        self.assertEqual({root["extension_id"] for root in result["roots"]}, {"K1", "K2"})
        self.assert_finite_roots(result, 3)

    def test_finite_base_scope_excludes_extension_roots(self):
        result = self.calculation("(x+1)*(x^2+x+1)", field="Fp", p=2, root_scope="base")
        self.assertEqual(result["extensions"], [])
        self.assertEqual([root["text"] for root in result["roots"]], ["1"])
        self.assertEqual(result["summary"]["total_multiplicity"], 1)
        self.assertFalse(result["summary"]["all_roots_found"])

    def test_finite_rational_coefficients_use_reduced_denominators(self):
        valid = self.calculation("2/6*x+1", field="Fp", p=2)
        self.assertEqual(valid["normalized"]["text"], "x + 1")
        self.assertEqual(valid["roots"][0]["text"], "1")
        valid_five = self.calculation("x/2+1", field="Fp", p=5)
        self.assertEqual(valid_five["roots"][0]["text"], "3")
        self.assert_error("x/2+1", "denominator_not_invertible", field="Fp", p=2)

    def test_degree_drop_and_reduction_to_zero_or_unit(self):
        degree_drop = self.calculation("2x^2+x+1", field="Fp", p=2)
        self.assertEqual(degree_drop["normalized"]["degree"], 1)
        self.assertTrue(degree_drop["warnings"])
        zero = self.calculation("2x+4", field="Fp", p=2)
        self.assertEqual(zero["irreducibility"]["status"], "zero")
        self.assertIsNone(zero["summary"]["degree"])
        unit = self.calculation("2x+1", field="Fp", p=2)
        self.assertEqual(unit["irreducibility"]["status"], "unit")
        self.assertEqual(unit["summary"]["total_multiplicity"], 0)

    def test_zero_polynomial_has_special_semantics(self):
        options = [{}, {"root_scope": "real"}, {"root_scope": "base"},
                   {"field": "Fp", "p": 3}, {"field": "Fp", "p": 3, "root_scope": "base"}]
        for option in options:
            with self.subTest(option=option):
                result = self.calculation("x-x", **option)
                self.assertEqual(result["roots"], [])
                self.assertEqual(result["irreducibility"]["status"], "zero")
                self.assertIsNone(result["summary"]["distinct"])
                self.assertIsNone(result["summary"]["total_multiplicity"])
                self.assertTrue(result["summary"]["special"])
        self.assertIn("全3個", self.calculation("0", field="Fp", p=3, root_scope="base")["summary"]["special"])

    def test_units_and_linear_polynomials(self):
        for source in ("1", "-7/3"):
            result = self.calculation(source)
            self.assertEqual(result["irreducibility"]["status"], "unit")
            self.assertEqual(result["summary"]["degree"], 0)
            self.assertEqual(result["roots"], [])
        self.assertEqual(self.calculation("2x+1")["irreducibility"]["status"], "irreducible")

    def test_option_validation(self):
        for p in (1, 4, 9, "2147483648", 2.0, True, "9" * 100):
            with self.subTest(p=p):
                result = self.assert_error("x+1", field="Fp", p=p)
                self.assertEqual(result["error"]["field"], "p")
        for digits in (4, 51, 15.5, True, "NaN"):
            with self.subTest(digits=digits):
                result = self.assert_error("x+1", digits=digits)
                self.assertEqual(result["error"]["field"], "digits")
        self.assert_error("x+1", "invalid_root_scope", field="Fp", root_scope="real")
        self.assert_error("x+1", "invalid_root_scope", root_scope="unknown")
        self.assert_error("x+1", "invalid_field", field="R")
        self.assertFalse(kernel.calculate(None)["ok"])
        self.assert_error(None, "invalid_input")

    def test_largest_supported_prime(self):
        result = self.calculation("x+1", field="Fp", p=2147483647)
        self.assertEqual(result["roots"][0]["text"], "2147483646")

    def test_json_boundary(self):
        result = json.loads(kernel.calculate_json('{"polynomial":"x^2-2"}'))
        self.assertTrue(result["ok"])
        self.assertEqual(result["irreducibility"]["label"], "既約")
        self.assertEqual(json.loads(kernel.calculate_json("not json"))["error"]["code"], "invalid_json")
        self.assertFalse(json.loads(kernel.calculate_json("null"))["ok"])

    def test_small_finite_polynomials_against_independent_substitution(self):
        rng = random.Random(731)
        for p in (2, 3, 5):
            for degree in (2, 3, 4, 5):
                expression = sum(rng.randrange(p)*x**i for i in range(degree)) + rng.randrange(1, p)*x**degree
                source = sp.sstr(expression).replace("**", "^")
                with self.subTest(p=p, source=source):
                    result = self.calculation(source, field="Fp", p=p)
                    self.assert_finite_roots(result, p)
                    reconstructed = kernel.parse_polynomial(result["factorization"]["text"])
                    self.assertTrue(sp.Poly(reconstructed.as_expr() - expression, x, modulus=p).is_zero)


if __name__ == "__main__":
    unittest.main()
