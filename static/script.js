const form = document.querySelector("#question-form");
const questionInput = document.querySelector("#question");
const askButton = document.querySelector("#ask-button");
const resultPanel = document.querySelector("#result-panel");
const answerText = document.querySelector("#answer");
const tableWrap = document.querySelector("#table-wrap");
const errorText = document.querySelector("#error");
const sourcePill = document.querySelector("#source-pill");
const resultChart = document.querySelector("#result-chart");

function number(value, digits = 2) { return Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: digits }); }
function currency(value) { return "₹" + number(value); }
function titleCase(value) { return String(value).replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase()); }
function formatValue(value, key = "", row = {}) {
  if (typeof value === "number") {
    const label = `${key} ${row.metric || ""}`.toLowerCase();
    if (label.includes("revenue") || label.includes("discount") || label.includes("order value") || label === "value") return currency(value);
    return number(value, 2);
  }
  return value == null ? "—" : String(value);
}
function renderTable(rows) {
  tableWrap.innerHTML = "";
  if (!Array.isArray(rows) || rows.length === 0) return;
  const columns = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const wrapper = document.createElement("div"); wrapper.className = "table-scroll";
  const table = document.createElement("table"); const thead = document.createElement("thead"); const headerRow = document.createElement("tr");
  columns.forEach(column => { const th = document.createElement("th"); th.textContent = titleCase(column); headerRow.appendChild(th); });
  thead.appendChild(headerRow); table.appendChild(thead); const tbody = document.createElement("tbody");
  rows.forEach(row => { const tr = document.createElement("tr"); columns.forEach(column => { const td = document.createElement("td"); td.textContent = formatValue(row[column], column, row); tr.appendChild(td); }); tbody.appendChild(tr); });
  table.appendChild(tbody); wrapper.appendChild(table); tableWrap.appendChild(wrapper);
}
function renderBarChart(container, rows, labelKey, valueKey, isCurrency = true) {
  container.innerHTML = "";
  if (!Array.isArray(rows) || !rows.length) { container.innerHTML = '<div class="chart-empty">No data available.</div>'; return; }
  const values = rows.map(row => Number(row[valueKey] || 0)); const max = Math.max(...values, 1);
  const chart = document.createElement("div"); chart.className = "bar-chart";
  rows.forEach(row => {
    const column = document.createElement("div"); column.className = "chart-column";
    const track = document.createElement("div"); track.className = "bar-track";
    const bar = document.createElement("div"); bar.className = "chart-bar";
    bar.style.height = `${Math.max(4, Number(row[valueKey] || 0) / max * 100)}%`;
    const label = document.createElement("span"); label.className = "chart-bar-label"; label.textContent = isCurrency ? currency(row[valueKey]) : number(row[valueKey], 0);
    bar.appendChild(label); track.appendChild(bar);
    const caption = document.createElement("span"); caption.className = "chart-month"; caption.textContent = row[labelKey]; caption.title = row[labelKey];
    column.appendChild(track); column.appendChild(caption); chart.appendChild(column);
  });
  container.appendChild(chart);
}
function renderHorizontalChart(container, rows) {
  container.innerHTML = "";
  if (!Array.isArray(rows) || !rows.length) { container.innerHTML = '<div class="chart-empty">No data available.</div>'; return; }
  const max = Math.max(...rows.map(row => Number(row.revenue || 0)), 1);
  rows.forEach((row, index) => {
    const item = document.createElement("div"); item.className = "horizontal-item";
    const top = document.createElement("div"); top.className = "horizontal-top";
    const label = document.createElement("span"); label.className = "horizontal-label"; label.textContent = row.label; label.title = row.label;
    const value = document.createElement("strong"); value.className = "horizontal-value"; value.textContent = currency(row.revenue);
    top.append(label, value);
    const track = document.createElement("div"); track.className = "horizontal-track";
    const bar = document.createElement("div"); bar.className = `horizontal-fill fill-${index % 4}`; bar.style.width = `${Math.max(2, Number(row.revenue || 0) / max * 100)}%`; track.appendChild(bar);
    item.append(top, track); container.appendChild(item);
  });
}
async function loadOverview() {
  try {
    const response = await fetch("/api/overview"); if (!response.ok) throw new Error("Could not load overview");
    const data = await response.json();
    document.querySelector("#metric-revenue").textContent = currency(data.revenue);
    document.querySelector("#metric-transactions").textContent = number(data.transaction_count, 0);
    document.querySelector("#metric-units").textContent = number(data.units_sold, 0);
    document.querySelector("#metric-aov").textContent = currency(data.average_order_value);
    const monthFormat = iso => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", year: "numeric" });
    if (data.first_date && data.last_date) {
      const range = `${monthFormat(data.first_date)} – ${monthFormat(data.last_date)}`;
      document.querySelector("#date-range").textContent = range;
      document.querySelector("#footer-date-range").textContent = range;
    }
    renderBarChart(document.querySelector("#overview-chart"), data.monthly_trend.map(row => ({...row, month: new Date(`${row.month}-01T00:00:00`).toLocaleDateString("en-US", { month: "short" })})), "month", "revenue");
    renderHorizontalChart(document.querySelector("#products-chart"), data.top_products);
    renderHorizontalChart(document.querySelector("#category-chart"), data.category_revenue);
    renderHorizontalChart(document.querySelector("#region-chart"), data.region_revenue);
    renderHorizontalChart(document.querySelector("#channel-chart"), data.channel_revenue);
    if (data.top_product) {
      document.querySelector("#highlight-product").textContent = data.top_product.label;
      document.querySelector("#highlight-product-value").textContent = currency(data.top_product.revenue) + " revenue";
    }
    if (data.top_region) {
      document.querySelector("#highlight-region").textContent = data.top_region.label;
      document.querySelector("#highlight-region-value").textContent = currency(data.top_region.revenue) + " revenue";
    }
    if (data.best_month) {
      document.querySelector("#highlight-month").textContent = monthFormat(data.best_month.month);
      document.querySelector("#highlight-month-value").textContent = currency(data.best_month.revenue) + " revenue";
    }
  } catch (error) {
    document.querySelector("#metric-revenue").textContent = "Unavailable";
    document.querySelector("#overview-chart").innerHTML = '<div class="chart-empty">Overview data could not be loaded. Check that the Flask server is running.</div>';
  }
}
async function askQuestion(question) {
  resultPanel.hidden = false; errorText.hidden = true; tableWrap.innerHTML = ""; resultChart.hidden = true; resultChart.innerHTML = "";
  answerText.textContent = "Analysing your question…"; sourcePill.textContent = "Processing"; askButton.disabled = true;
  document.querySelector("#result-title").textContent = "Your answer"; document.querySelector("#result-subtitle").textContent = question;
  try {
    const response = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    const result = await response.json(); if (!response.ok) throw new Error(result.error || "The request could not be processed.");
    answerText.textContent = result.answer || "No answer was returned.";
    sourcePill.textContent = result.interpretation_source === "LLM" ? "AI interpreted · SQL calculated" : "Rule-based · SQL calculated";
    renderTable(result.supporting_data);
    if (result.ok && Array.isArray(result.supporting_data)) {
      let chartRows = result.supporting_data.filter(row => typeof row.revenue === "number");
      if (result.intent === "most_sold_product") chartRows = result.supporting_data.filter(row => typeof row.units_sold === "number");
      if (chartRows.length > 1) {
        const labelKey = Object.keys(chartRows[0]).find(key => ["month", "product", "region", "category", "sales_channel"].includes(key));
        const valueKey = result.intent === "most_sold_product" ? "units_sold" : "revenue";
        if (labelKey) renderBarChart(resultChart, chartRows.map(row => ({...row, [labelKey]: row[labelKey]})), labelKey, valueKey, valueKey === "revenue");
        resultChart.hidden = false;
      }
    }
    if (!result.ok) { errorText.hidden = false; errorText.textContent = (result.suggestions || []).length ? "Try a question from the question list above, or include both month names for a comparison." : "Check the question and try again."; }
    resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { answerText.textContent = "Sorry, the request failed."; errorText.hidden = false; errorText.textContent = error.message; }
  finally { askButton.disabled = false; }
}
form.addEventListener("submit", event => { event.preventDefault(); const question = questionInput.value.trim(); if (question) askQuestion(question); });
document.querySelectorAll(".question-option").forEach(button => button.addEventListener("click", () => { questionInput.value = button.dataset.question; askQuestion(button.dataset.question); }));
loadOverview();
