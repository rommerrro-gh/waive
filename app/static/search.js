const form = document.querySelector("form");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
let activeSearch;

function renderSection(id, cards) {
  const section = document.getElementById(id);
  section.hidden = !cards.length;
  section.querySelector(".cards").replaceChildren(...cards.map(productCard));
}

function addText(parent, tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  parent.append(element);
  return element;
}

function productCard(item) {
  const card = document.createElement("article");
  addText(card, "h2", item.name);
  const price = item.price == null ? "Цена не указана" :
    new Intl.NumberFormat("ru-RU", { style: "currency", currency: item.currency === "RUR" ? "RUB" : item.currency || "RUB" })
      .format(Number(item.price));
  addText(card, "p", price + " · " + (item.available ? "В наличии" : "Недоступно"));
  addText(card, "p", item.reason);
  for (const key of ["Версия", "Срок действия", "Модель лицензирования", "Тип лицензии", "Минимальная покупка"]) {
    const value = item.attributes[key];
    if (value && value.toLowerCase() !== "не определено") addText(card, "p", key + ": " + value);
  }
  if (/^https?:\/\//i.test(item.url || "")) addText(card, "a", "Карточка на Softline").href = item.url;
  return card;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
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
    results.replaceChildren(...data.results.map(productCard));
    renderSection("alternatives", data.alternatives);
    renderSection("recommendations", data.recommendations);
    status.textContent = data.clarification || (data.search_mode === "hybrid" ?
      "Результаты поиска по тексту и смыслу" : "Результаты текстового поиска");
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = "Поиск временно недоступен. Попробуйте ещё раз.";
  }
});
