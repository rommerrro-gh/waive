const form = document.querySelector("#search");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const query = document.querySelector("#query");
const suggestions = document.querySelector("#suggestions");
const comparisonBar = document.querySelector("#comparison-bar");
const comparisonItems = document.querySelector("#comparison-items");
const openComparison = document.querySelector("#open-comparison");
const comparison = document.querySelector("#comparison");
const filterPanel = document.querySelector("#search-filters");
const filterCount = document.querySelector("#active-filters");
const demoMode = new URLSearchParams(location.search).get("demo") === "true";
const selected = new Map();
let activeSearch;
let activeSuggestion;
let suggestionTimer;
let activeOption = -1;

function addText(parent, tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.append(element);
  return element;
}

function priceLabel(item) {
  if (item.price == null) return "Цена не указана";
  const currency = item.currency === "RUR" ? "RUB" : item.currency || "RUB";
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency }).format(Number(item.price));
}

function availabilityLabel(item) {
  return item.available ? "В наличии" : "Недоступно";
}

function attributeValue(item, ...keys) {
  for (const key of keys) {
    const value = String(item.attributes[key] ?? "").trim();
    if (value && !["не определено", "не указано", "другое или отсутствует"].includes(value.toLowerCase())) {
      return value;
    }
  }
  return null;
}

function addProductLink(parent, item, requestId, label = "Карточка на Softline") {
  if (/^https?:\/\//i.test(item.url || "")) {
    const link = addText(parent, "a", label);
    link.href = item.url;
    if (requestId) {
      link.addEventListener("click", () => {
        fetch("/analytics/events/click", {
          method: "POST", keepalive: true,
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({request_id: requestId, offer_id: item.offer_id}),
        }).catch(() => {});
      });
    }
  }
}

function renderSection(id, cards, requestId, context) {
  const section = document.querySelector("#" + id);
  section.hidden = !cards.length;
  section.querySelector(".cards").replaceChildren(...cards.map(item => productCard(item, requestId, context)));
}

function productIcon(item) {
  if (item.category_id !== "650") return "box-seam";
  if (/cloud|облачн/i.test(item.name)) return "cloud-check";
  if (/desktop|workstation|рабочих станций/i.test(item.name)) return "display";
  return "shield-check";
}

function productCard(item, requestId, context) {
  const card = document.createElement("article");
  card.className = "product-card";
  const body = addText(card, "div", "");
  body.className = "product-body";
  const icon = document.createElement("img");
  const symbol = productIcon(item);
  icon.src = "/icons/" + symbol + ".svg";
  icon.alt = "";
  icon.width = icon.height = 22;
  const tile = addText(body, "span", "");
  tile.className = "product-icon " + symbol;
  tile.append(icon);
  const copy = addText(body, "div", "");
  addText(copy, "h2", item.name);
  if (item.reason) addText(copy, "p", item.reason).className = "product-reason";
  const facts = addText(copy, "ul", "");
  facts.className = "product-facts";
  for (const key of ["Версия", "Срок действия", "Модель лицензирования", "Тип лицензии", "Платформа", "Минимальная покупка"]) {
    const value = attributeValue(item, key);
    if (value) addText(facts, "li", key + " · " + value);
  }
  facts.hidden = !facts.children.length;
  const availability = addText(card, "p", availabilityLabel(item));
  availability.className = "product-availability";
  availability.dataset.available = String(item.available);
  const footer = addText(card, "div", "");
  footer.className = "product-footer";
  addText(footer, "strong", priceLabel(item));
  const actions = addText(footer, "div", "");
  actions.className = "card-actions";
  const button = addText(actions, "button", "");
  button.type = "button";
  button.className = "secondary compare-toggle";
  button.dataset.offerId = item.offer_id;
  button.addEventListener("click", () => {
    if (selected.has(item.offer_id)) selected.delete(item.offer_id);
    else if (selected.size < 2) selected.set(item.offer_id, item);
    syncComparison();
  });
  renderCompareButton(button);
  addProductLink(actions, item, requestId, "В каталог");
  if (context) addProcurementActions(actions, item, context);
  return card;
}

function renderCompareButton(button) {
  const isSelected = selected.has(button.dataset.offerId);
  button.setAttribute("aria-pressed", String(isSelected));
  button.textContent = isSelected ? "Убрать из сравнения" : "В сравнение";
  button.disabled = selected.size === 2 && !isSelected;
}

