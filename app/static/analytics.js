const form = document.querySelector("#analytics-form");
const dashboard = document.querySelector("#analytics-report");
const status = document.querySelector("#analytics-status");
const metrics = document.querySelector("#metrics");
const misses = document.querySelector("#misses");
const empty = document.querySelector("#analytics-empty");
let pending;

const conditions = {platform: "платформа", term: "срок лицензии", license_type: "тип лицензии"};
const labels = {windows: "Windows", "windows-server": "Windows Server", linux: "Linux",
  "astra-linux": "Astra Linux", macos: "macOS", new: "новая лицензия", subscription: "подписка",
  renewal: "продление", upgrade: "обновление", perpetual: "бессрочная"};

function addText(parent, tag, value) {
  const element = document.createElement(tag);
  element.textContent = value;
  parent.append(element);
}

function explain(reason) {
  const [code, key] = reason.split(".");
  if (code === "unverified") return ["Не подтверждено условие «" + conditions[key] + "»",
    "Проверить и заполнить характеристику лицензии"];
  if (code === "filter") return ["Сузило выдачу условие «" + conditions[key] + "»",
    "Проверить, нужны ли лицензии с этим условием"];
  return {
    no_candidates: ["Поиск не нашёл кандидатов", "Проверить названия и ассортимент"],
    combined_filters: ["Сузило выдачу сочетание условий", "Проверить доступные сочетания условий"],
    few_matches: ["Меньше четырёх подходящих семейств", "Проверить ассортимент и ранжирование"],
    full: ["Нет переходов в основные карточки", "Проверить порядок выдачи и описание карточек"],
  }[code];
}

function percentage(count, total) {
  return total ? new Intl.NumberFormat("ru-RU", {style: "percent", maximumFractionDigits: 1}).format(count / total) : "—";
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  pending?.abort();
  const controller = new AbortController();
  pending = controller;
  dashboard.hidden = true;
  status.textContent = "Загружаем отчёт…";
  const demo = document.querySelector("#analytics-demo").checked;
  try {
    const response = await fetch("/analytics/report?demo=" + demo, {
      headers: {"X-Analytics-Token": document.querySelector("#analytics-token").value},
      cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new Error((await response.json()).detail);
    const data = await response.json();
    if (controller.signal.aborted) return;
    const m = data.metrics;
    metrics.replaceChildren();
    for (const [name, value] of [
      ["Поиски", m.searches],
      ["Без подходящих семейств", percentage(m.empty, m.searches)],
      ["1–3 подходящих семейства", percentage(m.few, m.searches)],
      ["С переходом в основную карточку", percentage(m.direct_clicks, m.searches)],
      ["С переходом в рекомендацию", percentage(m.recommendation_clicks, m.searches)],
    ]) {
      const metric = document.createElement("div");
      addText(metric, "dt", name);
      addText(metric, "dd", value);
      metrics.append(metric);
    }
    misses.replaceChildren();
    for (const group of data.groups) {
      const row = misses.insertRow();
      const filters = Object.entries(group.filters).map(([key, value]) =>
        conditions[key] + " — " + (labels[value] || value + " мес."));
      addText(row, "td", [group.query, ...filters].join(" · "));
      addText(row, "td", group.searches);
      for (const text of explain(group.reason)) addText(row, "td", text);
    }
    empty.hidden = data.groups.length > 0;
    misses.closest(".analytics-table").hidden = data.groups.length === 0;
    status.textContent = (demo ? "Демо-запросы" : "Рабочие запросы") + " за " + data.days + " дней";
    dashboard.hidden = false;
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = error.message || "Не удалось получить отчёт";
  }
});
