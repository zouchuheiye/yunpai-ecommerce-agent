from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
)
from ecommerce_agent.service import AgentService

from conftest import make_settings


TENANT_ID = "tenant-test"
OBSERVED_AT = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)


def seed_catalog(service: AgentService, *, tenant_id: str = TENANT_ID) -> None:
    service.operations.catalog.upsert(
        tenant_id,
        CatalogItemUpsert(
            connector_id="catalog-feed",
            store_id="store-a",
            item_id="item-a",
            sku_id="sku-a",
            title="云湃智能客服一体机",
            status="active",
            sale_price=Decimal("5000"),
            currency="CNY",
            attributes={
                "brand": "云湃",
                "model": "YP-100",
                "category": "智能客服一体机",
                "gtin": "06912345678901",
            },
            source_updated_at=OBSERVED_AT - timedelta(hours=1),
            source_id=f"catalog-{tenant_id}",
        ),
    )


def row(source_id: str, competitor_sku: str, **updates) -> CompetitiveDatasetRow:
    values = {
        "connector_id": "query-feed",
        "store_id": "store-a",
        "source_ref": f"https://licensed.example/{source_id}",
        "source_type": "licensed_provider",
        "source_id": source_id,
        "subject_sku": "sku-a",
        "competitor_name": f"竞店 {competitor_sku}",
        "competitor_sku": competitor_sku,
        "product_title": f"竞品 {competitor_sku}",
        "brand": "云湃",
        "model": "YP-100",
        "category": "智能客服一体机",
        "gtin": "06912345678901",
        "custom_dimensions": [
            CompetitiveCustomDimension(
                key="color",
                label="颜色",
                value_type="text",
                value_text="曜石黑",
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
        ],
        "subject_price": Decimal("5000"),
        "competitor_price": Decimal("4300"),
        "currency": "CNY",
        "rating_value": Decimal("9"),
        "rating_scale": Decimal("10"),
        "sales_rank": 3,
        "rank_scope": "平台日榜",
        "is_estimate": False,
        "observed_at": OBSERVED_AT,
    }
    values.update(updates)
    return CompetitiveDatasetRow(**values)


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


def test_query_combines_latest_facts_rating_and_typed_dimensions(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        first = service.operations.competitive.record_dataset(
            TENANT_ID, row("query-a-v1", "comp-a", competitor_price=Decimal("4100"))
        )
        approve(service, first["match"]["id"])
        latest = service.operations.competitive.record_dataset(
            TENANT_ID,
            row(
                "query-a-v2",
                "comp-a",
                competitor_price=Decimal("4300"),
                observed_at=OBSERVED_AT + timedelta(hours=1),
            ),
        )
        second = service.operations.competitive.record_dataset(
            TENANT_ID,
            row(
                "query-b-v1",
                "comp-b",
                competitor_price=Decimal("4400"),
                rating_value=Decimal("4.5"),
                rating_scale=Decimal("5"),
                sales_rank=5,
            ),
        )
        approve(service, second["match"]["id"])
        service.operations.competitive.record_dataset(
            TENANT_ID,
            row("query-pending", "comp-pending", competitor_price=Decimal("4350")),
        )

        result = service.operations.competitive.query_datasets(
            TENANT_ID,
            CompetitiveDatasetQuery(
                store_id="store-a",
                category="智能客服一体机",
                brand="云湃",
                currency="CNY",
                competitor_price_min=Decimal("4200"),
                competitor_price_max=Decimal("4500"),
                rating_min=Decimal("4.5"),
                rating_max=Decimal("4.5"),
                sales_rank_min=3,
                sales_rank_max=5,
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
                        key="voice_enabled",
                        value_type="boolean",
                        value_boolean=True,
                    ),
                ],
            ),
        )

        assert result["count"] == 2
        assert {item["match"]["id"] for item in result["items"]} == {
            first["match"]["id"],
            second["match"]["id"],
        }
        comp_a = next(
            item for item in result["items"] if item["match"]["id"] == first["match"]["id"]
        )
        assert comp_a["latest_observation"]["id"] == latest["latest_observation"]["id"]
        assert comp_a["latest_observation"]["competitor_price"] == "4300"
        assert comp_a["latest_observation"]["normalized_rating"] == "4.50"
        assert all(item["actionable"] for item in result["items"])
    finally:
        service.close()


