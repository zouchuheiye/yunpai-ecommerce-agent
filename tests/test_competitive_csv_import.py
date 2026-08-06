from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from ecommerce_agent.api import create_app
from ecommerce_agent.business import CatalogItemUpsert
from ecommerce_agent.service import AgentService

from conftest import make_settings


TENANT_ID = "tenant-test"
CSV_CONTEXT = {
    "connector_id": "csv-feed",
    "store_id": "store-a",
    "source_ref": "virtual-competitors.csv",
    "source_type": "virtual",
}
CSV_HEADER = (
    "source_id,subject_sku,competitor_name,competitor_sku,product_title,"
    "subject_price,competitor_price,currency,rating_value,rating_scale,"
    "sales_rank,rank_scope,observed_at"
)


def seed_catalog(service: AgentService, tenant_id: str = TENANT_ID) -> None:
    service.operations.catalog.upsert(
        tenant_id,
        CatalogItemUpsert(
            connector_id="catalog-feed",
            store_id="store-a",
            item_id="item-a",
            sku_id="sku-a",
            title="云湃智能客服一体机",
            status="active",
            sale_price=Decimal("4999"),
            currency="CNY",
            attributes={"brand": "云湃", "model": "YP-100"},
            source_updated_at=datetime(2026, 8, 5, tzinfo=UTC),
            source_id="catalog-source-a",
        ),
    )


def csv_row(
    source_id: str,
    competitor_sku: str,
    price: str,
    observed_at: str = "2026-08-06T00:00:00+00:00",
) -> str:
    return (
        f"{source_id},sku-a,Shop {competitor_sku},{competitor_sku},Virtual {competitor_sku},"
        f"4999,{price},CNY,4.5,5,3,Daily,{observed_at}"
    )


def test_csv_import_skips_invalid_rows_and_replays_idempotently(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        content = "\n".join(
            [
                CSV_HEADER,
                csv_row("row-a", "comp-a", "4599"),
                csv_row("row-b", "comp-b", "not-money"),
                csv_row("row-c", "comp-c", "4399"),
            ]
        )

        first = service.operations.competitive.import_dataset_csv(
            TENANT_ID, content, **CSV_CONTEXT
        )
        replay = service.operations.competitive.import_dataset_csv(
            TENANT_ID, content, **CSV_CONTEXT
        )

        assert {key: first[key] for key in (
            "total_rows", "accepted_rows", "rejected_rows", "applied", "idempotent", "conflicts"
        )} == {
            "total_rows": 3,
            "accepted_rows": 2,
            "rejected_rows": 1,
            "applied": 2,
            "idempotent": 0,
            "conflicts": 0,
        }
        assert len(first["records"]) == 2
        assert first["errors"] == [
            {
                "row": 2,
                "field": "competitor_price",
                "code": "decimal_invalid",
                "message": "competitor_price must be a decimal number",
            }
        ]
        assert replay["applied"] == 0
        assert replay["idempotent"] == 2
        assert replay["rejected_rows"] == 1
    finally:
        service.close()


def test_csv_import_reports_d014_stale_and_same_version_conflicts(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service)
        initial = "\n".join([CSV_HEADER, csv_row("row-a", "comp-a", "4599")])
        service.operations.competitive.import_dataset_csv(
            TENANT_ID, initial, **CSV_CONTEXT
        )

        stale = "\n".join(
            [CSV_HEADER, csv_row("row-a", "comp-a", "4599", "2026-08-05T00:00:00+00:00")]
        )
        conflict = "\n".join([CSV_HEADER, csv_row("row-a", "comp-a", "4499")])

        stale_result = service.operations.competitive.import_dataset_csv(
            TENANT_ID, stale, **CSV_CONTEXT
        )
        conflict_result = service.operations.competitive.import_dataset_csv(
            TENANT_ID, conflict, **CSV_CONTEXT
        )

        assert stale_result["errors"][0]["code"] == "stale_source_version"
        assert stale_result["errors"][0]["field"] == "observed_at"
        assert conflict_result["errors"][0]["code"] == "source_version_conflict"
        assert conflict_result["errors"][0]["field"] == "source_id"
        assert conflict_result["conflicts"] == 1
    finally:
        service.close()


def test_csv_import_f305_error_does_not_disclose_other_tenant(tmp_path) -> None:
    service = AgentService(make_settings(tmp_path))
    try:
        seed_catalog(service, "tenant-other")
        content = "\n".join([CSV_HEADER, csv_row("row-a", "comp-a", "4599")])

        result = service.operations.competitive.import_dataset_csv(
            TENANT_ID, content, **CSV_CONTEXT
        )

        assert result["accepted_rows"] == 0
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


def test_csv_import_endpoint_accepts_bom_and_rejects_invalid_utf8(tmp_path) -> None:
    settings = make_settings(tmp_path)
    service = AgentService(settings)
    seed_catalog(service)
    service.close()
    app = create_app(settings)
    headers = {
        "X-Tenant-ID": TENANT_ID,
        "X-Admin-ID": "admin-test",
        "X-Admin-Key": "test-admin-key-123456",
        "Content-Type": "text/csv",
    }
    query = {
        "connector_id": "csv-feed",
        "store_id": "store-a",
        "source_ref": "virtual-competitors.csv",
        "source_type": "virtual",
    }
    content = "\ufeff" + "\n".join(
        [CSV_HEADER, csv_row("row-a", "comp-a", "4599")]
    )

    with TestClient(app) as client:
        accepted = client.post(
            "/v1/competitive/datasets/import",
            headers=headers,
            params=query,
            content=content.encode("utf-8"),
        )
        rejected = client.post(
            "/v1/competitive/datasets/import",
            headers=headers,
            params=query,
            content=b"\xff\xfe\x00",
        )

    assert accepted.status_code == 200
    assert accepted.json()["accepted_rows"] == 1
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "competitive_csv_encoding_invalid"
