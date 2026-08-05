from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api import create_app
from ecommerce_agent.business import (
    CatalogItemUpsert,
    CompetitiveCustomDimension,
    CompetitiveDatasetRow,
    CompetitorObservationCreate,
)
from ecommerce_agent.service import AgentService

from conftest import make_settings


TENANT_ID = "tenant-test"


def seed_subject_catalog(service: AgentService) -> None:
    service.operations.catalog.upsert(
        TENANT_ID,
        CatalogItemUpsert(
            connector_id="catalog-feed",
            store_id="store-a",
            item_id="item-a",
            sku_id="sku-a",
            title="云湃智能客服一体机 YP-100",
            status="active",
            sale_price=Decimal("4999"),
            currency="CNY",
            attributes={
                "brand": "云湃",
                "model": "YP-100",
                "category": "智能客服一体机",
                "gtin": "06912345678901",
            },
            source_updated_at=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
            source_id="catalog-source-a",
        ),
    )


def dataset_row(**updates) -> CompetitiveDatasetRow:
    values = {
        "connector_id": "licensed-feed",
        "store_id": "store-a",
        "source_ref": "https://licensed.example/datasets/1",
        "source_type": "licensed_provider",
        "source_id": "dataset-row-1",
        "subject_sku": "sku-a",
        "competitor_name": "竞店 A",
        "competitor_sku": "comp-a",
        "product_title": "竞品智能客服一体机 CP-100",
        "brand": "竞品品牌",
        "model": "CP-100",
        "category": "智能客服一体机",
        "attributes": {"颜色": "曜石黑"},
        "custom_dimensions": [
            CompetitiveCustomDimension(
                key="battery_hours",
                label="续航时长",
                value_type="number",
                value_number=Decimal("12.5"),
                unit="hour",
            )
        ],
        "subject_price": Decimal("4999"),
        "competitor_price": Decimal("4599"),
        "currency": "CNY",
        "rating_value": Decimal("9"),
        "rating_scale": Decimal("10"),
        "sales_rank": 3,
        "rank_scope": "平台/智能客服一体机/日榜",
        "is_estimate": False,
        "observed_at": datetime(2026, 8, 5, 1, 0, tzinfo=UTC),
    }
    values.update(updates)
    return CompetitiveDatasetRow(**values)


def test_manual_dataset_record_creates_pending_match_and_observation(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)

        result = service.operations.competitive.record_dataset(
            TENANT_ID, dataset_row()
        )

        assert result["match"]["status"] == "pending"
        assert result["match"]["subject_identity"]["title"] == (
            "云湃智能客服一体机 YP-100"
        )
        assert result["identity"]["custom_dimensions"][0] == {
            "key": "battery_hours",
            "label": "续航时长",
            "value_type": "number",
            "value_text": None,
            "value_number": "12.5",
            "value_boolean": None,
            "unit": "hour",
        }
        assert result["latest_observation"]["entity_match_id"] == result["match"]["id"]
        assert result["latest_observation"]["normalized_rating"] == "4.50"
        assert result["signals"] == []
        assert result["actionable"] is False
        assert result["write_status"] == "applied"

        with service.db.connect() as conn:
            match_count = conn.execute(
                "SELECT COUNT(*) FROM competitive_entity_matches WHERE tenant_id=?",
                (TENANT_ID,),
            ).fetchone()[0]
            observation_count = conn.execute(
                "SELECT COUNT(*) FROM competitor_observations WHERE tenant_id=?",
                (TENANT_ID,),
            ).fetchone()[0]
        assert match_count == 1
        assert observation_count == 1
    finally:
        service.close()


