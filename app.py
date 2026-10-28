import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "sales.db"
DEFAULT_CSV_PATH = BASE_DIR / "data" / "sales_data.csv"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT UNIQUE,
    order_date TEXT,
    product TEXT,
    category TEXT,
    region TEXT,
    sales_channel TEXT,
    quantity INTEGER,
    unit_price REAL,
    discount_amount REAL,
    status TEXT,
    validation_status TEXT NOT NULL,
    validation_reason TEXT
);
"""

# All analytics use this fixed filter. Invalid and returned rows remain in the
# database for traceability, but are excluded from revenue calculations.
VALID_SALES_FILTER = """
status = 'Completed'
AND validation_status = 'valid'
"""

MONTHS = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}


@contextmanager
def get_connection(db_path):
    """Open a SQLite connection, return dictionary-like rows, and always close it."""
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def clean_and_validate_row(row):
    """Normalize one CSV row and explain whether it can be used for analytics."""
    record = {str(k).strip(): (None if pd.isna(v) else str(v).strip())
              for k, v in row.items()}

    transaction_id = record.get("Transaction_Id") or None
    raw_date = record.get("Order_date")
    order_date = None
    if raw_date:
        try:
            order_date = datetime.strptime(raw_date, "%d-%m-%Y").date().isoformat()
        except ValueError:
            pass

    def number(field, cast=float):
        value = record.get(field)
        if value in (None, ""):
            return None
        try:
            return cast(value)
        except (TypeError, ValueError):
            return None

    quantity = number("Quantity", int)
    unit_price = number("Unit_price", float)
    discount = number("Discount_amount", float)
    status = (record.get("Status") or "").strip().title()

    reasons = []
    if not transaction_id:
        reasons.append("missing transaction ID")
    if not order_date:
        reasons.append("missing or invalid order date")
    if not record.get("Product"):
        reasons.append("missing product")
    if not record.get("Region"):
        reasons.append("missing region")
    if quantity is None:
        reasons.append("missing or invalid quantity")
    elif quantity <= 0:
        reasons.append("quantity must be greater than zero")
    if unit_price is None:
        reasons.append("missing or invalid unit price")
    elif unit_price <= 0:
        reasons.append("unit price must be greater than zero")
    if discount is None:
        reasons.append("missing or invalid discount")
    elif discount < 0:
        reasons.append("discount cannot be negative")
    if status != "Completed":
        reasons.append("transaction is not completed")

    # Returned and incomplete transactions are kept, but cannot contribute to
    # the business metrics. "valid" here means valid for the analytics filter.
    validation_status = "valid" if not reasons else "excluded"
    return (
        transaction_id, order_date, record.get("Product"), record.get("Category"),
        record.get("Region"), record.get("Sales_channel"), quantity, unit_price,
        discount, status or "Unknown", validation_status,
        "; ".join(reasons) if reasons else None
    )


def load_csv_to_database(csv_path, db_path):
    """Load the supplied CSV into SQLite, replacing the table's imported rows."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Sales CSV not found: {csv_path}")

    dataframe = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    dataframe.columns = [str(c).strip() for c in dataframe.columns]

    required = {
        "Transaction_Id", "Order_date", "Product", "Category", "Region",
        "Sales_channel", "Quantity", "Unit_price", "Discount_amount", "Status"
    }
    missing_columns = required - set(dataframe.columns)
    if missing_columns:
        raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing_columns)))

    with get_connection(db_path) as connection:
        connection.execute(SCHEMA_SQL)
        connection.execute("DELETE FROM transactions")
        for _, row in dataframe.iterrows():
            cleaned = clean_and_validate_row(row.to_dict())
            connection.execute(
                """INSERT OR IGNORE INTO transactions
                (transaction_id, order_date, product, category, region, sales_channel,
                 quantity, unit_price, discount_amount, status, validation_status,
                 validation_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                cleaned
            )
        connection.commit()


def month_to_iso(text):
    """Convert month names such as 'January' or 'Jan 2026' to YYYY-MM."""
    if not text:
        return None
    cleaned = text.lower().strip()
    match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", cleaned)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    year_match = re.search(r"\b(20\d{2})\b", cleaned)
    year = year_match.group(1) if year_match else "2026"
    for month_name, month_num in MONTHS.items():
        if re.search(r"\b" + re.escape(month_name) + r"\b", cleaned):
            return f"{year}-{month_num}"
    return None


def rule_based_interpretation(question):
    """Safe fallback: map common wording to one of the supported analytics operations."""
    q = question.lower().strip()
    if any(word in q for word in ["average order", "average transaction", "average sale"]):
        return {"intent": "average_order_value"}
    if any(word in q for word in ["how many transaction", "how many completed transaction", "number of transaction", "transaction count", "total orders", "number of orders"]):
        return {"intent": "transaction_count"}
    if any(word in q for word in ["discount", "discount amount", "discounts given"]):
        return {"intent": "discount_summary"}
    if any(word in q for word in ["sales channel", "by channel", "channel performance", "online vs retail", "channel generated"]):
        return {"intent": "revenue_by_channel"}
    if any(word in q for word in ["category", "categories", "by category", "category performance"]):
        return {"intent": "revenue_by_category"}
    if any(word in q for word in ["most units", "most sold", "most quantity", "units sold", "highest quantity"]):
        return {"intent": "most_sold_product"}
    if any(word in q for word in ["monthly trend", "month by month", "revenue trend", "monthly revenue", "trend by month"]):
        return {"intent": "monthly_trend"}
    if any(word in q for word in ["highest sales", "top region", "which region", "region generated", "best region", "regional performance"]):
        return {"intent": "top_region"}
    if any(word in q for word in ["highest revenue", "top product", "best selling product", "which product", "product generated", "top 5 product"]):
        return {"intent": "top_product"}
    if any(word in q for word in ["between", "change", "compared", "difference"]):
        months = re.findall(
            r"\b(?:january|jan|february|feb|march|mar|april|apr|may|june|jun|"
            r"july|jul|august|aug|september|sept|sep|october|oct|november|nov|"
            r"december|dec)\b(?:\s+20\d{2})?|\b20\d{2}-(?:0[1-9]|1[0-2])\b",
            q
        )
        parsed = [month_to_iso(m) for m in months]
        parsed = [m for m in parsed if m]
        result = {"intent": "revenue_change"}
        if len(parsed) >= 2:
            result["month_a"], result["month_b"] = parsed[0], parsed[1]
        return result
    if any(word in q for word in ["total revenue", "revenue overall", "overall revenue", "how much revenue", "total sales", "total net sales"]):
        return {"intent": "total_revenue"}
    return {"intent": "unsupported"}


def ai_interpretation(question):
    """
    Optional LLM interpretation. The model may choose only a supported intent;
    the application never executes SQL returned by the model.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return rule_based_interpretation(question), "rule-based fallback"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify a sales analytics question. Return JSON only with keys "
                        "intent, month_a, month_b. Allowed intents: total_revenue, top_product, "
                        "monthly_trend, top_region, revenue_change, revenue_by_category, "
                        "revenue_by_channel, transaction_count, average_order_value, "
                        "most_sold_product, discount_summary, unsupported. "
                        "For revenue_change, month_a and month_b must be YYYY-MM if identifiable, "
                        "otherwise null. Never produce SQL. Never invent months."
                    )
                },
                {"role": "user", "content": question}
            ]
        )
        parsed = json.loads(response.choices[0].message.content)
        allowed = {"total_revenue", "top_product", "monthly_trend", "top_region",
                   "revenue_change", "revenue_by_category", "revenue_by_channel",
                   "transaction_count", "average_order_value", "most_sold_product",
                   "discount_summary", "unsupported"}
        intent = parsed.get("intent")
        if intent not in allowed:
            return rule_based_interpretation(question), "rule-based fallback"
        if intent == "revenue_change":
            parsed["month_a"] = month_to_iso(str(parsed.get("month_a") or ""))
            parsed["month_b"] = month_to_iso(str(parsed.get("month_b") or ""))
        return parsed, "LLM"
    except Exception:
        # Do not fail the entire application if the optional AI service is down.
        return rule_based_interpretation(question), "rule-based fallback"


