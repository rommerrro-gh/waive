const form = document.querySelector("#search");
const status = document.querySelector("#status");
const correction = document.querySelector("#query-correction");
const spellingOptions = document.querySelector("#spelling-options");
const spellingButtons = spellingOptions.querySelector(".card-actions");
const results = document.querySelector("#results");
const pagination = document.querySelector("#result-pagination");
const progress = document.querySelector("#result-progress");
const moreResults = document.querySelector("#more-results");
const pageSize = 12;
const query = document.querySelector("#query");
const suggestions = document.querySelector("#suggestions");
const comparisonBar = document.querySelector("#comparison-bar");
const comparisonToggle = document.querySelector("#toggle-comparison");
const comparisonItems = document.querySelector("#comparison-items");
const openComparison = document.querySelector("#open-comparison");
const comparison = document.querySelector("#comparison");
const filterPanel = document.querySelector("#search-filters");
const filterCount = document.querySelector("#active-filters");
const categoryFilters = document.querySelector("#category-filters");
const categoryOptions = document.querySelector("#category-options");
const sortControl = document.querySelector("#sort-control");
const sortResults = document.querySelector("#sort-results");
const demoMode = new URLSearchParams(location.search).get("demo") === "true";
const selected = new Map();
const comparisonLimit = 4;
let activeSearch;
let resultSet;
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

function catalogArt(item) {
  if (/cloud|облачн|виртуальн/i.test(item.name)) return "cloud";
  if (/сервер|хранения данных|\bserver\b|rack/i.test(item.name)) return "server";
  if (/аналитик|analyst|threat intelligence/i.test(item.name)) return "analyst";
  return "package";
}

function productCard(item, requestId, context) {
  const card = document.createElement("article");
  card.className = "product-card";
  const body = addText(card, "div", "");
  body.className = "product-body";
  const icon = document.createElement("img");
  const symbol = productIcon(item);
  icon.src = isBusinessView() ? "/icons/" + symbol + ".svg" : "/catalog-art/" + catalogArt(item) + ".webp";
  icon.alt = "";
  icon.loading = "lazy";
  icon.width = icon.height = 22;
  const tile = addText(body, "span", "");
  tile.className = "product-icon " + symbol;
  tile.append(icon);
  const copy = addText(body, "div", "");
  if (item.vendor) addText(copy, "p", item.vendor).className = "product-vendor";
  addText(copy, "h2", item.name);
  if (item.reason) addText(copy, "p", item.reason).className = "product-reason";
  const facts = addText(copy, "ul", "");
  facts.className = "product-facts";
  for (const key of ["Версия", "Срок действия", "Модель лицензирования", "Тип лицензии", "Платформа", "Минимальная покупка"]) {
    const value = attributeValue(item, key);
    if (value) addText(facts, "li", key + " · " + value);
  }
  facts.hidden = !facts.children.length;
  const footer = addText(card, "div", "");
  footer.className = "product-footer";
  const offer = addText(footer, "div", "");
  offer.className = "product-offer";
  addText(offer, "strong", priceLabel(item));
  const availability = addText(offer, "p", availabilityLabel(item));
  availability.className = "product-availability";
  availability.dataset.available = String(item.available);
  const actions = addText(footer, "div", "");
  actions.className = "card-actions";
  const button = addText(actions, "button", "");
  button.type = "button";
  button.className = "secondary compare-toggle";
  button.dataset.offerId = item.offer_id;
  button.addEventListener("click", () => {
    if (selected.has(item.offer_id)) selected.delete(item.offer_id);
    else if (selected.size < comparisonLimit) selected.set(item.offer_id, item);
    syncComparison();
  });
  renderCompareButton(button);
  addProductLink(actions, item, requestId, "В каталог");
  if (context && !context.anchor && item.bundle_available) {
    filterButton(footer, isBusinessView() ? "Подобрать комплект" : "Подобрать дополнения", () => loadBundle(item, context));
    footer.lastElementChild.classList.add("bundle-toggle");
  }
  if (context) addProcurementActions(actions, item, context);
  return card;
}

function renderCompareButton(button) {
  const isSelected = selected.has(button.dataset.offerId);
  button.setAttribute("aria-pressed", String(isSelected));
  button.textContent = isSelected ? "Убрать из сравнения" : "В сравнение";
  button.disabled = selected.size >= comparisonLimit && !isSelected;
}