def test_management_status_filter_and_analysis_are_separate(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        pending = service.operations.competitive.record_dataset(
            TENANT_ID, row("pending-only", "comp-pending")
        )

        management = service.operations.competitive.query_datasets(
            TENANT_ID,
            CompetitiveDatasetQuery(status="pending"),
        )
        analysis_query = service.operations.competitive.query_actionable_datasets(
            TENANT_ID,
            CompetitiveDatasetQuery(),
        )
        price_analysis = service.operations.competitive.analyze_prices(
            TENANT_ID, "sku-a", store_id="store-a"
        )

        assert management["items"][0]["match"]["id"] == pending["match"]["id"]
        assert management["items"][0]["actionable"] is False
        assert analysis_query == {"count": 0, "items": []}
        assert price_analysis["observations"] == []
        assert price_analysis["summary"]["competitors"] == 0
    finally:
        service.close()


def test_latest_observation_uses_created_at_for_equal_source_times(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        first = service.operations.competitive.record_dataset(
            TENANT_ID, row("tie-first", "comp-tie", competitor_price=Decimal("4100"))
        )
        second = service.operations.competitive.record_dataset(
            TENANT_ID,
            row(
                "tie-second",
                "comp-tie",
                connector_id="query-feed-second",
                competitor_price=Decimal("4200"),
            ),
        )

        result = service.operations.competitive.query_datasets(
            TENANT_ID,
            CompetitiveDatasetQuery(subject_sku="sku-a"),
        )

        assert first["match"]["id"] == second["match"]["id"]
        assert result["count"] == 1
        assert result["items"][0]["latest_observation"]["id"] == (
            second["latest_observation"]["id"]
        )
        assert result["items"][0]["latest_observation"]["competitor_price"] == "4200"
    finally:
        service.close()


def test_new_source_version_supersedes_old_approved_match(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        first = service.operations.competitive.record_dataset(
            TENANT_ID,
            row("versioned-source", "comp-versioned", model="YP-100"),
        )
        approve(service, first["match"]["id"])
        latest = service.operations.competitive.record_dataset(
            TENANT_ID,
            row(
                "versioned-source",
                "comp-versioned",
                model="YP-200",
                product_title="竞品新型号 YP-200",
                competitor_price=Decimal("4200"),
                observed_at=OBSERVED_AT + timedelta(hours=1),
            ),
        )

        management = service.operations.competitive.query_datasets(
            TENANT_ID, CompetitiveDatasetQuery(subject_sku="sku-a")
        )
        analysis = service.operations.competitive.query_actionable_datasets(
            TENANT_ID, CompetitiveDatasetQuery(subject_sku="sku-a")
        )

        assert latest["match"]["status"] == "pending"
        assert management["count"] == 1
        assert management["items"][0]["match"]["id"] == latest["match"]["id"]
        assert analysis == {"count": 0, "items": []}
    finally:
        service.close()


def test_query_requires_currency_for_price_ranges_and_valid_bounds() -> None:
    with pytest.raises(ValidationError, match="currency is required"):
        CompetitiveDatasetQuery(competitor_price_min=Decimal("100"))
    with pytest.raises(ValidationError, match="rating minimum cannot exceed maximum"):
        CompetitiveDatasetQuery(rating_min=Decimal("4.5"), rating_max=Decimal("4"))


def test_query_is_tenant_isolated_and_http_does_not_accept_gate_override(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {
        "X-Admin-Id": "admin-test",
        "X-Admin-Key": "test-admin-key-123456",
    }
    with TestClient(app) as client:
        seed_catalog(app.state.agent)
        created = app.state.agent.operations.competitive.record_dataset(
            TENANT_ID, row("http-query", "comp-http")
        )
        response = client.post(
            "/v1/competitive/datasets/query",
            headers=headers,
            json={"status": "pending", "approved_only": False},
        )
        assert response.status_code == 422

        response = client.post(
            "/v1/competitive/datasets/query",
            headers=headers,
            json={"status": "pending"},
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["match"]["id"] == created["match"]["id"]

        other_tenant = app.state.agent.operations.competitive.query_datasets(
            "tenant-other", CompetitiveDatasetQuery()
        )
        assert other_tenant == {"count": 0, "items": []}
