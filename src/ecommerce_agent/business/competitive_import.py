from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .competitive import CompetitiveDatasetRow, CompetitorSource


class CompetitiveCsvError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    field: str
    code: str
    message: str


class CompetitiveCsvRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    value: CompetitiveDatasetRow


class CompetitiveCsvParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_rows: int
    rows: list[CompetitiveCsvRow]
    errors: list[CompetitiveCsvError]


class CompetitiveDatasetRowError(ValueError):
    def __init__(self, field: str, code: str, message: str):
        super().__init__(message)
        self.field = field
        self.code = code
        self.message = message


COMPETITIVE_CSV_ALIASES = {
    "source_id": "source_id",
    "来源记录id": "source_id",
    "数据id": "source_id",
    "subject_sku": "subject_sku",
    "自有sku": "subject_sku",
    "本店sku": "subject_sku",
    "competitor_name": "competitor_name",
    "竞品名称": "competitor_name",
    "竞品店铺": "competitor_name",
    "competitor_sku": "competitor_sku",
    "竞品sku": "competitor_sku",
    "product_title": "product_title",
    "商品名称": "product_title",
    "商品标题": "product_title",
    "brand": "brand",
    "品牌": "brand",
    "model": "model",
    "型号": "model",
    "category": "category",
    "品类": "category",
    "类目": "category",
    "gtin": "gtin",
    "条码": "gtin",
    "商品条码": "gtin",
    "subject_price": "subject_price",
    "自有价格": "subject_price",
    "本店价格": "subject_price",
    "competitor_price": "competitor_price",
    "竞品价格": "competitor_price",
    "售价": "competitor_price",
    "currency": "currency",
    "币种": "currency",
    "rating_value": "rating_value",
    "商品评分": "rating_value",
    "评分": "rating_value",
    "rating_scale": "rating_scale",
    "评分满分": "rating_scale",
    "满分": "rating_scale",
    "sales_rank": "sales_rank",
    "销量排名": "sales_rank",
    "排名": "sales_rank",
    "rank_scope": "rank_scope",
    "排名范围": "rank_scope",
    "榜单": "rank_scope",
    "observed_at": "observed_at",
    "采集时间": "observed_at",
    "数据时间": "observed_at",
    "entity_match_id": "entity_match_id",
    "匹配id": "entity_match_id",
}

COMPETITIVE_CSV_REQUIRED = {
    "source_id",
    "subject_sku",
    "competitor_name",
    "competitor_sku",
    "product_title",
    "subject_price",
    "competitor_price",
    "currency",
    "observed_at",
}


def parse_competitive_csv(
    content: str,
    *,
    connector_id: str,
    store_id: str,
    source_ref: str,
    source_type: CompetitorSource = "file_import",
) -> CompetitiveCsvParseResult:
    normalized_source_ref = source_ref.strip()
    if (
        re.match(r"^[A-Za-z]:[\\/]", normalized_source_ref)
        or normalized_source_ref.startswith(("/", "\\\\", "~/"))
        or normalized_source_ref.casefold().startswith("file://")
    ):
        raise ValueError("competitive_source_ref_invalid")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("competitive_csv_header_missing")
    header_map = _competitive_csv_header_map(reader.fieldnames)
    raw_rows = list(reader)
    if len(raw_rows) > 2000:
        raise ValueError("competitive_csv_too_many_rows")

    rows: list[CompetitiveCsvRow] = []
    errors: list[CompetitiveCsvError] = []
    for row_number, raw_row in enumerate(raw_rows, start=1):
        try:
            normalized = _normalize_competitive_csv_row(raw_row, header_map)
            normalized.update(
                {
                    "connector_id": connector_id,
                    "store_id": store_id,
                    "source_ref": source_ref,
                    "source_type": source_type,
                }
            )
            rows.append(
                CompetitiveCsvRow(
                    row=row_number,
                    value=CompetitiveDatasetRow.model_validate(normalized),
                )
            )
        except Exception as exc:
            errors.append(competitive_csv_error(row_number, exc))
    return CompetitiveCsvParseResult(
        total_rows=len(raw_rows),
        rows=rows,
        errors=errors,
    )


