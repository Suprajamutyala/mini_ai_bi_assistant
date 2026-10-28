import pytest
from app import create_app


@pytest.fixture()
def client(tmp_path):
    project_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "test_sales.db"),
        "CSV_PATH": str(project_root / "data" / "sales_data.csv"),
        "USE_LLM": False,
    })
    return app.test_client()


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_total_revenue_returns_number(client):
    response = ask(client, "What is the total revenue?")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["intent"] == "total_revenue"
    assert body["supporting_data"][0]["value"] > 0


def test_top_product_returns_supporting_data(client):
    body = ask(client, "Which product generated the highest revenue?").get_json()
    assert body["ok"] is True
    assert body["supporting_data"][0]["product"]
    assert body["supporting_data"][0]["revenue"] > 0


def test_monthly_trend_returns_months(client):
    body = ask(client, "Show the monthly revenue trend.").get_json()
    assert body["ok"] is True
    assert len(body["supporting_data"]) >= 1
    assert "month" in body["supporting_data"][0]


def test_top_region_returns_region(client):
    body = ask(client, "Which region generated the highest sales?").get_json()
    assert body["ok"] is True
    assert body["supporting_data"][0]["region"]


def test_month_comparison(client):
    body = ask(
        client, "How did revenue change between January and February 2026?"
    ).get_json()
    assert body["ok"] is True
    assert body["intent"] == "revenue_change"
    assert len(body["supporting_data"]) == 3


def test_unsupported_question_is_handled(client):
    body = ask(client, "What is the weather tomorrow?").get_json()
    assert body["ok"] is False
    assert body["suggestions"]


def test_empty_question_is_rejected(client):
    response = ask(client, "   ")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_missing_months_asks_for_clarification(client):
    body = ask(client, "How did revenue change between two months?").get_json()
    assert body["ok"] is False
    assert "specify both months" in body["answer"].lower()


def test_data_quality_endpoint(client):
    body = client.get("/api/data-quality").get_json()
    assert body["total_rows"] == 51
    assert body["valid_completed_rows"] < body["total_rows"]


def test_overview_endpoint(client):
    body = client.get("/api/overview").get_json()
    assert body["total_rows"] == 51
    assert body["revenue"] > 0
    assert len(body["monthly_trend"]) >= 1


def test_revenue_by_category(client):
    body = ask(client, "Show revenue by category.").get_json()
    assert body["ok"] is True
    assert body["intent"] == "revenue_by_category"
    assert body["supporting_data"][0]["category"]


def test_revenue_by_channel(client):
    body = ask(client, "Compare revenue by sales channel.").get_json()
    assert body["ok"] is True
    assert body["intent"] == "revenue_by_channel"
    assert body["supporting_data"][0]["sales_channel"]


def test_average_order_value(client):
    body = ask(client, "What is the average order value?").get_json()
    assert body["ok"] is True
    assert body["intent"] == "average_order_value"
    assert body["supporting_data"][0]["value"] > 0


def test_most_sold_product(client):
    body = ask(client, "Which product sold the most units?").get_json()
    assert body["ok"] is True
    assert body["intent"] == "most_sold_product"
    assert body["supporting_data"][0]["units_sold"] > 0


def test_discount_summary(client):
    body = ask(client, "What is the total discount amount?").get_json()
    assert body["ok"] is True
    assert body["intent"] == "discount_summary"
    assert body["supporting_data"][0]["value"] >= 0