def test_csv_import_skips_invalid_rows_and_replays_idempotently(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    csv_text = """source_id,subject_sku,competitor_name,competitor_sku,product_title,brand,category,subject_price,competitor_price,currency,rating_value,rating_scale,sales_rank,rank_scope,observed_at,dim.color
csv-row-1,sku-a,竞店 A,comp-a,竞品一体机,竞品品牌,智能客服一体机,"￥4,999.00","¥4,599.00",cny,9,10,第3名,平台日榜,2026-08-05T01:00:00+00:00,曜石黑
csv-row-2,sku-a,竞店 B,comp-b,错误评分样本,竞品品牌,智能客服一体机,4999,4499,CNY,4.5,,8,平台日榜,2026-08-05T01:05:00+00:00,银色
"""
    try:
        seed_subject_catalog(service)

        first = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            csv_text,
            connector_id="file-m6",
            store_id="store-a",
            source_ref="file://m6-competitors.csv",
        )
        replay = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            csv_text,
            connector_id="file-m6",
            store_id="store-a",
            source_ref="file://m6-competitors.csv",
        )

        assert first["total_rows"] == 2
        assert first["imported_count"] == 1
        assert first["idempotent_count"] == 0
        assert first["error_count"] == 1
        assert first["errors"] == [
            {
                "row": 2,
                "field": "rating_scale",
                "code": "field_required",
                "message": "rating_scale is required with rating_value",
            }
        ]
        assert set(first["errors"][0]) == {"row", "field", "code", "message"}

        imported = first["items"][0]
        assert imported["latest_observation"]["subject_price"] == "4999.00"
        assert imported["latest_observation"]["competitor_price"] == "4599.00"
        assert imported["latest_observation"]["currency"] == "CNY"
        assert imported["latest_observation"]["sales_rank"] == 3
        assert imported["identity"]["custom_dimensions"][0]["key"] == "color"
        assert imported["identity"]["custom_dimensions"][0]["value_type"] == "text"
        assert imported["identity"]["custom_dimensions"][0]["value_text"] == "曜石黑"

        assert replay["imported_count"] == 0
        assert replay["idempotent_count"] == 1
        assert replay["error_count"] == 1
        assert replay["items"][0]["match"]["id"] == imported["match"]["id"]
        assert replay["items"][0]["latest_observation"]["id"] == (
            imported["latest_observation"]["id"]
        )
    finally:
        service.close()


def test_failed_csv_row_rolls_back_new_match(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    collision_time = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)
    try:
        seed_subject_catalog(service)
        service.operations.competitive.record(
            TENANT_ID,
            CompetitorObservationCreate(
                connector_id="collision-feed",
                store_id="store-a",
                subject_sku="sku-a",
                competitor_name="旧来源",
                competitor_sku="comp-collision",
                subject_price=Decimal("4999"),
                competitor_price=Decimal("4500"),
                currency="CNY",
                source_type="licensed_provider",
                source_ref="https://licensed.example/collision/old",
                source_id="old-natural-key-source",
                is_estimate=False,
                observed_at=collision_time,
            ),
        )
        csv_text = """source_id,subject_sku,competitor_name,competitor_sku,product_title,subject_price,competitor_price,currency,observed_at
new-source,sku-a,新竞店,comp-collision,触发自然键冲突的竞品,4999,4400,CNY,2026-08-05T02:00:00+00:00
"""

        result = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            csv_text,
            connector_id="collision-feed",
            store_id="store-a",
            source_ref="file://collision.csv",
        )

        assert result["imported_count"] == 0
        assert result["error_count"] == 1
        assert result["errors"] == [
            {
                "row": 1,
                "field": "row",
                "code": "row_conflict",
                "message": "row conflicts with an existing observation",
            }
        ]
        with service.db.connect() as conn:
            rolled_back_matches = conn.execute(
                """
                SELECT COUNT(*) FROM competitive_entity_matches
                WHERE tenant_id=? AND competitor_sku='comp-collision'
                """,
                (TENANT_ID,),
            ).fetchone()[0]
        assert rolled_back_matches == 0
    finally:
        service.close()


