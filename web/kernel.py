"""Exact polynomial calculations shared by the browser worker and the CLI.

Input is parsed by a small arithmetic parser, never as Python or a SymPy
expression.  Its intermediate values are bounded polynomials over Fraction.
"""

import json
import math
import re
from fractions import Fraction

import sympy as sp


MAX_INPUT_LENGTH = 3000
MAX_DEGREE = 50
MAX_COEFFICIENT_DIGITS = 100
MAX_PRIME = 2147483647
X = sp.Symbol("x")
_TOKEN = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)|x|[+*/^()\-]")
_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "u+": 3, "u-": 3, "^": 4}


class InputError(Exception):
    def __init__(self, code, message, field="polynomial"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def _checked(coefficients):
    result = {degree: value for degree, value in coefficients.items() if value}
    if result and max(result) > MAX_DEGREE:
        raise InputError("degree_limit", f"途中計算を含む次数の上限は{MAX_DEGREE}です。")
    for value in result.values():
        if (len(str(abs(value.numerator))) > MAX_COEFFICIENT_DIGITS
                or len(str(value.denominator)) > MAX_COEFFICIENT_DIGITS):
            raise InputError("coefficient_limit", "係数の分子・分母は、途中計算を含めてそれぞれ100桁以内にしてください。")
    return result


def _add(left, right, sign=1):
    result = left.copy()
    for degree, value in right.items():
        result[degree] = result.get(degree, Fraction(0)) + sign * value
    return _checked(result)


def _multiply(left, right):
    if not left or not right:
        return {}
    if max(left) + max(right) > MAX_DEGREE:
        raise InputError("degree_limit", f"途中計算を含む次数の上限は{MAX_DEGREE}です。")
    result = {}
    for i, a in left.items():
        for j, b in right.items():
            result[i + j] = result.get(i + j, Fraction(0)) + a * b
    return _checked(result)


def _constant(value):
    return _checked({0: Fraction(value)})


def _power(base, exponent):
    if any(degree != 0 for degree in exponent):
        raise InputError("invalid_exponent", "指数には整数の定数を指定してください。")
    exponent = exponent.get(0, Fraction(0))
    if exponent.denominator != 1:
        raise InputError("invalid_exponent", "指数には整数の定数を指定してください。")
    exponent = exponent.numerator
    if base and max(base) > 0:
        if exponent < 0:
            raise InputError("negative_exponent", "変数を含む式の指数は0以上にしてください。")
        if max(base) * exponent > MAX_DEGREE:
            raise InputError("degree_limit", f"途中計算を含む次数の上限は{MAX_DEGREE}です。")
    else:
        value = base.get(0, Fraction(0))
        if value == 0 and exponent < 0:
            raise InputError("division_by_zero", "0では割れません。")
        if value in (0, 1, -1):
            return _constant(value ** exponent)
        # Even 2^333 exceeds the 100 decimal digit coefficient limit.
        if abs(exponent) > 332:
            raise InputError("coefficient_limit", "この累乗は係数の100桁制限を超えます。")
        if exponent < 0:
            value, exponent = 1 / value, -exponent
        # Check while exponentiating, before a large integer can be allocated.
        base = _constant(value)
    result = _constant(1)
    while exponent:
        if exponent & 1:
            result = _multiply(result, base)
        exponent >>= 1
        if exponent:
            base = _multiply(base, base)
    return result


def _number(token):
    if "." in token:
        integer, decimal = token.split(".", 1)
        decimal = decimal.rstrip("0")
        digits = (integer + decimal).lstrip("0") or "0"
        if digits == "0":
            return {}
        # Parse only decimal digits; no expression parser receives user input.
        number = Fraction(int(digits), 10 ** len(decimal))
    else:
        number = Fraction(int(token))
    return _constant(number)


def _tokens(source):
    tokens = []
    position = 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(source, position)
        if match is None:
            raise InputError("invalid_character", f"{position + 1}文字目に使用できない文字があります。変数はx、演算子は + - * / ^、括弧は ( ) を使ってください。")
        token = match.group()
        if tokens and token in ("x", "("):
            previous = tokens[-1]
            if previous in ("x", ")") or previous[0].isdigit() or previous.startswith("."):
                tokens.append("*")
        tokens.append(token)
        position = match.end()
    return tokens


def parse_polynomial(source):
    """Parse exact rational polynomial arithmetic without evaluating code."""
    if not isinstance(source, str):
        raise InputError("invalid_input", "多項式を文字列で入力してください。")
    if len(source) > MAX_INPUT_LENGTH:
        raise InputError("input_limit", "入力は3000文字以内にしてください。")
    if not source.strip():
        raise InputError("empty_input", "多項式を入力してください。")
    values, operators = [], []

    def apply_operator():
        operator = operators.pop()
        if operator in ("u+", "u-"):
            if not values:
                raise InputError("syntax_error", "符号の後に数または式が必要です。")
            if operator == "u-":
                values[-1] = {i: -a for i, a in values[-1].items()}
            return
        if len(values) < 2:
            raise InputError("syntax_error", "演算子の前後に数または式が必要です。")
        right, left = values.pop(), values.pop()
        if operator in ("+", "-"):
            result = _add(left, right, 1 if operator == "+" else -1)
        elif operator == "*":
            result = _multiply(left, right)
        elif operator == "/":
            if any(i != 0 for i in right):
                raise InputError("nonconstant_denominator", "割り算の分母には、0でない定数を指定してください。")
            denominator = right.get(0, Fraction(0))
            if denominator == 0:
                raise InputError("division_by_zero", "0では割れません。")
            result = _checked({i: a / denominator for i, a in left.items()})
        else:
            result = _power(left, right)
        values.append(result)

    expecting_operand = True
    for token in _tokens(source):
        if token == "x" or token[0].isdigit() or token.startswith("."):
            if not expecting_operand:
                raise InputError("syntax_error", "数の間には演算子を指定してください。")
            values.append({1: Fraction(1)} if token == "x" else _number(token))
            expecting_operand = False
        elif token == "(":
            if not expecting_operand:
                raise InputError("syntax_error", "括弧の前に演算子が必要です。")
            operators.append(token)
            expecting_operand = True
        elif token == ")":
            if expecting_operand:
                raise InputError("syntax_error", "空の括弧や、演算子直後の閉じ括弧は使えません。")
            while operators and operators[-1] != "(":
                apply_operator()
            if not operators:
                raise InputError("syntax_error", "閉じ括弧に対応する開き括弧がありません。")
            operators.pop()
            expecting_operand = False
        elif expecting_operand:
            if token not in ("+", "-"):
                raise InputError("syntax_error", "数・変数・開き括弧のいずれかが必要です。")
            operators.append("u" + token)
        else:
            while operators and operators[-1] != "(":
                top = operators[-1]
                if (_PRECEDENCE[top] > _PRECEDENCE[token]
                        or (_PRECEDENCE[top] == _PRECEDENCE[token] and token != "^")):
                    apply_operator()
                else:
                    break
            operators.append(token)
            expecting_operand = True
    if expecting_operand:
        raise InputError("syntax_error", "式が途中で終わっています。")
    while operators:
        if operators[-1] == "(":
            raise InputError("syntax_error", "開き括弧に対応する閉じ括弧がありません。")
        apply_operator()
    if len(values) != 1:
        raise InputError("syntax_error", "多項式の書式を確認してください。")
    return sp.Poly.from_dict(
        {(i,): sp.Rational(a.numerator, a.denominator) for i, a in values[0].items()},
        X, domain=sp.QQ,
    )


def _text(expression):
    return sp.sstr(expression).replace("**", "^")


def _degree(poly):
    return None if poly.is_zero else int(poly.degree())


def _finite_expr(poly, p, symbol=X):
    return sp.Add(*(sp.Integer(int(c) % p) * symbol ** monomial[0]
                    for monomial, c in poly.terms()))


def _factorization(unit, factors, p=None):
    details, text_parts, latex_parts = [], [], []
    if unit != 1 or not factors:
        text_parts.append(_text(unit))
        latex_parts.append(sp.latex(unit))
    for factor, multiplicity in factors:
        expression = _finite_expr(factor, p) if p else factor.as_expr()
        literal, latex = _text(expression), sp.latex(expression)
        details.append({"text": literal, "latex": latex, "degree": int(factor.degree()),
                        "multiplicity": int(multiplicity)})
        text_parts.append(f"({literal})" + (f"^{multiplicity}" if multiplicity != 1 else ""))
        latex_parts.append(r"\left(" + latex + r"\right)"
                           + (f"^{{{multiplicity}}}" if multiplicity != 1 else ""))
    return {"text": "*".join(text_parts), "latex": r" \cdot ".join(latex_parts), "factors": details}


def _eisenstein_prime(poly):
    """Look for a short sufficient-condition explanation, never for a verdict."""
    integer_poly = poly.clear_denoms(convert=True)[1].primitive()[1]
    coefficients = [int(c) for c in integer_poly.all_coeffs()]
    common = 0
    for coefficient in coefficients[1:]:
        common = math.gcd(common, coefficient)
    if common < 2 or coefficients[-1] == 0:
        return None
    # A bounded supplementary check; exact factorization decides irreducibility.
    for p in sp.primerange(2, 1000):
        if common % p == 0 and coefficients[0] % p and coefficients[-1] % (p * p):
            return int(p)
    return None


def _q_roots(factors, scope, digits):
    result, uses_rootof = [], False
    for factor, outer_multiplicity in factors:
        if scope == "base" and factor.degree() != 1:
            continue
        # Try the symbolic solver at every degree: besides general cubic and
        # quartic formulas it handles supported quintics, binomials and cyclotomics.
        # Use its output only when the complete factor is accounted for.
        explicit = sp.roots(factor.as_expr(), X, cubics=True, quartics=True, quintics=True)
        if sum(explicit.values()) == factor.degree():
            root_pairs = list(explicit.items())
        else:
            root_pairs = (factor.real_roots(multiple=False) if scope == "real"
                          else factor.all_roots(multiple=False))
        if scope == "all" and sum(int(mult) for _, mult in root_pairs) != factor.degree():
            raise ArithmeticError("Exact root coverage failed")
        for root, inner_multiplicity in root_pairs:
            is_real = root.is_real
            if is_real is None:
                # Both values are roots of this rational-coefficient factor.
                # same_root uses a root-separation bound and bounded-error
                # evaluation, not an arbitrary numerical proximity threshold.
                is_real = bool(factor.same_root(root, sp.conjugate(root)))
            if scope == "real" and not is_real:
                continue
            uses_rootof = uses_rootof or root.has(sp.CRootOf)
            # Once reality is certified, evaluate only the real part. Cardano
            # expressions may have complex intermediates even for real roots.
            approximation = (sp.re(root) if is_real else root).evalf(digits, maxn=1000, strict=True)
            label = "Q" if root.is_Rational else ("Qbar ∩ R" if is_real else "Qbar")
            result.append({"text": _text(root), "latex": sp.latex(root),
                           "approx_text": _text(approximation), "approx_latex": sp.latex(approximation),
                           "multiplicity": int(outer_multiplicity * inner_multiplicity),
                           "field_label": label, "extension_id": None})
    return result, uses_rootof


def _mod_multiply(left, right, modulus, p):
    """Multiply coefficient vectors modulo a monic degree-d polynomial."""
    degree = len(modulus) - 1
    product = [0] * (2 * degree - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            product[i + j] = (product[i + j] + a * b) % p
    for k in range(len(product) - 1, degree - 1, -1):
        coefficient = product[k]
        if coefficient:
            for j in range(degree):
                product[k - degree + j] = (product[k - degree + j] - coefficient * modulus[j]) % p
    return product[:degree]


def _mod_power(base, exponent, modulus, p):
    result = [1] + [0] * (len(modulus) - 2)
    while exponent:
        if exponent & 1:
            result = _mod_multiply(result, base, modulus, p)
        exponent >>= 1
        if exponent:
            base = _mod_multiply(base, base, modulus, p)
    return result


def _fp_roots(factors, p, scope):
    roots, extensions = [], []
    for factor, multiplicity in factors:
        factor = factor.monic()
        degree = int(factor.degree())
        if degree == 1:
            root = (-int(factor.nth(0))) % p
            roots.append({"text": str(root), "latex": str(root), "approx_text": None,
                          "approx_latex": None, "multiplicity": int(multiplicity),
                          "field_label": f"F_{p}", "extension_id": None})
            continue
        if scope == "base":
            continue
        index = len(extensions) + 1
        identifier, symbol = f"K{index}", sp.Symbol(f"a{index}")
        variable = sp.Symbol("t")
        modulus_expr = _finite_expr(factor, p)
        defining_expr = _finite_expr(factor, p, variable)
        modulus = [int(factor.nth(i)) % p for i in range(degree + 1)]
        extensions.append({
            "id": identifier, "symbol": str(symbol), "label": f"{identifier}（F_({p}^{degree})と同型）",
            "degree": degree, "modulus_text": _text(modulus_expr), "modulus_latex": sp.latex(modulus_expr),
            "definition_text": f"{identifier} = F_{p}[t] / ({_text(defining_expr)}), {symbol} = t mod ({_text(defining_expr)})",
            "definition_latex": f"K_{{{index}}} = \\mathbb{{F}}_{{{p}}}[t]/\\left({sp.latex(defining_expr)}\\right),\\quad {sp.latex(symbol)} = t \\bmod \\left({sp.latex(defining_expr)}\\right)",
        })
        initial = [0, 1] + [0] * (degree - 2)
        coordinate, seen = initial, set()
        for _ in range(degree):
            key = tuple(coordinate)
            if key in seen:
                raise ArithmeticError("Frobenius orbit ended before the factor degree")
            seen.add(key)
            expression = sp.Add(*(sp.Integer(c) * symbol ** i for i, c in enumerate(coordinate) if c))
            roots.append({"text": _text(expression), "latex": sp.latex(expression), "approx_text": None,
                          "approx_latex": None, "multiplicity": int(multiplicity),
                          "field_label": identifier, "extension_id": identifier})
            coordinate = _mod_power(coordinate, p, modulus, p)
        if coordinate != initial:
            raise ArithmeticError("Frobenius orbit did not close")
    return roots, extensions


def _integer_option(value, field, lower, upper):
    if isinstance(value, bool):
        valid = False
    elif isinstance(value, int):
        valid = True
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        value = value.strip()
        valid = len(value) <= 12
        if valid:
            value = int(value)
    else:
        valid = False
    if not valid or not lower <= value <= upper:
        name = "素数p" if field == "p" else "近似値の有効桁数"
        raise InputError(f"invalid_{field}", f"{name}は{lower}以上{upper}以下の整数を指定してください。", field)
    return value


def _calculate(request):
    if not isinstance(request, dict):
        raise InputError("invalid_request", "計算条件をオブジェクトで指定してください。", None)
    field = request.get("field", "Q")
    if field not in ("Q", "Fp"):
        raise InputError("invalid_field", "係数体はQまたはFpを選んでください。", None)
    scope = request.get("root_scope", "all")
    if scope not in ("all", "real", "base") or (field == "Fp" and scope == "real"):
        raise InputError("invalid_root_scope", "根の範囲を確認してください。有限体では実数範囲を選べません。", "root_scope")
    digits = _integer_option(request.get("digits", 15), "digits", 5, 50)
    p = None
    if field == "Fp":
        p = _integer_option(request.get("p", "2"), "p", 2, MAX_PRIME)
        if not sp.isprime(p):
            raise InputError("not_prime", "pには素数を指定してください。合成数を法とする剰余環は体ではありません。", "p")
    source = request.get("polynomial", "")
    rational_poly = parse_polynomial(source)
    warnings = []
    if p:
        coefficients = {}
        for monomial, coefficient in rational_poly.terms():
            numerator, denominator = map(int, coefficient.as_numer_denom())
            if denominator % p == 0:
                raise InputError("denominator_not_invertible", f"約分後の係数の分母が{p}で割り切れるため、F_{p}へ写せません。")
            coefficients[monomial] = numerator * pow(denominator, -1, p) % p
        poly = sp.Poly.from_dict(coefficients, X, modulus=p)
        expression = _finite_expr(poly, p)
        if _degree(poly) != _degree(rational_poly):
            after = "零多項式" if poly.is_zero else f"次数{poly.degree()}"
            warnings.append(f"F_{p}で係数を還元すると、入力の次数{rational_poly.degree()}から{after}に変わります。以後は還元後の多項式を計算します。")
    else:
        poly, expression = rational_poly, rational_poly.as_expr()
    degree = _degree(poly)
    field_label = "有理数体 Q" if p is None else f"有限体 F_{p}"
    scope_label = ({"all": "代数的閉体 Qbar", "real": "実数 R", "base": "有理数 Q"}[scope]
                   if p is None else (f"F_{p}の代数的閉体（因子ごとの拡大体）" if scope == "all" else f"有限体 F_{p}"))
    result = {
        "ok": True, "input": source, "field": {"kind": field, "label": field_label, "characteristic": p or 0},
        "normalized": {"text": _text(expression), "latex": sp.latex(expression), "degree": degree},
        "irreducibility": {}, "factorization": {}, "roots": [], "extensions": [],
        "summary": {"degree": degree, "distinct": 0, "total_multiplicity": 0,
                    "all_roots_found": False, "scope_label": scope_label, "special": None},
        "warnings": warnings, "eisenstein_prime": None,
    }
    if poly.is_zero:
        result["irreducibility"] = {"status": "zero", "label": "零多項式（判定対象外）",
                                    "reason": "零多項式は、既約・可約の二択の対象外です。"}
        result["factorization"] = {"text": "0", "latex": "0", "factors": []}
        if p and scope == "base":
            special = f"零多項式はF_{p}の全{p}個の元を根に持ちます。各根の有限な重複度と次数は定義しません。"
        else:
            special = "零多項式は、指定した範囲のすべての元を根に持ちます（無限個）。各根の有限な重複度と次数は定義しません。"
        result["summary"].update(distinct=None, total_multiplicity=None, special=special)
        return result
    if degree == 0:
        result["irreducibility"] = {"status": "unit", "label": "単元（0でない定数）",
                                    "reason": "体上の0でない定数は単元であり、既約多項式には含めません。"}
        result["factorization"] = {"text": _text(expression), "latex": sp.latex(expression), "factors": []}
        result["summary"].update(all_roots_found=True, special="0でない定数なので根はありません。")
        return result
    unit, factors = poly.factor_list()
    factors = [(factor, int(multiplicity)) for factor, multiplicity in factors]
    if sum(int(factor.degree()) * multiplicity for factor, multiplicity in factors) != degree:
        raise ArithmeticError("Factorization degree coverage failed")
    if p:
        unit = sp.Integer(int(unit) % p)
    result["factorization"] = _factorization(unit, factors, p)
    irreducible = len(factors) == 1 and factors[0][1] == 1
    if p is None and irreducible:
        result["eisenstein_prime"] = _eisenstein_prime(poly)
    if not irreducible:
        reason = f"{field_label}上の厳密な因数分解で、正次数の非自明な因子に分解できます。"
    elif degree == 1:
        reason = "体上の一次多項式は既約です。"
    elif result["eisenstein_prime"]:
        reason = f"分母と係数の共通因子を除いた原始整数係数多項式が、素数{result['eisenstein_prime']}でアイゼンシュタインの判定条件を満たします。Q上の厳密な因数分解でも確認しました。"
    else:
        reason = f"{field_label}上の厳密な因数分解で、正次数の非自明な因子がないことを確認しました。"
    result["irreducibility"] = {"status": "irreducible" if irreducible else "reducible",
                                "label": "既約" if irreducible else "可約", "reason": reason}
    if p:
        result["roots"], result["extensions"] = _fp_roots(factors, p, scope)
        if result["extensions"]:
            warnings.append("拡大体は既約因子ごとに定義しています。根はその体の基底で還元した厳密な座標です。異なる拡大体の根同士の演算は、この表示の対象外です。")
    else:
        result["roots"], uses_rootof = _q_roots(factors, scope, digits)
        if uses_rootof:
            warnings.append("計算系が明示式を取得できなかった根には、CRootOf(g(x), k)を使用しています。根号などによる表示が不可能だと判定したものではありません。多項式gの根を区間分離と番号k（0始まり）で特定する厳密な代数的数表示で、近似値も併記しています。")
    total = sum(root["multiplicity"] for root in result["roots"])
    if scope == "all" and total != degree:
        raise ArithmeticError("Whole-root multiplicity coverage failed")
    if scope != "all" and total < degree:
        warnings.append("指定した範囲にある根を表示しています。範囲外の根は含めていません。既約性は選択した係数体上で判定しています。")
    result["summary"].update(distinct=len(result["roots"]), total_multiplicity=total,
                             all_roots_found=(total == degree))
    return result


def calculate(request):
    """Return a JSON-compatible result, with validation failures as data."""
    try:
        return _calculate(request)
    except InputError as error:
        return {"ok": False, "error": {"code": error.code, "message": error.message, "field": error.field}}
    except (ArithmeticError, NotImplementedError, sp.PolynomialError) as error:
        return {"ok": False, "error": {"code": "calculation_failed", "message": "厳密計算を完了できませんでした。式の次数や係数を小さくして再度お試しください。", "field": None}}


def calculate_json(request_json):
    try:
        request = json.loads(request_json)
    except (ValueError, TypeError):
        return json.dumps({"ok": False, "error": {"code": "invalid_json", "message": "計算条件のJSONを読み取れませんでした。", "field": None}}, ensure_ascii=False)
    return json.dumps(calculate(request), ensure_ascii=False, allow_nan=False)
