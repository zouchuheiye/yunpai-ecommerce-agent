from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ecommerce_agent.api import create_app
from ecommerce_agent.business import (
    CatalogItemUpsert,
    CompetitiveCustomDimension,
    CompetitiveDatasetQuery,
    CompetitiveDatasetRow,
    CompetitiveDimensionFilter,
    CompetitiveMatchTransition,
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
            source_updated_at=datetime(2026, 8, 5, tzinfo=UTC),
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
        "observed_at": datetime(2026, 8, 5, 1, tzinfo=UTC),
    }
    values.update(updates)
    return CompetitiveDatasetRow(**values)


def test_dataset_write_is_pending_persisted_and_idempotent(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)

        first = service.operations.competitive.record_dataset(TENANT_ID, dataset_row())
        replay = service.operations.competitive.record_dataset(TENANT_ID, dataset_row())

        assert first["match"]["status"] == "pending"
        assert first["match"]["subject_identity"]["title"] == "云湃智能客服一体机 YP-100"
        assert first["identity"]["custom_dimensions"][0]["value_number"] == "12.5"
        assert first["latest_observation"]["normalized_rating"] == "4.50"
        assert first["latest_observation"]["entity_match_id"] == first["match"]["id"]
        assert first["actionable"] is False
        assert first["write_status"] == "applied"
        assert replay["write_status"] == "idempotent"
        assert replay["match"]["id"] == first["match"]["id"]
        with service.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM competitive_entity_matches").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM competitor_observations").fetchone()[0] == 1
    finally:
        service.close()


def test_dataset_write_rolls_back_match_when_observation_conflicts(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)
        value = dataset_row()
        service.operations.competitive.record(
            TENANT_ID,
            CompetitorObservationCreate(
                connector_id=value.connector_id,
                store_id=value.store_id,
                subject_sku=value.subject_sku,
                competitor_name="其他竞店",
                competitor_sku="other-comp",
                subject_price=Decimal("4999"),
                competitor_price=Decimal("3999"),
                currency="CNY",
                source_type=value.source_type,
                source_ref=value.source_ref,
                is_estimate=False,
                observed_at=value.observed_at,
                source_id=value.source_id,
            ),
        )

        try:
            service.operations.competitive.record_dataset(TENANT_ID, value)
        except ValueError as exc:
            assert str(exc) == "source_version_conflict"
        else:
            raise AssertionError("dataset write should reject a same-version conflict")

        with service.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM competitive_entity_matches").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM competitor_observations").fetchone()[0] == 1
    finally:
        service.close()


