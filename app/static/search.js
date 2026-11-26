const form = document.querySelector("#search");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const query = document.querySelector("#query");
const suggestions = document.querySelector("#suggestions");
const comparisonBar = document.querySelector("#comparison-bar");
const comparisonItems = document.querySelector("#comparison-items");
const openComparison = document.querySelector("#open-comparison");
const comparison = document.querySelector("#comparison");
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

function addProductLink(parent, item) {
  if (/^https?:\/\//i.test(item.url || "")) {
    addText(parent, "a", "Карточка на Softline").href = item.url;
  }
}

function renderSection(id, cards) {
  const section = document.querySelector("#" + id);
  section.hidden = !cards.length;
  section.querySelector(".cards").replaceChildren(...cards.map(productCard));
}

function productCard(item) {
  const card = document.createElement("article");
  addText(card, "h2", item.name);
  addText(card, "p", priceLabel(item) + " · " + availabilityLabel(item));
  addText(card, "p", item.reason);
  for (const key of ["Версия", "Срок действия", "Модель лицензирования", "Тип лицензии", "Минимальная покупка"]) {
    const value = attributeValue(item, key);
    if (value) addText(card, "p", key + ": " + value);
  }
  const actions = addText(card, "div", "");
  actions.className = "card-actions";
  addProductLink(actions, item);
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  closeSuggestions();
  activeSearch?.abort();
  const controller = new AbortController();
  activeSearch = controller;
  results.replaceChildren();
  renderSection("alternatives", []);
  renderSection("recommendations", []);
  status.textContent = "Ищем товары…";
  const params = new URLSearchParams(new FormData(form));
  if (document.querySelector("#lexical").checked) params.set("semantic", "false");
  try {
    const response = await fetch("/search?" + params, { signal: controller.signal });
    if (!response.ok) throw new Error("Поиск недоступен");
    const data = await response.json();
    if (controller.signal.aborted) return;
    results.replaceChildren(...data.results.map(productCard));
    renderSection("alternatives", data.alternatives);
    renderSection("recommendations", data.recommendations);
    status.textContent = data.clarification || (data.search_mode === "hybrid" ?
      "Результаты поиска по тексту и смыслу" : "Результаты текстового поиска");
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = "Поиск временно недоступен. Попробуйте ещё раз.";
  }
});
