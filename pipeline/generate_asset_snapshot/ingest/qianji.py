"""Qianji (钱迹) CSV parser.

Reads a Qianji export CSV and returns a list of normalized cashflow dicts.
"""

import csv
from pathlib import Path

from ..types import QJ_EXPENSE, QJ_INCOME, QJ_REPAYMENT, QJ_TRANSFER, QianjiRecord

# Mapping from Qianji CSV header names to internal field names.
_FIELD_MAP: dict[str, str] = {
    "ID": "id",
    "时间": "date",
    "分类": "category",
    "二级分类": "subcategory",
    "类型": "type",
    "金额": "amount",
    "币种": "currency",
    "账户1": "account_from",
    "账户2": "account_to",
    "备注": "note",
}

# Qianji type values mapped to lowercase internal types.
_TYPE_MAP: dict[str, str] = {
    "Income": QJ_INCOME,
    "Expense": QJ_EXPENSE,
    "Transfer": QJ_TRANSFER,
    "Repayment": QJ_REPAYMENT,
}


def _parse_reader(reader: csv.DictReader[str], source: str = "") -> list[QianjiRecord]:
    """Parse a Qianji CSV DictReader into normalized records."""
    if reader.fieldnames is None:
        raise ValueError(f"Empty CSV{': ' + source if source else ''}")

    missing = set(_FIELD_MAP.keys()) - set(reader.fieldnames)
    if missing:
        raise ValueError(f"Missing required CSV headers: {missing}")

    records: list[QianjiRecord] = []
    for row in reader:
        raw_type = row["类型"]
        mapped_type = _TYPE_MAP.get(raw_type)
        if mapped_type is None:
            raise ValueError(f"Unknown Qianji type: {raw_type!r}")

        record: QianjiRecord = {
            "id": row["ID"],
            "date": row["时间"],
            "category": row["分类"],
            "subcategory": row["二级分类"] or "",
            "type": mapped_type,
            "amount": float(row["金额"]),
            "currency": row["币种"],
            "account_from": row["账户1"],
            "account_to": row["账户2"] or "",
            "note": row["备注"] or "",
        }
        records.append(record)
    return records


def load_cashflow(csv_path: Path) -> list[QianjiRecord]:
    """Parse a Qianji CSV export file and return normalized cashflow records.

    Raises:
        FileNotFoundError: If the CSV file does not exist.
        ValueError: If the CSV is missing required headers or has invalid data.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Qianji CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig") as f:
        return _parse_reader(csv.DictReader(f), source=str(csv_path))
