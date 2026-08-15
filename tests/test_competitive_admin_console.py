from pathlib import Path


def admin_console() -> str:
    return (
        Path(__file__).resolve().parents[1] / "docs" / "admin-console.html"
    ).read_text(encoding="utf-8")


def test_competitive_admin_has_ingestion_query_and_error_surfaces() -> None:
    html = admin_console()

    for element_id in (
        "competitiveDatasetForm",
        "competitiveDatasetFile",
        "competitiveImportForm",
        "competitiveImportErrors",
        "competitiveDatasetQueryForm",
        "competitiveDatasetRows",
        "competitiveDimensionRows",
        "addCompetitiveDimension",
        "competitiveQueryDimensionKey",
        "competitiveQueryDimensionType",
        "competitiveQueryDimensionValue",
        "competitiveQueryRankScope",
    ):
        assert f'id="{element_id}"' in html

    assert "'/v1/competitive/datasets'" in html
    assert "'/v1/competitive/datasets/import'" in html
    assert "'/v1/competitive/datasets/query'" in html
    assert "rating_value" in html
    assert "rating_scale" in html
    assert "normalized_rating" in html
    assert "sales_rank" in html
    assert "rank_scope" in html
    assert "custom_dimensions" in html
    assert "price_min:'competitiveQueryPriceMin'" in html
    assert "price_max:'competitiveQueryPriceMax'" in html
    assert "result.accepted_rows" in html
    assert "result.rejected_rows" in html
    assert "body:await file.arrayBuffer()" in html
    assert "competitor_price_min:'competitiveQueryPriceMin'" not in html


def test_competitive_dataset_results_reuse_match_decision_dialog() -> None:
    html = admin_console()

    assert "renderCompetitiveMatchActions" in html
    assert "openCompetitiveMatchDialog" in html
    assert "data-competitive-match" in html
    assert "competitive-dataset-table" in html