function syncComparison() {
  comparisonBar.hidden = selected.size === 0;
  openComparison.disabled = selected.size !== 2;
  document.querySelector("#comparison-count").textContent = "Выбрано " + selected.size + " из 2";
  comparisonItems.replaceChildren();
  for (const [id, item] of selected) {
    const entry = addText(comparisonItems, "div", "");
    entry.className = "comparison-item";
    addText(entry, "span", item.name);
    const remove = addText(entry, "button", "Убрать");
    remove.type = "button";
    remove.className = "secondary";
    remove.setAttribute("aria-label", "Убрать из сравнения: " + item.name);
    remove.addEventListener("click", () => {
      selected.delete(id);
      syncComparison();
    });
  }
  document.querySelectorAll(".compare-toggle").forEach(renderCompareButton);
}

openComparison.addEventListener("click", () => {
  if (selected.size !== 2) return;
  const items = Array.from(selected.values());
  const content = document.querySelector("#comparison-content");
  content.replaceChildren();
  const table = document.createElement("table");
  addText(table.createTHead().insertRow(), "th", "Условие");
  for (const item of items) {
    const header = addText(table.tHead.rows[0], "th", item.name);
    header.scope = "col";
    addProductLink(header, item);
  }
  const metrics = [
    ["Цена из каталога", priceLabel],
    ["Наличие", availabilityLabel],
    ["Версия", item => attributeValue(item, "Версия")],
    ["Срок действия", item => attributeValue(item, "Срок действия")],
    ["Платформа", item => attributeValue(item, "Платформа", "Совместимая архитектура/платформа")],
    ["Тип лицензии", item => attributeValue(item, "Тип лицензии")],
    ["Модель лицензирования", item => attributeValue(item, "Модель лицензирования")],
    ["Минимальная покупка", item => attributeValue(item, "Минимальная покупка")],
  ];
  const body = table.createTBody();
  for (const [label, displayValue] of metrics) {
    const row = body.insertRow();
    const values = items.map(displayValue);
    if (values[0] && values[1] && values[0] !== values[1]) row.className = "difference";
    addText(row, "th", label).scope = "row";
    for (const value of values) addText(row, "td", value ?? "Нет данных в каталоге");
  }
  content.append(table);
  comparison.showModal();
});

const filterNames = {platform: "платформа", term: "срок лицензии", license_type: "тип лицензии"};
const catalogFilterNames = {...filterNames, in_stock: "в наличии", price_min: "цена от", price_max: "цена до"};
const filterSuggestions = document.querySelector("#filter-suggestions");
const filterDetails = document.querySelector("#filter-details");

function filterButton(parent, label, action) {
  const button = addText(parent, "button", label);
  button.type = "button";
  button.className = "secondary";
  button.addEventListener("click", action);
}

function showFilters(data) {
  for (const [key, value] of Object.entries(data.suggested_filters)) {
    const select = form.elements[key];
    if (select.value) continue;
    const option = Array.from(select.options).find(option => option.value === value);
    const label = option?.textContent || value + " мес.";
    filterButton(filterSuggestions, "Применить: " + label, () => {
      if (!option) select.add(new Option(label, value));
      select.value = value;
      form.requestSubmit();
    });
  }
  filterSuggestions.hidden = !filterSuggestions.children.length;
  if (!data.filter_diagnostics || data.results.length >= 4) return;
  const entries = Object.entries(data.filter_diagnostics.conditions)
    .filter(([, counts]) => counts.would_restore > 0);
  for (const [key, counts] of entries) {
    const note = counts.unverified ? " · без подтверждения: " + counts.unverified : "";
    addText(filterDetails, "p", catalogFilterNames[key] + " исключает семейств: " + counts.would_restore + note);
    filterButton(filterDetails, "Снять условие «" + catalogFilterNames[key] + "»", () => {
      const field = form.elements[key];
      if (field.type === "checkbox") field.checked = false;
      else field.value = "";
      validatePrice();
      form.requestSubmit();
    });
  }
  if (!entries.length && data.filter_diagnostics.families_before > data.filter_diagnostics.families_after) {
    addText(filterDetails, "p", "Сочетание условий сузило выдачу. Попробуйте ослабить несколько фильтров.");
  }
  filterDetails.hidden = !filterDetails.children.length;
}

function closeSuggestions() {
  clearTimeout(suggestionTimer);
  activeSuggestion?.abort();
  activeOption = -1;
  suggestions.hidden = true;
  suggestions.replaceChildren();
  query.setAttribute("aria-expanded", "false");
  query.removeAttribute("aria-activedescendant");
}

