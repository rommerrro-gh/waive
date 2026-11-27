const approvalKey = "waive-approval-v1";
const workplaces = document.querySelector("#workplaces");
const approval = document.querySelector("#approval");
const approvalItems = document.querySelector("#approval-items");
const csvButton = document.querySelector("#export-approval");
const quotePreview = document.querySelector("#quote-preview");
const quote = document.querySelector("#quote");
const quoteText = document.querySelector("#quote-text");
const quoteForm = document.querySelector("#quote-form");
const quoteSend = document.querySelector("#quote-send");
const quoteDelivery = document.querySelector("#quote-delivery");
const quoteStatus = document.querySelector("#quote-status");
let quoteConfig;
let quoteOpening = 0;
const bundle = document.querySelector("#bundle");
const bundleMessage = document.querySelector("#bundle-message");
const pageTitle = document.querySelector("#page-title");
const businessLink = document.querySelector("#business-link");
const backToSearch = document.querySelector("#back-to-search");
const businessApproval = document.querySelector("#business-approval");
let bundleRequest;
const saved = new Map(readLocal(approvalKey, []).map(entry => [entry.item.offer_id, entry]));

function readLocal(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

function saveLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function isBusinessView() {
  return location.pathname === "/business";
}

function updateView({focus = false} = {}) {
  const business = isBusinessView();
  document.body.dataset.view = business ? "business" : "search";
  document.querySelector(".license-filters").open = false;
  pageTitle.textContent = business ? "Закупки для бизнеса" : "Поиск по каталогу Softline";
  document.title = "Waive | " + pageTitle.textContent;
  businessApproval.hidden = backToSearch.hidden = !business;
  businessLink.hidden = business;
  if (!business) clearBundle();
  if (focus) {
    pageTitle.tabIndex = -1;
    pageTitle.focus();
  }
}

for (const link of [businessLink, backToSearch]) {
  link.search = location.search;
  link.addEventListener("click", event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.pushState(null, "", link.href);
    updateView({focus: true});
  });
}
window.addEventListener("popstate", () => updateView());

function requirements(entry) {
  const checks = [];
  if (entry.count) checks.push("Подтвердить покрытие " + entry.count + " рабочих мест");
  if (entry.item.price == null) checks.push("Уточнить цену");
  if (!entry.item.available) checks.push("Уточнить наличие и срок поставки");
  return checks;
}

function positionId(item) {
  return item.vendor_code ? "арт. " + item.vendor_code : "ID каталога " + item.offer_id;
}

function renderApproval() {
  document.querySelector("#approval-count").textContent = "Список на согласование · " + saved.size;
  document.querySelector("#approval-empty").hidden = saved.size > 0;
  approvalItems.replaceChildren();
  for (const entry of saved.values()) {
    const {item, anchor} = entry;
    const row = addText(approvalItems, "li", "");
    addText(row, "strong", item.name);
    addText(row, "p", positionId(item) + " · " + priceLabel(item));
    if (anchor) addText(row, "p", "Дополнение к «" + anchor + "» · " + item.reason);
    for (const check of requirements(entry)) addText(row, "p", check);
    const actions = addText(row, "div", "");
    actions.className = "card-actions";
    addProductLink(actions, item);
    filterButton(actions, "Убрать позицию", () => toggleApproval(entry));
  }
  csvButton.disabled = quotePreview.disabled = saved.size === 0;
  if (!saved.size) quote.hidden = true;
  document.querySelectorAll("button[data-approval-id]").forEach(updateApprovalButton);
}

function toggleApproval(entry) {
  const id = entry.item.offer_id;
  if (saved.has(id)) saved.delete(id);
  else {
    saved.set(id, entry);
    approval.open = true;
  }
  saveLocal(approvalKey, Array.from(saved.values()));
  quote.hidden = true;
  renderApproval();
}

function updateApprovalButton(button) {
  const included = saved.has(button.dataset.approvalId);
  button.textContent = included ? "Убрать из списка" : "На согласование";
  button.setAttribute("aria-pressed", String(included));
}

