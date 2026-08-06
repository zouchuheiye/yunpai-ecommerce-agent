from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