function syncComparison() {
  if (!selected.size) {
    if (comparisonBar.contains(document.activeElement)) query.focus();
    showComparisonBar(false);
  }
  comparisonToggle.hidden = !selected.size;
  comparisonToggle.querySelector("span").textContent = "Сравнение (" + selected.size + " из " + comparisonLimit + ")";
  openComparison.disabled = selected.size < 2;
  const summary = "Выбрано " + selected.size + " из " + comparisonLimit;
  document.querySelector("#comparison-count").textContent = summary;
  document.querySelector("#comparison-announcement").textContent = summary;
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
      if (selected.size) comparisonBar.focus();
      else query.focus();
    });
  }
  document.querySelectorAll(".compare-toggle").forEach(renderCompareButton);
}

function showComparisonBar(open) {
  comparisonBar.hidden = !open;
  comparisonToggle.setAttribute("aria-expanded", String(open));
}

comparisonToggle.addEventListener("click", () => {
  showComparisonBar(comparisonBar.hidden);
  if (!comparisonBar.hidden) comparisonBar.focus();
});
document.addEventListener("click", event => {
  const path = event.composedPath();
  if (!path.includes(comparisonBar) && !path.includes(comparisonToggle)) showComparisonBar(false);
});
comparisonBar.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  showComparisonBar(false);
  comparisonToggle.focus();
});
comparison.addEventListener("close", () => comparisonToggle.focus());