function addProcurementActions(actions, item, context) {
  const group = addText(actions, "div", "");
  group.className = "card-actions procurement-actions";
  const button = addText(group, "button", "");
  button.type = "button";
  button.className = "secondary";
  button.dataset.approvalId = item.offer_id;
  button.addEventListener("click", () => {
    if (!saved.has(item.offer_id) && !workplaces.checkValidity()) {
      approval.open = true;
      workplaces.reportValidity();
      return;
    }
    toggleApproval({item, count: workplaces.value, anchor: context.anchor});
  });
  updateApprovalButton(button);
}

function clearBundle() {
  bundleRequest?.abort();
  bundle.hidden = true;
  bundle.querySelector(".cards").replaceChildren();
}

async function loadBundle(anchor, context) {
  clearBundle();
  const controller = new AbortController();
  bundleRequest = controller;
  bundle.hidden = false;
  bundleMessage.textContent = "Подбираем дополнения к «" + anchor.name + "»…";
  bundle.querySelector("h2").focus({preventScroll: true});
  bundle.scrollIntoView({block: "start"});
  const params = new URLSearchParams({offer_id: anchor.offer_id, catalog_version: context.version});
  if (context.platform) params.set("platform", context.platform);
  if (context.query) params.set("q", context.query);
  if (isBusinessView() && workplaces.value && workplaces.checkValidity()) params.set("seats", workplaces.value);
  try {
    const response = await fetch("/bundle?" + params, {signal: controller.signal});
    const data = await response.json();
    if (controller.signal.aborted) return;
    if (!response.ok) throw new Error(data.detail);
    bundleMessage.textContent = data.items.length
      ? "Дополнения к «" + anchor.name + "». Основания для подбора указаны на карточках."
      : data.status === "not_applicable"
        ? "Для этой категории дополнения не предусмотрены."
        : data.status === "anchor_unavailable"
          ? "Товар сейчас недоступен. Выберите доступную позицию для подбора дополнений."
        : "В каталоге нет дополнений с подтверждённой совместимостью для этой позиции.";
    if (!data.items.length) return;
    const cards = [
      productCard({...data.anchor, bundle_available: false}, undefined, context),
      ...data.items.map(item => productCard(item, undefined, {...context, anchor: anchor.name})),
    ];
    bundle.querySelector(".cards").replaceChildren(...cards);
  } catch (error) {
    if (error.name !== "AbortError") bundleMessage.textContent = error.message || "Не удалось подобрать комплект.";
  }
}

function download(name, content, type) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], {type}));
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function csvCell(value) {
  const text = String(value ?? "");
  const safe = /^[\s\uFEFF]*[=+@-]|^[\t\r\n]/.test(text) ? "'" + text : text;
  return '"' + safe.replaceAll('"', '""') + '"';
}

csvButton.addEventListener("click", () => {
  const rows = [["Предложение", "Артикул", "ID каталога", "Цена из каталога", "Наличие",
    "Платформа", "Срок действия", "Тип лицензии", "Рабочие места", "Дополнение к", "Что уточнить", "Ссылка"]];
  for (const {item, count, anchor} of saved.values()) {
    rows.push([item.name, item.vendor_code, item.offer_id, priceLabel(item), availabilityLabel(item),
      attributeValue(item, "Платформа", "Поддерживаемые ОС"), attributeValue(item, "Срок действия"),
      attributeValue(item, "Тип лицензии"), count, anchor,
      requirements({item, count}).join(". "), item.url]);
  }
  download("waive-approval.csv", "\uFEFF" + rows.map(row => row.map(csvCell).join(";")).join("\r\n"),
    "text/csv;charset=utf-8");
});

function quoteBody(contact) {
  const lines = ["Здравствуйте!", "", "Просим подготовить коммерческое предложение по следующим позициям:", ""];
  if (contact) {
    lines.push("Имя: " + contact.name, "Компания: " + contact.company, "Почта: " + contact.email,
      "Телефон: " + contact.phone, "");
  }
  for (const [index, {item, count, anchor}] of Array.from(saved.values()).entries()) {
    lines.push((index + 1) + ". " + item.name + " (" + positionId(item) + ")");
    if (anchor) lines.push("   Дополнение к «" + anchor + "».");
    for (const [label, value] of [
      ["Платформа", attributeValue(item, "Платформа", "Поддерживаемые ОС")],
      ["Срок лицензии", attributeValue(item, "Срок действия")],
      ["Тип лицензии", attributeValue(item, "Тип лицензии")],
    ]) if (value) lines.push("   " + label + " — " + value + ".");
    for (const check of requirements({item, count})) lines.push("   " + check + ".");
    if (item.url) lines.push("   " + item.url);
    lines.push("");
  }
  lines.push("Пожалуйста, укажите итоговую стоимость с НДС, срок поставки и срок действия предложения.");
  return lines.join("\n");
}

