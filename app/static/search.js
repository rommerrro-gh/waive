const form = document.querySelector("form");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
let activeSearch;

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
  for (const key of ["Версия", "Срок действия", "Модель лицензирования"]) {
    if (item.attributes[key]) addText(card, "p", key + ": " + item.attributes[key]);
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
  status.textContent = "Ищем товары…";
  const params = new URLSearchParams(new FormData(form));
  try {
    const response = await fetch("/search?" + params, { signal: controller.signal });
    if (!response.ok) throw new Error("Поиск недоступен");
    const data = await response.json();
    results.replaceChildren(...data.results.map(productCard));
    status.textContent = data.clarification || "Результаты поиска";
  } catch (error) {
    if (error.name !== "AbortError") status.textContent = "Поиск временно недоступен. Попробуйте ещё раз.";
  }
});
