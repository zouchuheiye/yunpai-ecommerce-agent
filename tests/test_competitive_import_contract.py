from __future__ import annotations

from decimal import Decimal

import pytest

from ecommerce_agent.business.competitive_import import parse_competitive_csv


IMPORT_CONTEXT = {
    "connector_id": "file-connector",
    "store_id": "store-a",
    "source_ref": "virtual-competitors.csv",
    "source_type": "virtual",
}


def test_csv_contract_maps_bom_chinese_headers_and_cleans_values() -> None:
    content = (
        "\ufeff来源记录ID,自有SKU,竞品名称,竞品SKU,商品标题,自有价格,竞品价格,"
        "币种,商品评分,评分满分,销量排名,排名范围,采集时间,维度.材质\n"
        'source-1,sku-a,竞店 A,comp-a,虚拟竞品 A,"4,999.00元","￥4,599.50",'
        "cny,9,10,第3名,平台/品类/日榜,2026-08-06T00:00:00+00:00,金属\n"
    )

    result = parse_competitive_csv(content, **IMPORT_CONTEXT)

    assert result.total_rows == 1
    assert result.errors == []
    assert len(result.rows) == 1
    parsed = result.rows[0]
    assert parsed.row == 1
    assert parsed.value.subject_price == Decimal("4999.00")
    assert parsed.value.competitor_price == Decimal("4599.50")
    assert parsed.value.currency == "CNY"
    assert parsed.value.rating_value == Decimal("9")
    assert parsed.value.rating_scale == Decimal("10")
    assert parsed.value.sales_rank == 3
    assert parsed.value.rank_scope == "平台/品类/日榜"
    assert parsed.value.custom_dimensions[0].model_dump(mode="json") == {
        "key": "材质",
        "label": "材质",
        "value_type": "text",
        "unit": None,
        "value_text": "金属",
        "value_number": None,
        "value_boolean": None,
    }


def test_csv_contract_keeps_valid_rows_and_returns_field_errors() -> None:
    content = (
        "source_id,subject_sku,competitor_name,competitor_sku,product_title,"
        "subject_price,competitor_price,currency,rating_value,rating_scale,"
        "sales_rank,rank_scope,observed_at\n"
        "ok-1,sku-a,Shop A,comp-a,Virtual A,100,90,CNY,4.5,5,1,Daily,"
        "2026-08-06T00:00:00+00:00\n"
        "bad-price,sku-a,Shop B,comp-b,Virtual B,100,not-money,CNY,4.5,5,2,Daily,"
        "2026-08-06T00:00:00+00:00\n"
        "bad-pair,sku-a,Shop C,comp-c,Virtual C,100,80,CNY,4.5,,3,Daily,"
        "2026-08-06T00:00:00+00:00\n"
        "bad-rank,sku-a,Shop D,comp-d,Virtual D,100,70,CNY,4.5,5,top,Daily,"
        "2026-08-06T00:00:00+00:00\n"
    )

    result = parse_competitive_csv(content, **IMPORT_CONTEXT)

    assert result.total_rows == 4
    assert [item.value.source_id for item in result.rows] == ["ok-1"]
    assert [error.model_dump() for error in result.errors] == [
        {
            "row": 2,
            "field": "competitor_price",
            "code": "decimal_invalid",
            "message": "competitor_price must be a decimal number",
        },
        {
            "row": 3,
            "field": "rating_scale",
            "code": "field_required",
            "message": "rating_scale is required with rating_value",
        },
        {
            "row": 4,
            "field": "sales_rank",
            "code": "integer_invalid",
            "message": "sales_rank must be a positive integer",
        },
    ]
    assert all(set(error.model_dump()) == {"row", "field", "code", "message"} for error in result.errors)


@pytest.mark.parametrize(
    "header",
    [
        "source_id,来源记录ID,subject_sku,competitor_name,competitor_sku,product_title,subject_price,competitor_price,currency,observed_at",
        "source_id,subject_sku,competitor_name,competitor_sku,product_title,subject_price,competitor_price,currency,observed_at,dim.",
    ],
)
def test_csv_contract_rejects_ambiguous_headers(header: str) -> None:
    with pytest.raises(ValueError, match="competitive_csv_"):
        parse_competitive_csv(f"{header}\n", **IMPORT_CONTEXT)


def test_csv_contract_rejects_more_than_2000_rows() -> None:
    header = (
        "source_id,subject_sku,competitor_name,competitor_sku,product_title,"
        "subject_price,competitor_price,currency,observed_at"
    )
    row = "source-{0},sku-a,Shop,comp-{0},Virtual,100,90,CNY,2026-08-06T00:00:00+00:00"
    content = "\n".join([header, *(row.format(index) for index in range(2001))])

    with pytest.raises(ValueError, match="competitive_csv_too_many_rows"):
        parse_competitive_csv(content, **IMPORT_CONTEXT)


def test_csv_contract_rejects_missing_required_header_even_without_rows() -> None:
    with pytest.raises(
        ValueError,
        match="competitive_csv_required_column_missing:competitor_price",
    ):
        parse_competitive_csv(
            "source_id,subject_sku,competitor_name,competitor_sku,product_title,"
            "subject_price,currency,observed_at\n",
            **IMPORT_CONTEXT,
        )


def test_csv_contract_rejects_tenant_column_and_absolute_source_path() -> None:
    header = (
        "tenant_id,source_id,subject_sku,competitor_name,competitor_sku,product_title,"
        "subject_price,competitor_price,currency,observed_at"
    )
    with pytest.raises(ValueError, match="competitive_csv_forbidden_column:tenant_id"):
        parse_competitive_csv(f"{header}\n", **IMPORT_CONTEXT)

    with pytest.raises(ValueError, match="competitive_source_ref_invalid"):
        parse_competitive_csv(
            CSV_HEADER_WITHOUT_TENANT,
            **{**IMPORT_CONTEXT, "source_ref": r"C:\Users\name\export.csv"},
        )


CSV_HEADER_WITHOUT_TENANT = (
    "source_id,subject_sku,competitor_name,competitor_sku,product_title,"
    "subject_price,competitor_price,currency,observed_at\n"
)