def run_analytics(question, db_path, interpretation=None, interpretation_source="rule-based fallback"):
    """Execute a fixed, allow-listed SQL query and return a structured response."""
    interpreted = interpretation or rule_based_interpretation(question)
    intent = interpreted.get("intent")

    if intent == "unsupported":
        return {
            "ok": False,
            "answer": "I could not identify that as a supported sales question.",
            "supporting_data": [],
            "suggestions": [
                "What is the total revenue?",
                "Which product generated the highest revenue?",
                "Show the monthly revenue trend.",
                "Which region generated the highest sales?",
                "How did revenue change between January and February 2026?",
                "Show revenue by category.",
                "Compare revenue by sales channel.",
                "How many completed transactions are included?",
                "What is the average order value?",
                "Which product sold the most units?",
                "What is the total discount amount?"
            ],
            "interpretation_source": interpretation_source
        }

    with get_connection(db_path) as connection:
        if intent == "total_revenue":
            row = connection.execute(
                f"""SELECT COALESCE(SUM(quantity * unit_price - discount_amount), 0) AS revenue,
                           COUNT(*) AS transaction_count
                    FROM transactions WHERE {VALID_SALES_FILTER}"""
            ).fetchone()
            revenue = round(float(row["revenue"]), 2)
            data = [{"metric": "Net revenue", "value": revenue},
                    {"metric": "Included transactions", "value": row["transaction_count"]}]
            return {"ok": True, "intent": intent,
                    "answer": f"Total net revenue is {revenue:,.2f}.",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "top_product":
            rows = connection.execute(
                f"""SELECT product, SUM(quantity * unit_price - discount_amount) AS revenue,
                           SUM(quantity) AS units_sold
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY product ORDER BY revenue DESC, product ASC LIMIT 5"""
            ).fetchall()
            data = [{"product": r["product"], "revenue": round(float(r["revenue"]), 2),
                     "units_sold": int(r["units_sold"])} for r in rows]
            if not data:
                return {"ok": False, "answer": "No valid completed sales were found.",
                        "supporting_data": [], "interpretation_source": interpretation_source}
            return {"ok": True, "intent": intent,
                    "answer": f"{data[0]['product']} generated the highest net revenue "
                              f"({data[0]['revenue']:,.2f}).",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "monthly_trend":
            rows = connection.execute(
                f"""SELECT substr(order_date, 1, 7) AS month,
                           SUM(quantity * unit_price - discount_amount) AS revenue
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY substr(order_date, 1, 7) ORDER BY month"""
            ).fetchall()
            data = [{"month": r["month"], "revenue": round(float(r["revenue"]), 2)}
                    for r in rows]
            return {"ok": True, "intent": intent,
                    "answer": "Here is the monthly net revenue trend.",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "top_region":
            rows = connection.execute(
                f"""SELECT region, SUM(quantity * unit_price - discount_amount) AS revenue
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY region ORDER BY revenue DESC, region ASC LIMIT 5"""
            ).fetchall()
            data = [{"region": r["region"], "revenue": round(float(r["revenue"]), 2)}
                    for r in rows]
            if not data:
                return {"ok": False, "answer": "No valid completed sales were found.",
                        "supporting_data": [], "interpretation_source": interpretation_source}
            return {"ok": True, "intent": intent,
                    "answer": f"{data[0]['region']} generated the highest sales by net revenue "
                              f"({data[0]['revenue']:,.2f}).",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "revenue_by_category":
            rows = connection.execute(
                f"""SELECT COALESCE(category, 'Unknown') AS category,
                           SUM(quantity * unit_price - discount_amount) AS revenue,
                           SUM(quantity) AS units_sold, COUNT(*) AS transactions
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY category ORDER BY revenue DESC"""
            ).fetchall()
            data = [{"category": r["category"], "revenue": round(float(r["revenue"]), 2),
                     "units_sold": int(r["units_sold"]), "transactions": int(r["transactions"])} for r in rows]
            return {"ok": True, "intent": intent, "answer": "Revenue breakdown by product category.",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "revenue_by_channel":
            rows = connection.execute(
                f"""SELECT COALESCE(sales_channel, 'Unknown') AS sales_channel,
                           SUM(quantity * unit_price - discount_amount) AS revenue,
                           COUNT(*) AS transactions
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY sales_channel ORDER BY revenue DESC"""
            ).fetchall()
            data = [{"sales_channel": r["sales_channel"], "revenue": round(float(r["revenue"]), 2),
                     "transactions": int(r["transactions"])} for r in rows]
            return {"ok": True, "intent": intent, "answer": "Revenue breakdown by sales channel.",
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "transaction_count":
            row = connection.execute(
                f"SELECT COUNT(*) AS transaction_count FROM transactions WHERE {VALID_SALES_FILTER}"
            ).fetchone()
            count = int(row["transaction_count"])
            return {"ok": True, "intent": intent,
                    "answer": f"There are {count} completed transactions included in the analysis.",
                    "supporting_data": [{"metric": "Completed transactions", "value": count}],
                    "interpretation_source": interpretation_source}

        if intent == "average_order_value":
            row = connection.execute(
                f"""SELECT AVG(quantity * unit_price - discount_amount) AS average_value,
                           COUNT(*) AS transaction_count
                    FROM transactions WHERE {VALID_SALES_FILTER}"""
            ).fetchone()
            average = round(float(row["average_value"] or 0), 2)
            return {"ok": True, "intent": intent,
                    "answer": f"Average net revenue per completed transaction is {average:,.2f}.",
                    "supporting_data": [{"metric": "Average transaction revenue", "value": average},
                                        {"metric": "Included transactions", "value": int(row["transaction_count"])}],
                    "interpretation_source": interpretation_source}

        if intent == "most_sold_product":
            rows = connection.execute(
                f"""SELECT product, SUM(quantity) AS units_sold,
                           SUM(quantity * unit_price - discount_amount) AS revenue
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY product ORDER BY units_sold DESC, product ASC LIMIT 5"""
            ).fetchall()
            data = [{"product": r["product"], "units_sold": int(r["units_sold"]),
                     "revenue": round(float(r["revenue"]), 2)} for r in rows]
            answer = f"{data[0]['product']} has the highest quantity sold ({data[0]['units_sold']} units)." if data else "No valid completed sales were found."
            return {"ok": bool(data), "intent": intent, "answer": answer,
                    "supporting_data": data, "interpretation_source": interpretation_source}

        if intent == "discount_summary":
            row = connection.execute(
                f"""SELECT SUM(discount_amount) AS total_discount,
                           AVG(discount_amount) AS average_discount,
                           COUNT(*) AS transaction_count
                    FROM transactions WHERE {VALID_SALES_FILTER}"""
            ).fetchone()
            total_discount = round(float(row["total_discount"] or 0), 2)
            average_discount = round(float(row["average_discount"] or 0), 2)
            return {"ok": True, "intent": intent,
                    "answer": f"Total discounts on completed transactions amount to {total_discount:,.2f}.",
                    "supporting_data": [{"metric": "Total discount amount", "value": total_discount},
                                        {"metric": "Average discount per transaction", "value": average_discount},
                                        {"metric": "Included transactions", "value": int(row["transaction_count"])}],
                    "interpretation_source": interpretation_source}

        if intent == "revenue_change":
            month_a = interpreted.get("month_a")
            month_b = interpreted.get("month_b")
            if not month_a or not month_b:
                return {
                    "ok": False,
                    "intent": intent,
                    "answer": "Please specify both months, for example: "
                              "'How did revenue change between January and February 2026?'",
                    "supporting_data": [],
                    "interpretation_source": interpretation_source
                }
            rows = connection.execute(
                f"""SELECT substr(order_date, 1, 7) AS month,
                           SUM(quantity * unit_price - discount_amount) AS revenue
                    FROM transactions
                    WHERE {VALID_SALES_FILTER}
                      AND substr(order_date, 1, 7) IN (?, ?)
                    GROUP BY substr(order_date, 1, 7)""",
                (month_a, month_b)
            ).fetchall()
            by_month = {r["month"]: float(r["revenue"]) for r in rows}
            if month_a not in by_month or month_b not in by_month:
                return {"ok": False, "intent": intent,
                        "answer": "I could not find valid sales for both requested months.",
                        "supporting_data": [
                            {"month": month_a, "revenue": round(by_month.get(month_a, 0), 2)},
                            {"month": month_b, "revenue": round(by_month.get(month_b, 0), 2)}
                        ], "interpretation_source": interpretation_source}
            revenue_a, revenue_b = by_month[month_a], by_month[month_b]
            difference = revenue_b - revenue_a
            direction = "increased" if difference > 0 else "decreased" if difference < 0 else "did not change"
            percentage = (difference / revenue_a * 100) if revenue_a else None
            answer = f"Revenue {direction} by {abs(difference):,.2f} from {month_a} to {month_b}."
            if percentage is not None:
                answer += f" That is a {abs(percentage):.2f}% " + ("increase." if difference > 0 else "decrease." if difference < 0 else "change.")
            data = [
                {"month": month_a, "revenue": round(revenue_a, 2)},
                {"month": month_b, "revenue": round(revenue_b, 2)},
                {"metric": "Change (second month minus first)", "value": round(difference, 2)}
            ]
            return {"ok": True, "intent": intent, "answer": answer,
                    "supporting_data": data, "interpretation_source": interpretation_source}

    return {"ok": False, "answer": "This question is not supported yet.",
            "supporting_data": [], "interpretation_source": interpretation_source}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE_PATH=str(DEFAULT_DB_PATH),
        CSV_PATH=str(DEFAULT_CSV_PATH),
        USE_LLM=True
    )
    if test_config:
        app.config.update(test_config)

    # Create and populate the database when the app starts.
    load_csv_to_database(app.config["CSV_PATH"], app.config["DATABASE_PATH"])

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "message": "Sales BI Assistant is running."})

    @app.get("/api/overview")
    def overview():
        with get_connection(app.config["DATABASE_PATH"]) as connection:
            row = connection.execute(
                f"""SELECT COUNT(*) AS transaction_count,
                           COALESCE(SUM(quantity * unit_price - discount_amount), 0) AS revenue,
                           COALESCE(SUM(quantity), 0) AS units_sold,
                           COALESCE(SUM(discount_amount), 0) AS discounts,
                           COUNT(DISTINCT product) AS product_count,
                           COUNT(DISTINCT category) AS category_count,
                           COUNT(DISTINCT region) AS region_count,
                           MIN(order_date) AS first_date, MAX(order_date) AS last_date
                    FROM transactions WHERE {VALID_SALES_FILTER}"""
            ).fetchone()
            monthly = connection.execute(
                f"""SELECT substr(order_date, 1, 7) AS month,
                           SUM(quantity * unit_price - discount_amount) AS revenue
                    FROM transactions WHERE {VALID_SALES_FILTER}
                    GROUP BY substr(order_date, 1, 7) ORDER BY month"""
            ).fetchall()
            def grouped(column, limit=10):
                # column comes only from the fixed allow-list below, never from user input.
                return connection.execute(
                    f"""SELECT {column} AS label,
                               SUM(quantity * unit_price - discount_amount) AS revenue,
                               SUM(quantity) AS units_sold,
                               COUNT(*) AS transactions
                        FROM transactions WHERE {VALID_SALES_FILTER}
                        GROUP BY {column} ORDER BY revenue DESC LIMIT ?""",
                    (limit,)
                ).fetchall()
            products = grouped("product", 5)
            categories = grouped("category")
            regions = grouped("region")
            channels = grouped("sales_channel")
        monthly_data = [{"month": r["month"], "revenue": round(float(r["revenue"] or 0), 2)} for r in monthly]
        product_data = [{"label": r["label"], "revenue": round(float(r["revenue"] or 0), 2), "units_sold": int(r["units_sold"] or 0), "transactions": int(r["transactions"] or 0)} for r in products]
        category_data = [{"label": r["label"], "revenue": round(float(r["revenue"] or 0), 2), "units_sold": int(r["units_sold"] or 0), "transactions": int(r["transactions"] or 0)} for r in categories]
        region_data = [{"label": r["label"], "revenue": round(float(r["revenue"] or 0), 2), "units_sold": int(r["units_sold"] or 0), "transactions": int(r["transactions"] or 0)} for r in regions]
        channel_data = [{"label": r["label"], "revenue": round(float(r["revenue"] or 0), 2), "units_sold": int(r["units_sold"] or 0), "transactions": int(r["transactions"] or 0)} for r in channels]
        total_revenue = round(float(row["revenue"] or 0), 2)
        transaction_count = int(row["transaction_count"] or 0)
        best_month = max(monthly_data, key=lambda item: item["revenue"], default=None)
        return jsonify({
            "transaction_count": transaction_count,
            "revenue": total_revenue,
            "average_order_value": round(total_revenue / transaction_count, 2) if transaction_count else 0,
            "units_sold": int(row["units_sold"] or 0),
            "discounts": round(float(row["discounts"] or 0), 2),
            "product_count": int(row["product_count"] or 0),
            "category_count": int(row["category_count"] or 0),
            "region_count": int(row["region_count"] or 0),
            "first_date": row["first_date"], "last_date": row["last_date"],
            "monthly_trend": monthly_data,
            "top_products": product_data,
            "category_revenue": category_data,
            "region_revenue": region_data,
            "channel_revenue": channel_data,
            "top_product": product_data[0] if product_data else None,
            "top_region": region_data[0] if region_data else None,
            "best_month": best_month
        })

    @app.post("/api/ask")
    def ask():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "Send a JSON object with a 'question' field."}), 400

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            return jsonify({"ok": False, "error": "The question must be a non-empty string."}), 400
        if len(question) > 500:
            return jsonify({"ok": False, "error": "Question is too long (maximum 500 characters)."}), 400

        if app.config["USE_LLM"]:
            interpreted, source = ai_interpretation(question)
        else:
            interpreted, source = rule_based_interpretation(question), "rule-based fallback"
        result = run_analytics(question, app.config["DATABASE_PATH"], interpreted, source)
        return jsonify(result)

    @app.get("/api/data-quality")
    def data_quality():
        with get_connection(app.config["DATABASE_PATH"]) as connection:
            total = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            valid = connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE validation_status = 'valid'"
            ).fetchone()[0]
            excluded = connection.execute(
                "SELECT validation_reason, COUNT(*) AS count FROM transactions "
                "WHERE validation_status = 'excluded' GROUP BY validation_reason "
                "ORDER BY count DESC"
            ).fetchall()
        return jsonify({
            "total_rows": total,
            "valid_completed_rows": valid,
            "excluded_rows": total - valid,
            "exclusion_reasons": [{"reason": r["validation_reason"], "count": r["count"]}
                                  for r in excluded]
        })

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
