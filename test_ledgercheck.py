"""ledgercheck 的自检测试。用独立临时 CSV 文件。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from ledgercheck import (LedgerError, compare, find_amount_column,  # noqa: E402
                         read_csv, to_decimal)

ENTRY = os.path.join(BASE, "ledgercheck.py")


def _csv(name: str, content: str) -> str:
    tmp = tempfile.mkdtemp(prefix="ledger_")
    path = Path(tmp) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_to_decimal_basic() -> None:
    assert to_decimal("123.45") == Decimal("123.45")
    assert to_decimal(" 42 ") == Decimal("42")
    assert to_decimal("¥1,000.00") == Decimal("1000.00")


def test_to_decimal_handles_negative_parentheses() -> None:
    assert to_decimal("(50.00)") == Decimal("-50.00")


def test_to_decimal_returns_none_for_empty() -> None:
    assert to_decimal("") is None
    assert to_decimal("   ") is None
    assert to_decimal(None) is None


def test_to_decimal_returns_none_for_non_numeric() -> None:
    assert to_decimal("abc") is None


def test_find_amount_column_by_name() -> None:
    rows = [{"id": "1", "amount": "10"}]
    assert find_amount_column(rows) == "amount"


def test_find_amount_column_by_hint() -> None:
    rows = [{"id": "1", "total_price": "10"}]
    assert find_amount_column(rows) == "total_price"


def test_find_amount_column_returns_none() -> None:
    assert find_amount_column([{"id": "1", "note": "x"}]) is None


def test_read_csv_missing_file() -> None:
    try:
        read_csv(Path("no_such.csv"))
    except LedgerError as e:
        assert "找不到文件" in str(e), str(e)
        return
    raise AssertionError("应当抛出 LedgerError")


def test_compare_detects_only_in_a() -> None:
    a = [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}]
    b = [{"id": "1", "amount": "10"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    assert len(result["only_in_a"]) == 1, result
    assert result["only_in_a"][0]["key"] == "2", result
    assert result["only_in_b"] == [], result


def test_compare_detects_only_in_b() -> None:
    a = [{"id": "1", "amount": "10"}]
    b = [{"id": "1", "amount": "10"}, {"id": "3", "amount": "30"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    assert len(result["only_in_b"]) == 1, result
    assert result["only_in_b"][0]["key"] == "3", result


def test_compare_detects_amount_mismatch() -> None:
    a = [{"id": "1", "amount": "10.00"}]
    b = [{"id": "1", "amount": "12.50"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    assert len(result["amount_mismatch"]) == 1, result
    assert result["amount_mismatch"][0]["delta"] == "2.50", result


def test_compare_respects_tolerance() -> None:
    a = [{"id": "1", "amount": "10.00"}]
    b = [{"id": "1", "amount": "10.005"}]
    strict = compare(a, b, "id", "amount", Decimal("0"))
    assert len(strict["amount_mismatch"]) == 1, strict
    loose = compare(a, b, "id", "amount", Decimal("0.01"))
    assert len(loose["amount_mismatch"]) == 0, loose


def test_compare_excludes_missing_source_from_totals() -> None:
    a = [{"id": "1", "amount": "10"}, {"id": "2", "amount": ""}]
    b = [{"id": "1", "amount": "10"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    assert result["totals"]["a_total"] == "10", result
    assert len(result["missing_source"]["a"]) == 1, result


def test_compare_flags_duplicate_keys() -> None:
    a = [{"id": "1", "amount": "10"}, {"id": "1", "amount": "20"}]
    b = [{"id": "1", "amount": "10"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    reasons = [m["reason"] for m in result["missing_source"]["a"]]
    assert any("重复" in r for r in reasons), result


def test_compare_flags_empty_key() -> None:
    a = [{"id": "", "amount": "10"}]
    b = [{"id": "1", "amount": "10"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    reasons = [m["reason"] for m in result["missing_source"]["a"]]
    assert any("为空" in r for r in reasons), result


def test_compare_residual_is_zero_when_explained() -> None:
    a = [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}]
    b = [{"id": "1", "amount": "10"}, {"id": "2", "amount": "25"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    residual = Decimal(result["reconciliation"]["residual"])
    assert abs(residual) < Decimal("0.0001"), result["reconciliation"]


def test_compare_totals() -> None:
    a = [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}]
    b = [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}]
    result = compare(a, b, "id", "amount", Decimal("0"))
    assert result["totals"]["a_total"] == "30", result
    assert result["totals"]["b_total"] == "30", result
    assert result["totals"]["difference"] == "0", result


def test_cli_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--help"], capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "ledgercheck" in r.stdout


def test_cli_reports_differences() -> None:
    a = _csv("a.csv", "id,amount\n1,10.00\n2,20.00\n")
    b = _csv("b.csv", "id,amount\n1,10.00\n2,25.00\n3,30.00\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--a", a, "--b", b,
                            "--key", "id"], capture_output=True, text=True,
                           timeout=30, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert "金额不同：  1 条" in r.stdout, r.stdout
        assert "只在 B 有：1 条" in r.stdout, r.stdout
    finally:
        shutil.rmtree(os.path.dirname(a), ignore_errors=True)
        shutil.rmtree(os.path.dirname(b), ignore_errors=True)


def test_cli_json_output() -> None:
    a = _csv("a2.csv", "id,amount\n1,10.00\n")
    b = _csv("b2.csv", "id,amount\n1,10.00\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--a", a, "--b", b,
                            "--key", "id", "--json"], capture_output=True,
                           text=True, timeout=30, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["counts"]["matched"] == 1, data
    finally:
        shutil.rmtree(os.path.dirname(a), ignore_errors=True)
        shutil.rmtree(os.path.dirname(b), ignore_errors=True)


def test_cli_missing_amount_column_exits_two() -> None:
    a = _csv("a3.csv", "id,note\n1,hello\n")
    b = _csv("b3.csv", "id,note\n1,world\n")
    try:
        r = subprocess.run([sys.executable, ENTRY, "--a", a, "--b", b,
                            "--key", "id"], capture_output=True, text=True,
                           timeout=30, encoding="utf-8", errors="replace")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "找不到金额列" in r.stderr, r.stderr
    finally:
        shutil.rmtree(os.path.dirname(a), ignore_errors=True)
        shutil.rmtree(os.path.dirname(b), ignore_errors=True)


def test_cli_missing_file_exits_two() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--a", "no_a.csv",
                        "--b", "no_b.csv", "--key", "id"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, (r.returncode, r.stderr)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK  ] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n共 {len(tests)} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
