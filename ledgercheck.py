"""ledgercheck —— 按对齐键比较两份账目明细，定位差异。

设计原则：
1. 先定位差异，再谈合计。
2. 来源追不到的金额单独列出，不并进任何合计。
3. 同时给多种口径的合计，让读者自己判断用哪个。

仅依赖 Python 标准库。

用法:
    python ledgercheck.py --help
    python ledgercheck.py --a a.csv --b b.csv --key id
    python ledgercheck.py --a a.csv --b b.csv --key id --tolerance 0.01 --json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# 常见的金额列名
AMOUNT_HINTS = ("amount", "money", "price", "total", "sum", "cost",
                "value", "fee", "金额", "费用", "总价")


class LedgerError(ValueError):
    """账目数据问题。"""


def read_csv(path: Path) -> list[dict]:
    """读一份 CSV 明细。"""
    if not path.is_file():
        raise LedgerError(f"找不到文件：{path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise LedgerError(f"文件没有表头：{path}")
            return list(reader)
    except UnicodeDecodeError as e:
        raise LedgerError(
            f"读取失败（编码不是 UTF-8）：{path}\n"
            f"请先转换编码，例如 iconv -f GBK -t UTF-8 输入.csv > 输出.csv"
        ) from e
    except OSError as e:
        raise LedgerError(f"读取失败：{path}（{e}）") from e


def find_amount_column(rows: list[dict]) -> str | None:
    """猜出金额列。猜不到返回 None。"""
    if not rows:
        return None
    for name in rows[0].keys():
        if name is None:
            continue
        lowered = name.strip().lower()
        if lowered in AMOUNT_HINTS:
            return name
    for name in rows[0].keys():
        if name is None:
            continue
        lowered = name.strip().lower()
        if any(hint in lowered for hint in AMOUNT_HINTS):
            return name
    return None


def to_decimal(raw: str) -> Decimal | None:
    """把金额字符串转成 Decimal。空值返回 None（表示来源缺失）。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # 去掉货币符号与千分位逗号
    cleaned = text.replace(",", "").replace("¥", "").replace("$", "")
    cleaned = cleaned.replace("￥", "").replace("RMB", "").replace("CNY", "")
    cleaned = cleaned.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def index_rows(rows: list[dict], key: str, amount_col: str) -> tuple[dict, list[dict]]:
    """按对齐键建立索引，同时挑出金额缺失的行。"""
    index: dict[str, dict] = {}
    missing: list[dict] = []
    for number, row in enumerate(rows, 1):
        raw_key = (row.get(key) or "").strip()
        if not raw_key:
            missing.append({"row": number, "reason": f"对齐键 {key} 为空",
                            "detail": str(row)[:120]})
            continue
        amount = to_decimal(row.get(amount_col))
        if amount is None:
            missing.append({"row": number, "reason": "金额缺失或无法解析",
                            "detail": f"键 {raw_key} 的金额字段为空或不是数字"})
            continue
        if raw_key in index:
            missing.append({"row": number, "reason": "对齐键重复",
                            "detail": f"键 {raw_key} 在 A 中出现多次，只保留第一条"})
            continue
        index[raw_key] = {"amount": amount, "raw": row}
    return index, missing