def test_csv_rejects_duplicate_canonical_headers(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        with pytest.raises(
            ValueError,
            match="competitive_csv_duplicate_column:subject_sku",
        ):
            service.operations.competitive.import_dataset_csv(
                TENANT_ID,
                "subject_sku,本店SKU\nsku-a,sku-a\n",
                connector_id="file-m6",
                store_id="store-a",
                source_ref="file://duplicate.csv",
            )
    finally:
        service.close()


def test_csv_reports_d014_stale_and_same_version_conflicts(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    header = "source_id,subject_sku,competitor_name,competitor_sku,product_title,subject_price,competitor_price,currency,observed_at\n"
    try:
        seed_subject_catalog(service)
        applied = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            header
            + "d014-row,sku-a,竞店 D,comp-d,竞品 D,4999,4500,CNY,2026-08-05T04:00:00+00:00\n",
            connector_id="file-d014",
            store_id="store-a",
            source_ref="file://d014.csv",
        )
        stale = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            header
            + "d014-row,sku-a,竞店 D,comp-d,竞品 D,4999,4400,CNY,2026-08-05T03:00:00+00:00\n",
            connector_id="file-d014",
            store_id="store-a",
            source_ref="file://d014.csv",
        )
        conflict = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            header
            + "d014-row,sku-a,竞店 D,comp-d,竞品 D,4999,4400,CNY,2026-08-05T04:00:00+00:00\n",
            connector_id="file-d014",
            store_id="store-a",
            source_ref="file://d014.csv",
        )

        assert applied["imported_count"] == 1
        assert stale["errors"] == [
            {
                "row": 1,
                "field": "observed_at",
                "code": "stale_source_version",
                "message": "source version is older than the stored version",
            }
        ]
        assert conflict["errors"] == [
            {
                "row": 1,
                "field": "source_id",
                "code": "source_version_conflict",
                "message": "same source version has different content",
            }
        ]
    finally:
        service.close()


def test_csv_f305_error_does_not_disclose_other_scope(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    csv_text = """source_id,subject_sku,competitor_name,competitor_sku,product_title,subject_price,competitor_price,currency,observed_at
missing-subject,sku-hidden,竞店,comp-hidden,竞品,100,90,CNY,2026-08-05T05:00:00+00:00
"""
    try:
        service.operations.catalog.upsert(
            "tenant-other",
            CatalogItemUpsert(
                connector_id="catalog-feed",
                store_id="store-a",
                item_id="hidden-item",
                sku_id="sku-hidden",
                title="其他租户商品",
                status="active",
                sale_price=Decimal("100"),
                currency="CNY",
                source_updated_at=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
                source_id="hidden-source",
            ),
        )
        result = service.operations.competitive.import_dataset_csv(
            TENANT_ID,
            csv_text,
            connector_id="file-m6",
            store_id="store-a",
            source_ref="file://hidden.csv",
        )

        assert result["errors"] == [
            {
                "row": 1,
                "field": "subject_sku",
                "code": "competitive_subject_sku_unavailable",
                "message": "subject SKU is unavailable",
            }
        ]
    finally:
        service.close()


def test_dataset_http_endpoints_accept_manual_json_and_bom_chinese_csv(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {
        "X-Admin-Id": "admin-test",
        "X-Admin-Key": "test-admin-key-123456",
    }
    with TestClient(app) as client:
        seed_subject_catalog(app.state.agent)
        manual = client.post(
            "/v1/competitive/datasets",
            headers=headers,
            json=dataset_row(source_id="manual-http-1").model_dump(mode="json"),
        )
        assert manual.status_code == 200
        assert manual.json()["match"]["status"] == "pending"

        csv_text = """\ufeff数据ID,本店SKU,竞品店铺,竞品SKU,商品标题,品牌,品类,本店价格,竞品价格,币种,评分,满分,排名,榜单,采集时间,维度.颜色
cn-row-1,sku-a,竞店中文,comp-cn,中文表头竞品,竞品品牌,智能客服一体机,"￥4,999","￥4,388",CNY,4.5,5,第2名,平台日榜,2026-08-05T03:00:00+00:00,银色
"""
        imported = client.post(
            "/v1/competitive/datasets/import",
            params={
                "connector_id": "csv-http",
                "store_id": "store-a",
                "source_ref": "file://cn.csv",
            },
            headers={**headers, "Content-Type": "text/csv; charset=utf-8"},
            content=csv_text.encode("utf-8"),
        )
        assert imported.status_code == 200
        assert imported.json()["imported_count"] == 1
        assert imported.json()["items"][0]["identity"]["custom_dimensions"][0][
            "value_text"
        ] == "银色"

        invalid_encoding = client.post(
            "/v1/competitive/datasets/import",
            params={
                "connector_id": "csv-http",
                "store_id": "store-a",
                "source_ref": "file://invalid.csv",
            },
            headers={**headers, "Content-Type": "text/csv"},
            content=b"\xff\xfe\x00\x00",
        )
        assert invalid_encoding.status_code == 422
        assert invalid_encoding.json()["detail"] == "competitive_csv_encoding_invalid"