def _competitive_csv_header_map(fieldnames: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    canonical_seen: set[str] = set()
    for raw_name in fieldnames:
        name = str(raw_name or "").strip().lstrip("\ufeff")
        normalized_name = name.casefold()
        if normalized_name in {"tenant_id", "租户id"}:
            raise ValueError("competitive_csv_forbidden_column:tenant_id")
        canonical = COMPETITIVE_CSV_ALIASES.get(normalized_name)
        if canonical is None and (
            normalized_name.startswith("dim.") or name.startswith("维度.")
        ):
            dimension_key = name.split(".", 1)[1].strip()
            if not dimension_key:
                raise ValueError("competitive_csv_dimension_key_missing")
            canonical = f"dim.{dimension_key}"
        if canonical is None:
            continue
        canonical_key = canonical.casefold()
        if canonical_key in canonical_seen:
            raise ValueError(f"competitive_csv_duplicate_column:{canonical}")
        canonical_seen.add(canonical_key)
        result[raw_name] = canonical
    missing = sorted(COMPETITIVE_CSV_REQUIRED - set(result.values()))
    if missing:
        raise ValueError(f"competitive_csv_required_column_missing:{missing[0]}")
    return result


def _normalize_competitive_csv_row(
    raw_row: dict[str, Any],
    header_map: dict[str, str],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    custom_dimensions: list[dict[str, Any]] = []
    for raw_name, canonical in header_map.items():
        raw_value = raw_row.get(raw_name)
        value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if value in (None, ""):
            continue
        if canonical.startswith("dim."):
            key = canonical.split(".", 1)[1]
            custom_dimensions.append(
                {
                    "key": key,
                    "label": key,
                    "value_type": "text",
                    "value_text": str(value),
                }
            )
        else:
            normalized[canonical] = value

    for field in sorted(COMPETITIVE_CSV_REQUIRED):
        if field not in normalized:
            raise CompetitiveDatasetRowError(
                field,
                "field_required",
                f"{field} is required",
            )
    _require_pair(normalized, "rating_value", "rating_scale")
    _require_pair(normalized, "sales_rank", "rank_scope")
    for field in ("subject_price", "competitor_price"):
        normalized[field] = _clean_csv_decimal(normalized[field], field, money=True)
    for field in ("rating_value", "rating_scale"):
        if field in normalized:
            normalized[field] = _clean_csv_decimal(
                normalized[field], field, money=False
            )
    if "sales_rank" in normalized:
        match = re.fullmatch(
            r"(?:第\s*)?([0-9][0-9,]*)(?:\s*名)?",
            str(normalized["sales_rank"]),
        )
        if match is None:
            raise CompetitiveDatasetRowError(
                "sales_rank",
                "integer_invalid",
                "sales_rank must be a positive integer",
            )
        normalized["sales_rank"] = int(match.group(1).replace(",", ""))
    normalized["currency"] = str(normalized["currency"]).upper()
    normalized["custom_dimensions"] = custom_dimensions
    return normalized


def _require_pair(values: dict[str, Any], left: str, right: str) -> None:
    if (left in values) == (right in values):
        return
    missing = right if left in values else left
    present = left if missing == right else right
    raise CompetitiveDatasetRowError(
        missing,
        "field_required",
        f"{missing} is required with {present}",
    )


def _clean_csv_decimal(value: Any, field: str, *, money: bool) -> Decimal:
    cleaned = unicodedata.normalize("NFKC", str(value)).strip()
    if money:
        cleaned = re.sub(r"^[¥￥$€£]\s*", "", cleaned)
        cleaned = re.sub(r"\s*元$", "", cleaned)
    cleaned = cleaned.replace(",", "")
    try:
        result = Decimal(cleaned)
    except Exception as exc:
        raise CompetitiveDatasetRowError(
            field,
            "decimal_invalid",
            f"{field} must be a decimal number",
        ) from exc
    return result.quantize(Decimal("0.01")) if money else result


def competitive_csv_error(row_number: int, exc: Exception) -> CompetitiveCsvError:
    if isinstance(exc, CompetitiveDatasetRowError):
        field, code, message = exc.field, exc.code, exc.message
    elif isinstance(exc, ValidationError):
        first = exc.errors(include_url=False)[0]
        field = ".".join(str(item) for item in first["loc"]) or "row"
        code = "field_required" if first["type"] == "missing" else "value_invalid"
        message = str(first["msg"])
    elif isinstance(exc, sqlite3.IntegrityError):
        field = "row"
        code = "row_conflict"
        message = "row conflicts with an existing observation"
    else:
        known_errors = {
            "stale_source_version": (
                "observed_at",
                "source version is older than the stored version",
            ),
            "source_version_conflict": (
                "source_id",
                "same source version has different content",
            ),
            "competitive_subject_sku_unavailable": (
                "subject_sku",
                "subject SKU is unavailable",
            ),
            "competitive_match_not_found": (
                "entity_match_id",
                "entity match is unavailable",
            ),
            "competitive_match_scope_mismatch": (
                "entity_match_id",
                "entity match is unavailable",
            ),
        }
        detail = str(exc)
        if detail in known_errors:
            field, message = known_errors[detail]
            code = detail
        else:
            field = "row"
            code = "row_invalid"
            message = "row could not be imported"
    return CompetitiveCsvError(
        row=row_number,
        field=field,
        code=code,
        message=message,
    )