def compare(a_rows: list[dict], b_rows: list[dict], key: str,
            amount_col: str, tolerance: Decimal) -> dict:
    """比较两份明细。"""
    a_index, a_missing = index_rows(a_rows, key, amount_col)
    b_index, b_missing = index_rows(b_rows, key, amount_col)

    only_a = []
    only_b = []
    amount_diff = []
    matched = 0

    for row_key, a_entry in a_index.items():
        b_entry = b_index.get(row_key)
        if b_entry is None:
            only_a.append({"key": row_key, "amount": str(a_entry["amount"]),
                           "raw": a_entry["raw"]})
            continue
        matched += 1
        delta = b_entry["amount"] - a_entry["amount"]
        if abs(delta) > tolerance:
            amount_diff.append({
                "key": row_key,
                "a_amount": str(a_entry["amount"]),
                "b_amount": str(b_entry["amount"]),
                "delta": str(delta),
                "a_raw": a_entry["raw"],
                "b_raw": b_entry["raw"],
            })

    for row_key, b_entry in b_index.items():
        if row_key not in a_index:
            only_b.append({"key": row_key, "amount": str(b_entry["amount"]),
                           "raw": b_entry["raw"]})

    # 合计：多种口径
    a_total = sum((e["amount"] for e in a_index.values()), Decimal("0"))
    b_total = sum((e["amount"] for e in b_index.values()), Decimal("0"))
    diff_total = b_total - a_total

    # 只在两边都出现的键上算的合计（排除「只在 A」「只在 B」的影响）
    common_total = Decimal("0")
    for row_key, a_entry in a_index.items():
        if row_key in b_index:
            common_total += a_entry["amount"]

    explained = sum((Decimal(d["delta"]) for d in amount_diff), Decimal("0"))
    only_a_total = sum((Decimal(o["amount"]) for o in only_a), Decimal("0"))
    only_b_total = sum((Decimal(o["amount"]) for o in only_b), Decimal("0"))

    return {
        "key": key,
        "amount_column": amount_col,
        "tolerance": str(tolerance),
        "counts": {
            "a_rows": len(a_rows),
            "b_rows": len(b_rows),
            "a_indexed": len(a_index),
            "b_indexed": len(b_index),
            "matched": matched,
            "only_in_a": len(only_a),
            "only_in_b": len(only_b),
            "amount_mismatch": len(amount_diff),
        },
        "only_in_a": only_a,
        "only_in_b": only_b,
        "amount_mismatch": amount_diff,
        "missing_source": {"a": a_missing, "b": b_missing},
        "totals": {
            "a_total": str(a_total),
            "b_total": str(b_total),
            "difference": str(diff_total),
            "common_keys_total": str(common_total),
        },
        "reconciliation": {
            "explained_by_amount_diff": str(explained),
            "only_in_a_total": str(only_a_total),
            "only_in_b_total": str(only_b_total),
            "residual": str(diff_total - explained - only_b_total + only_a_total),
            "note": ("残差应接近 0。若不为 0，说明还有未被分类的差异，"
                     "通常来自来源缺失的行或浮点误差。"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ledgercheck",
        description="按对齐键比较两份账目明细并定位差异（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python ledgercheck.py --a 十月.csv --b 十一月.csv --key id\n"
            "  python ledgercheck.py --a a.csv --b b.csv --key invoice --amount amount\n"
            "  python ledgercheck.py --a a.csv --b b.csv --key id --tolerance 0.01 --json\n"
            "\n"
            "两份文件要有相同的表头；金额列不指定时会自动猜，猜不到会报错。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--a", required=True, help="第一份 CSV（A）")
    ap.add_argument("--b", required=True, help="第二份 CSV（B）")
    ap.add_argument("--key", required=True, help="对齐键所在的列名，如 id / invoice")
    ap.add_argument("--amount", default="", help="金额列名；不给则自动猜")
    ap.add_argument("--tolerance", default="0", help="金额比较容差（默认 0）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        tolerance = Decimal(args.tolerance)
    except InvalidOperation:
        print(f"容差不是合法数字：{args.tolerance}", file=sys.stderr)
        return 2

    try:
        a_rows = read_csv(Path(args.a))
        b_rows = read_csv(Path(args.b))
    except LedgerError as e:
        print(str(e), file=sys.stderr)
        return 2

    if not a_rows:
        print(f"第一份文件没有数据行：{args.a}", file=sys.stderr)
        return 1
    if not b_rows:
        print(f"第二份文件没有数据行：{args.b}", file=sys.stderr)
        return 1

    amount_col = args.amount or find_amount_column(a_rows) or find_amount_column(b_rows)
    if not amount_col:
        print("找不到金额列。请用 --amount 指定列名。", file=sys.stderr)
        return 2

    if amount_col not in (a_rows[0].keys() or []) and \
            amount_col not in (b_rows[0].keys() or []):
        print(f"指定的金额列不存在：{amount_col}", file=sys.stderr)
        return 2

    result = compare(a_rows, b_rows, args.key, amount_col, tolerance)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    counts = result["counts"]
    totals = result["totals"]
    print(f"对齐键：{result['key']}    金额列：{result['amount_column']}"
          f"    容差：{result['tolerance']}")
    print()
    print(f"A 共 {counts['a_rows']} 行（有效 {counts['a_indexed']}），"
          f"B 共 {counts['b_rows']} 行（有效 {counts['b_indexed']}）")
    print(f"键能对上：{counts['matched']} 条")
    print()
    print("差异分布：")
    print(f"  只在 A 有：{counts['only_in_a']} 条")
    print(f"  只在 B 有：{counts['only_in_b']} 条")
    print(f"  金额不同：  {counts['amount_mismatch']} 条")
    print()

    if result["only_in_a"]:
        print("只在 A 有的条目：")
        for item in result["only_in_a"][:20]:
            print(f"  {item['key']}    {item['amount']}")
        if len(result["only_in_a"]) > 20:
            print(f"  ...（还有 {len(result['only_in_a']) - 20} 条）")
        print()

    if result["only_in_b"]:
        print("只在 B 有的条目：")
        for item in result["only_in_b"][:20]:
            print(f"  {item['key']}    {item['amount']}")
        if len(result["only_in_b"]) > 20:
            print(f"  ...（还有 {len(result['only_in_b']) - 20} 条）")
        print()

    if result["amount_mismatch"]:
        print("金额不同的条目：")
        for item in result["amount_mismatch"][:20]:
            print(f"  {item['key']}    A={item['a_amount']}  "
                  f"B={item['b_amount']}  差={item['delta']}")
        if len(result["amount_mismatch"]) > 20:
            print(f"  ...（还有 {len(result['amount_mismatch']) - 20} 条）")
        print()

    missing = result["missing_source"]
    if missing["a"] or missing["b"]:
        print("来源缺失的条目（不并入任何合计）：")
        for side, items in (("A", missing["a"]), ("B", missing["b"])):
            for item in items:
                print(f"  {side} 第 {item['row']} 行：{item['reason']}")
                print(f"    {item['detail']}")
        print()

    print("合计（多种口径）：")
    print(f"  A 全部有效行合计：   {totals['a_total']}")
    print(f"  B 全部有效行合计：   {totals['b_total']}")
    print(f"  两者差：             {totals['difference']}")
    print(f"  仅两边共有的键合计： {totals['common_keys_total']}")
    print()

    reconciliation = result["reconciliation"]
    print("差异归因：")
    print(f"  金额不同贡献： {reconciliation['explained_by_amount_diff']}")
    print(f"  只在 B 有：     {reconciliation['only_in_b_total']}")
    print(f"  只在 A 有：     {reconciliation['only_in_a_total']}")
    print(f"  残差：          {reconciliation['residual']}")
    print(f"  {reconciliation['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