def test_dataset_http_endpoint_uses_the_same_canonical_write(tmp_path) -> None:
    settings = make_settings(tmp_path)
    service = AgentService(settings)
    seed_subject_catalog(service)
    service.close()
    app = create_app(settings)
    headers = {
        "X-Tenant-ID": TENANT_ID,
        "X-Admin-ID": "admin-test",
        "X-Admin-Key": "test-admin-key-123456",
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/competitive/datasets",
            headers=headers,
            json=dataset_row().model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert response.json()["match"]["status"] == "pending"
    assert response.json()["latest_observation"]["normalized_rating"] == "4.50"


def approve(service: AgentService, match_id: str) -> None:
    service.operations.competitive.transition_entity_match(
        TENANT_ID,
        match_id,
        CompetitiveMatchTransition(
            target_status="approved",
            expected_record_version=1,
            note="查询测试确认商品身份与规格一致",
        ),
        actor="reviewer-a",
    )


def query_dimensions() -> list[CompetitiveCustomDimension]:
    return [
        CompetitiveCustomDimension(
            key="color", label="颜色", value_type="text", value_text="曜石黑"
        ),
        CompetitiveCustomDimension(
            key="battery_hours",
            label="续航",
            value_type="number",
            value_number=Decimal("12.5"),
        ),
        CompetitiveCustomDimension(
            key="voice_enabled",
            label="语音",
            value_type="boolean",
            value_boolean=True,
        ),
    ]


def test_dataset_query_combines_latest_rating_rank_and_typed_dimensions(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)
        common = {
            "custom_dimensions": query_dimensions(),
            "brand": "云湃",
            "model": "YP-100",
            "category": "智能客服一体机",
            "gtin": "06912345678901",
            "rank_scope": "平台日榜",
        }
        first = service.operations.competitive.record_dataset(
            TENANT_ID,
            dataset_row(source_id="query-a-v1", competitor_sku="comp-a", **common),
        )
        approve(service, first["match"]["id"])
        latest = service.operations.competitive.record_dataset(
            TENANT_ID,
            dataset_row(
                source_id="query-a-v2",
                competitor_sku="comp-a",
                competitor_price=Decimal("4300"),
                observed_at=datetime(2026, 8, 5, 2, tzinfo=UTC),
                **common,
            ),
        )
        second = service.operations.competitive.record_dataset(
            TENANT_ID,
            dataset_row(
                source_id="query-b-v1",
                competitor_sku="comp-b",
                competitor_price=Decimal("4400"),
                rating_value=Decimal("4.5"),
                rating_scale=Decimal("5"),
                sales_rank=5,
                **common,
            ),
        )
        approve(service, second["match"]["id"])
        service.operations.competitive.record_dataset(
            TENANT_ID,
            dataset_row(source_id="query-pending", competitor_sku="comp-pending", **common),
        )

        result = service.operations.competitive.query_datasets(
            TENANT_ID,
            CompetitiveDatasetQuery(
                category="智能客服一体机",
                brand="云湃",
                currency="CNY",
                price_min=Decimal("4200"),
                price_max=Decimal("4500"),
                rating_min=Decimal("4.5"),
                rating_max=Decimal("4.5"),
                sales_rank_min=3,
                sales_rank_max=5,
                rank_scope="平台日榜",
                status="approved",
                custom_dimensions=[
                    CompetitiveDimensionFilter(
                        key="color", value_type="text", value_text="曜石黑"
                    ),
                    CompetitiveDimensionFilter(
                        key="battery_hours",
                        value_type="number",
                        value_number=Decimal("12.5"),
                    ),
                    CompetitiveDimensionFilter(
                        key="voice_enabled", value_type="boolean", value_boolean=True
                    ),
                ],
            ),
        )

        assert result["count"] == 2
        assert {item["match"]["id"] for item in result["items"]} == {
            first["match"]["id"],
            second["match"]["id"],
        }
        comp_a = next(item for item in result["items"] if item["match"]["id"] == first["match"]["id"])
        assert comp_a["latest_observation"]["id"] == latest["latest_observation"]["id"]
        assert all(item["latest_observation"]["normalized_rating"] == "4.50" for item in result["items"])
        assert service.operations.competitive.query_datasets("tenant-other", CompetitiveDatasetQuery()) == {"count": 0, "items": []}
    finally:
        service.close()


def test_dataset_query_management_and_analysis_gates_are_separate(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)
        pending = service.operations.competitive.record_dataset(
            TENANT_ID, dataset_row(source_id="pending-only", competitor_sku="comp-pending")
        )

        management = service.operations.competitive.query_datasets(
            TENANT_ID, CompetitiveDatasetQuery(status="pending")
        )
        actionable = service.operations.competitive.query_actionable_datasets(
            TENANT_ID, CompetitiveDatasetQuery(status="rejected")
        )
        analysis = service.operations.competitive.analyze_prices(
            TENANT_ID, "sku-a", store_id="store-a"
        )
        assert management["items"][0]["match"]["id"] == pending["match"]["id"]
        assert management["items"][0]["actionable"] is False
        assert actionable == {"count": 0, "items": []}
        assert analysis["observations"] == []
        assert analysis["summary"]["competitors"] == 0
        assert analysis["summary"]["unverified_competitors"] == 1
    finally:
        service.close()


def test_dataset_query_uses_created_at_to_break_equal_source_time_ties(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_subject_catalog(service)
        first = service.operations.competitive.record_dataset(
            TENANT_ID, dataset_row(source_id="tie-a", competitor_sku="comp-tie")
        )
        second = service.operations.competitive.record_dataset(
            TENANT_ID,
            dataset_row(
                source_id="tie-b",
                connector_id="query-feed-second",
                competitor_sku="comp-tie",
                competitor_price=Decimal("4200"),
            ),
        )

        result = service.operations.competitive.query_datasets(
            TENANT_ID, CompetitiveDatasetQuery(subject_sku="sku-a")
        )

        assert first["match"]["id"] == second["match"]["id"]
        assert result["items"][0]["latest_observation"]["id"] == second["latest_observation"]["id"]
    finally:
        service.close()


def test_dataset_query_contract_and_http_gate_override(tmp_path) -> None:
    with pytest.raises(ValidationError, match="currency is required"):
        CompetitiveDatasetQuery(price_min=Decimal("100"))
    with pytest.raises(ValidationError, match="rank_scope is required"):
        CompetitiveDatasetQuery(sales_rank_max=5)
    with pytest.raises(ValidationError, match="rating minimum cannot exceed maximum"):
        CompetitiveDatasetQuery(rating_min=Decimal("4.5"), rating_max=Decimal("4"))

    app = create_app(make_settings(tmp_path))
    headers = {
        "X-Tenant-ID": TENANT_ID,
        "X-Admin-ID": "admin-test",
        "X-Admin-Key": "test-admin-key-123456",
    }
    with TestClient(app) as client:
        assert client.post(
            "/v1/competitive/datasets/query",
            headers=headers,
            json={"status": "pending", "approved_only": False},
        ).status_code == 422
        assert client.post(
            "/v1/competitive/datasets/query",
            headers=headers,
            json={"status": "pending"},
        ).status_code == 200