function updateQuoteDelivery() {
  quoteSend.textContent = quoteConfig.automatic_delivery ? "Отправить запрос" : "Открыть письмо";
  quoteDelivery.textContent = quoteConfig.automatic_delivery
    ? "Контакты и выбранные позиции будут отправлены на " + quoteConfig.recipient + "."
    : "Откроем письмо в вашем почтовом приложении. " +
      (quoteConfig.recipient ? "Проверьте адрес и отправьте письмо." : "Укажите адрес получателя и отправьте письмо.");
}

quotePreview.addEventListener("click", async () => {
  const opening = ++quoteOpening;
  quoteSend.disabled = true;
  quoteStatus.textContent = "";
  quoteDelivery.textContent = "Проверяем способ отправки…";
  quoteText.value = quoteBody();
  quote.hidden = false;
  quoteForm.elements.name.focus();
  let config;
  try {
    const response = await fetch("/quote/config");
    if (response.ok) config = await response.json();
  } catch {
    // На стенде без почтовой настройки запрос можно открыть в своём приложении.
  }
  if (opening !== quoteOpening || quote.hidden) return;
  quoteConfig = config ?? {automatic_delivery: false, recipient: ""};
  updateQuoteDelivery();
  quoteSend.disabled = false;
});

quoteForm.addEventListener("input", event => event.target.setCustomValidity?.(""));
quoteForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!quoteConfig || !saved.size || quoteSend.disabled) return;
  const contact = Object.fromEntries(Array.from(new FormData(quoteForm), ([key, value]) => [key, value.trim()]));
  for (const key of ["name", "company"]) {
    if (!contact[key]) {
      quoteForm.elements[key].setCustomValidity("Заполните поле");
      quoteForm.elements[key].reportValidity();
      return;
    }
  }
  const digits = contact.phone.replace(/\D/g, "").length;
  if (!/^\+?[\d() .-]+$/.test(contact.phone) || digits < 7 || digits > 15) {
    quoteForm.elements.phone.setCustomValidity("Укажите корректный телефон");
    quoteForm.elements.phone.reportValidity();
    return;
  }
  quoteText.value = quoteBody(contact);
  if (!quoteConfig.automatic_delivery) {
    const subject = encodeURIComponent("Запрос коммерческого предложения");
    const recipient = encodeURIComponent(quoteConfig.recipient).replace("%40", "@");
    window.location.href = "mailto:" + recipient + "?subject=" + subject +
      "&body=" + encodeURIComponent(quoteText.value);
    quoteStatus.textContent = "Запрос подготовлен. Отправьте письмо из почтового приложения.";
    return;
  }
  quoteSend.disabled = true;
  quoteStatus.textContent = "Отправляем запрос…";
  try {
    const items = Array.from(saved.values(), ({item, count}) => ({
      offer_id: item.offer_id, workplaces: Number(count) || null,
    }));
    const response = await fetch("/quote/request", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...contact, items}),
    });
    const result = await response.json();
    if (response.status === 502 || response.status === 503) {
      quoteConfig.automatic_delivery = false;
      updateQuoteDelivery();
    }
    if (!response.ok || !result.sent) throw new Error(typeof result.detail === "string" ? result.detail : "Проверьте контакты и список позиций");
    quoteStatus.textContent = "Запрос отправлен.";
    quoteForm.reset();
  } catch (error) {
    quoteStatus.textContent = error.message || "Не удалось отправить запрос";
  } finally {
    quoteSend.disabled = false;
  }
});

document.querySelector("#quote-download").addEventListener("click", () => {
  download("waive-quote-request.txt", quoteText.value, "text/plain;charset=utf-8");
});
renderApproval();
updateView();
