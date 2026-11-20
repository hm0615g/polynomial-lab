#!/usr/bin/env python3
"""Run the browser application's exact same calculation module from a terminal."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def load_calculate():
    kernel_path = Path(__file__).resolve().parent / "web" / "kernel.py"
    spec = importlib.util.spec_from_file_location("polynomial_lab_kernel", kernel_path)
    if spec is None or spec.loader is None:
        raise ImportError("web/kernel.py を読み込めません。")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.calculate


def text_result(result):
    if not result.get("ok"):
        error = result.get("error", {})
        return f"エラー: {error.get('message', '計算を完了できませんでした。')}"
    normalized = result["normalized"]
    irreducibility = result["irreducibility"]
    summary = result["summary"]
    lines = [
        f"係数体: {result['field']['label']}",
        f"多項式: {normalized['text']}",
        f"次数: {normalized['degree'] if normalized['degree'] is not None else '定義しません'}",
        f"既約性: {irreducibility['label']}",
        f"判定理由: {irreducibility['reason']}",
        f"因数分解: {result['factorization']['text']}",
        f"根を求める範囲: {summary['scope_label']}",
    ]
    if summary.get("special"):
        lines.append(summary["special"])
    if summary.get("distinct") is not None:
        lines.append(f"相異なる根の個数: {summary['distinct']}")
    if summary.get("total_multiplicity") is not None:
        lines.append(f"重複度を含む根の個数: {summary['total_multiplicity']}")
    if summary.get("all_roots_found"):
        lines.append("代数的閉体上の全根を確認しました。")
    for extension in result.get("extensions", []):
        lines.extend(["", f"拡大体: {extension['label']}", extension["definition_text"]])
    for index, root in enumerate(result.get("roots", []), 1):
        lines.extend(["", f"根 {index}: {root['text']}", f"重複度: {root['multiplicity']}"])
        if root.get("field_label"):
            lines.append(f"属する体: {root['field_label']}")
        if root.get("approx_text"):
            lines.append(f"近似値: {root['approx_text']}")
    for warning in result.get("warnings", []):
        lines.append(f"注意: {warning}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="多項式の根と既約性を厳密に計算します。Web版と同じ計算処理を使用します。",
        epilog="例: python cli.py 'x^4-4x^2+2' --field Q --json",
    )
    parser.add_argument("polynomial", nargs="?", help="多項式。省略時は標準入力から読み込みます。")
    parser.add_argument("--field", choices=("Q", "Fp"), default="Q", help="係数体（既定: Q）。")
    parser.add_argument("--p", default="2", help="F_p の素数 p（既定: 2）。")
    parser.add_argument("--root-scope", choices=("all", "real", "base"), default="all", help="根の範囲。all: 代数的閉体、real: 実数、base: 係数体。")
    parser.add_argument("--digits", type=int, default=15, help="近似値の有効桁数、5から50（既定: 15）。")
    parser.add_argument("--json", action="store_true", help="結果を機械処理用JSONで出力します。")
    args = parser.parse_args(argv)
    polynomial = args.polynomial
    if polynomial is None:
        if sys.stdin.isatty():
            parser.error("多項式を指定するか、標準入力から渡してください。")
        polynomial = sys.stdin.read(3001).strip()
    request = {
        "polynomial": polynomial,
        "field": args.field,
        "p": args.p,
        "root_scope": args.root_scope,
        "digits": args.digits,
    }
    exit_code = 0
    try:
        result = load_calculate()(request)
        if not result.get("ok"):
            exit_code = 2
    except ImportError as error:
        result = {"ok": False, "error": {"code": "DEPENDENCY_MISSING", "message": f"計算に必要なファイルまたはライブラリを読み込めません。python -m pip install -r requirements.txt を実行してください。詳細: {error}", "field": None}}
        exit_code = 1
    except Exception as error:
        result = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": f"計算を完了できませんでした。詳細: {error}", "field": None}}
        exit_code = 1
    output = json.dumps(result, ensure_ascii=False, indent=2) if args.json else text_result(result)
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