function acceptSuggestion(value) {
  query.value = value;
  restoreForNewQuery();
  closeSuggestions();
  form.requestSubmit();
}

async function fetchSuggestions(prefix) {
  const controller = new AbortController();
  activeSuggestion = controller;
  try {
    const response = await fetch("/suggest?" + new URLSearchParams({q: prefix}), {signal: controller.signal});
    if (!response.ok) return;
    const {suggestions: options} = await response.json();
    if (controller.signal.aborted || query.value.trim() !== prefix || document.activeElement !== query) return;
    options.forEach((value, index) => {
      const option = addText(suggestions, "li", value);
      option.id = "suggestion-" + index;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      option.addEventListener("mousedown", event => event.preventDefault());
      option.addEventListener("click", () => acceptSuggestion(value));
    });
    suggestions.hidden = options.length === 0;
    query.setAttribute("aria-expanded", String(options.length > 0));
  } catch {
    // Поиск доступен и при сетевой ошибке подсказок.
  }
}

query.addEventListener("input", event => {
  if (!event.isComposing) restoreForNewQuery();
  closeSuggestions();
  const prefix = query.value.trim();
  if (!event.isComposing && prefix.length >= 2 && prefix.length <= 120) {
    suggestionTimer = setTimeout(() => fetchSuggestions(prefix), 180);
  }
});
query.addEventListener("blur", closeSuggestions);
query.addEventListener("keydown", event => {
  if (suggestions.hidden || event.isComposing) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeSuggestions();
  } else if (event.key === "Enter" && activeOption >= 0) {
    event.preventDefault();
    acceptSuggestion(suggestions.children[activeOption].textContent);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const options = Array.from(suggestions.children);
    const next = event.key === "ArrowDown" ? activeOption + 1 : activeOption < 0 ? options.length - 1 : activeOption - 1;
    activeOption = (next + options.length) % options.length;
    options.forEach((element, index) => element.setAttribute("aria-selected", String(index === activeOption)));
    query.setAttribute("aria-activedescendant", options[activeOption].id);
    options[activeOption].scrollIntoView({block: "nearest"});
  }
});

function validatePrice() {
  const {price_min: min, price_max: max} = form.elements;
  max.setCustomValidity(min.value && max.value && min.valueAsNumber > max.valueAsNumber ?
    "Цена «От» должна быть не выше цены «До»" : "");
}

form.addEventListener("input", validatePrice);
form.addEventListener("invalid", event => {
  if (filterPanel.contains(event.target)) filterPanel.open = true;
}, true);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  closeSuggestions();
  activeSearch?.abort();
  clearBundle();
  const controller = new AbortController();
  activeSearch = controller;
  results.replaceChildren();
  for (const section of [filterSuggestions, filterDetails]) {
    section.replaceChildren();
    section.hidden = true;
  }
  renderSection("alternatives", []);
  renderSection("recommendations", []);
  status.textContent = "Ищем товары…";
  const params = new URLSearchParams(new FormData(form));
  for (const key of Object.keys(catalogFilterNames)) if (!params.get(key)) params.delete(key);
  params.set("track", "true");
  if (demoMode) params.set("demo", "true");
  if (isBusinessView() && profile) params.set("with_profile", "true");
  if (isBusinessView()) updateProfileControls();
  lastProcurementQuery = query.value.trim();
  try {
    const response = await fetch("/search?" + params, { signal: controller.signal });
    if (!response.ok) throw new Error("Поиск недоступен");
    const data = await response.json();
    if (controller.signal.aborted) return;
    const requestId = data.tracked ? data.request_id : undefined;
    const context = {version: data.catalog_version, platform: params.get("platform")};
    results.replaceChildren(...data.results.map(item => productCard(item, requestId, context)));
    renderSection("alternatives", data.alternatives, undefined, context);
    renderSection("recommendations", data.recommendations, requestId, context);
    const active = Object.keys(data.applied_filters);
    const activeCount = active.length - Number(active.includes("price_min") && active.includes("price_max"));
    filterCount.textContent = activeCount ? " · " + activeCount : "";
    filterCount.hidden = !activeCount;
    showFilters(data);
    status.textContent = data.clarification || "Результаты поиска";
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = "Поиск временно недоступен. Попробуйте ещё раз.";
  }
});