openComparison.addEventListener("click", () => {
  if (selected.size < 2) return;
  const items = Array.from(selected.values());
  const content = document.querySelector("#comparison-content");
  content.replaceChildren();
  const table = document.createElement("table");
  table.dataset.items = items.length;
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
    if (new Set(values.filter(value => value !== null)).size > 1) row.className = "difference";
    addText(row, "th", label).scope = "row";
    for (const value of values) addText(row, "td", value ?? "Нет данных в каталоге");
  }
  content.append(table);
  showComparisonBar(false);
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
  if (data.filter_diagnostics.requirements_unmet?.length) {
    addText(filterDetails, "p", "У части предложений не подтверждены условия: " +
      data.filter_diagnostics.requirements_unmet.join(", ") + ".");
    if (!data.results.length && !data.alternatives.length) {
      filterButton(filterDetails, "Изменить запрос", () => {
        query.focus();
        query.select();
      });
    }
  }
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
  suggestionTimer = undefined;
  activeSuggestion = undefined;
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

function scheduleSuggestions() {
  restoreForNewQuery();
  closeSuggestions();
  const prefix = query.value.trim();
  if (prefix.length >= 2 && prefix.length <= 120 && /[\p{L}\p{N}]/u.test(prefix)) {
    suggestionTimer = setTimeout(() => fetchSuggestions(prefix), 180);
  }
}

query.addEventListener("input", event => {
  if (event.isComposing) closeSuggestions();
  else scheduleSuggestions();
});
query.addEventListener("compositionend", scheduleSuggestions);
query.addEventListener("blur", closeSuggestions);
query.addEventListener("keydown", event => {
  if (event.isComposing) return;
  if (event.key === "Escape") {
    if (suggestionTimer || activeSuggestion || !suggestions.hidden) event.preventDefault();
    closeSuggestions();
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    if (activeOption >= 0) acceptSuggestion(suggestions.children[activeOption].textContent);
    else form.requestSubmit();
    return;
  }
  if (suggestions.hidden) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
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

function showNextResults(focusNew = false) {
  const {items, requestId, context} = resultSet;
  const start = results.children.length;
  const cards = items.slice(start, start + pageSize).map(item => productCard(item, requestId, context));
  results.append(...cards);
  const shown = results.children.length;
  pagination.hidden = shown === items.length;
  progress.textContent = "Показано " + shown + " из " + items.length;
  if (focusNew && cards.length) {
    const firstTitle = cards[0].querySelector("h2");
    firstTitle.tabIndex = -1;
    firstTitle.focus();
  }
}

moreResults.addEventListener("click", () => showNextResults(true));

const selectedCategories = () => new Set(Array.from(categoryOptions.querySelectorAll(":checked"), field => field.value));
let appliedFilterCount = 0;

function renderCategories(categories, previouslySelected) {
  categoryOptions.replaceChildren();
  for (const {id, name} of categories) {
    const label = addText(categoryOptions, "label", "");
    const field = document.createElement("input");
    field.type = "checkbox";
    field.value = id;
    field.checked = previouslySelected.has(id);
    label.append(field, document.createTextNode(name));
  }
  categoryFilters.hidden = !categories.length;
}

function renderOrderedResults() {
  if (!resultSet) return;
  const {allItems} = resultSet;
  const categories = selectedCategories();
  const items = allItems.filter(item => !categories.size || categories.has(item.category_id));
  if (sortResults.value !== "relevance") {
    const direction = sortResults.value === "price-asc" ? 1 : -1;
    items.sort((first, second) => {
      if (first.price == null || second.price == null) return Number(first.price == null) - Number(second.price == null);
      return direction * (Number(first.price) - Number(second.price));
    });
  }
  resultSet.items = items;
  results.replaceChildren();
  showNextResults();
  sortControl.hidden = !items.length;
  document.querySelector("#reset-filters").disabled = !categories.size && !appliedFilterCount;
  const count = appliedFilterCount + Number(categories.size > 0);
  filterCount.textContent = count ? " · " + count : "";
  filterCount.hidden = !count;
  if (items.length && !items.some(item => item.available) && resultSet.clarification) {
    status.textContent = resultSet.clarification;
  } else if (items.length) {
    const noun = new Intl.PluralRules("ru").select(items.length);
    status.textContent = "Найдено " + items.length + " " + ({one: "товар", few: "товара", many: "товаров"}[noun] || "товара");
  } else {
    status.textContent = spellingOptions.hidden ? resultSet.clarification || "Уточните запрос" : "";
  }
}

categoryOptions.addEventListener("change", renderOrderedResults);
sortResults.addEventListener("change", renderOrderedResults);
document.querySelector("#reset-filters").addEventListener("click", () => {
  for (const key of Object.keys(catalogFilterNames)) {
    const field = form.elements[key];
    if (field.type === "checkbox") field.checked = false;
    else field.value = "";
  }
  for (const field of categoryOptions.querySelectorAll(":checked")) field.checked = false;
  validatePrice();
  if (query.value.trim() && form.checkValidity()) form.requestSubmit();
});

if (matchMedia("(min-width: 62rem)").matches) filterPanel.open = true;

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
  resultSet = undefined;
  sortControl.hidden = true;
  results.replaceChildren();
  pagination.hidden = true;
  correction.hidden = true;
  spellingOptions.hidden = true;
  spellingButtons.replaceChildren();
  for (const section of [filterSuggestions, filterDetails]) {
    section.replaceChildren();
    section.hidden = true;
  }
  renderSection("alternatives", []);
  renderSection("recommendations", []);
  const entered = query.value.trim();
  if (entered.length < 2 || !/[\p{L}\p{N}]/u.test(entered)) {
    status.textContent = "Введите название, бренд или артикул от двух символов.";
    return;
  }
  status.textContent = "Ищем товары…";
  const params = new URLSearchParams(new FormData(form));
  for (const key of Object.keys(catalogFilterNames)) if (!params.get(key)) params.delete(key);
  params.set("track", "true");
  params.set("expanded", "true");
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
    const context = {version: data.catalog_version, platform: params.get("platform"), query: entered};
    const matchingCategorySelection = form.dataset.lastQuery === entered ? selectedCategories() : new Set();
    if (form.dataset.lastQuery !== entered) sortResults.value = "relevance";
    form.dataset.lastQuery = entered;
    renderCategories(data.result_categories ?? [], matchingCategorySelection);
    resultSet = {allItems: data.results, requestId, context, clarification: data.clarification};
    renderSection("alternatives", data.alternatives, undefined, context);
    renderSection("recommendations", data.recommendations, requestId, context);
    const active = Object.keys(data.applied_filters);
    appliedFilterCount = active.length - Number(active.includes("price_min") && active.includes("price_max"));
    showFilters(data);
    correction.hidden = !data.corrected_query;
    correction.querySelector("strong").textContent = data.corrected_query ? "«" + data.corrected_query + "»" : "";
    for (const option of data.spelling_suggestions ?? []) {
      filterButton(spellingButtons, option, () => acceptSuggestion(option));
    }
    spellingOptions.hidden = !spellingButtons.children.length;
    renderOrderedResults();
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = "Поиск временно недоступен. Попробуйте ещё раз.";
  }
});
