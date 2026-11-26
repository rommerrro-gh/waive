const assert = require("node:assert/strict");
const { readFile } = require("node:fs/promises");
const { chromium } = require("playwright");

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BIN,
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    page.on("pageerror", error => { throw error; });
    await page.goto(new URL("/?demo=true", process.env.WAIVE_TEST_URL || "http://127.0.0.1:8000").href);
    const input = page.getByRole("combobox", {name: "Название, бренд или артикул"});
    const filters = page.locator("#search-filters > summary");
    const stock = page.getByRole("checkbox", {name: "В наличии"});
    assert.equal(await stock.isVisible(), false);
    await filters.click();
    assert.equal(await stock.isVisible(), true);
    assert.equal(await page.getByRole("spinbutton", {name: "Цена от, ₽"}).isVisible(), true);
    await filters.click();
    assert.equal(await stock.isVisible(), false);
    await input.fill("Kaspersky");
    await page.getByRole("button", {name: "Найти", exact: true}).click();
    const more = page.getByRole("button", {name: "Показать ещё", exact: true});
    const cards = page.locator("#results article");
    await more.waitFor();
    assert.equal(await cards.count(), 12);
    const firstPage = await cards.locator("h2").allTextContents();
    await more.click();
    assert.ok(await cards.count() > firstPage.length);
    assert.deepEqual((await cards.locator("h2").allTextContents()).slice(0, 12), firstPage);
    await input.fill("крипто пр");
    await page.getByRole("option", { name: /КриптоПро CSP TLS/i }).waitFor();
    assert.equal(await input.getAttribute("aria-expanded"), "true");
    await input.press("Escape");
    assert.equal(await input.getAttribute("aria-expanded"), "false");

    await input.fill("AnyD");
    await page.getByRole("option", { name: "AnyDesk", exact: true }).waitFor();
    await input.press("ArrowDown");
    const active = await input.getAttribute("aria-activedescendant");
    assert.ok(active);
    const selectedName = await page.locator("#" + active).textContent();
    await input.press("Enter");
    assert.equal(await input.inputValue(), selectedName);
    await page.locator("#results article").first().waitFor();
    await input.fill("антивирус для рабочих станций компании");
    await page.getByRole("button", { name: "Найти" }).click();
    await page.locator("#results article").nth(2).waitFor();
    assert.equal(await page.getByRole("button", {name: "На согласование"}).count(), 0);
    const buttons = page.locator("#results .compare-toggle");
    await buttons.nth(0).click();
    assert.equal(await page.locator("#open-comparison").isDisabled(), true);
    await buttons.nth(1).click();
    assert.equal(await buttons.nth(2).isDisabled(), true);
    const chosen = await page.locator("#comparison-items span").allTextContents();
    await page.getByRole("button", { name: "Сравнить предложения" }).click();
    const dialog = page.getByRole("dialog", { name: "Сравнение предложений" });
    await dialog.waitFor();
    const headers = await dialog.locator("thead th").allTextContents();
    for (const [i, name] of chosen.entries()) assert.ok(headers[i + 1].includes(name));
    assert.equal(await dialog.locator("tbody tr").count(), 8);
    await dialog.getByRole("button", { name: "Закрыть" }).click();

    await input.fill("КриптоПро CSP 5.0");
    await page.getByRole("button", { name: "Найти" }).click();
    await page.locator("#results article").first().waitFor();
    assert.deepEqual(await page.locator("#comparison-items span").allTextContents(), chosen);
    await page.locator("#comparison-items button").first().click();
    assert.equal(await page.locator("#open-comparison").isDisabled(), true);
    await input.fill("антивирус для рабочих станций на год");
    await page.getByRole("button", { name: "Найти" }).click();
    await page.getByRole("button", { name: "Применить: 12 месяцев" }).click();
    await page.locator("#results article").first().waitFor();
    assert.equal(await page.locator("select[name=term]").inputValue(), "12");
    await page.getByRole("link", {name: "Закупки для бизнеса", exact: true}).click();
    await page.waitForURL("**/business?demo=true");
    assert.equal(await input.inputValue(), "антивирус для рабочих станций на год");
    await filters.click();
    await page.locator("select[name=platform]").selectOption("windows");
    await page.getByRole("spinbutton", {name: "Рабочих мест"}).fill("50");
    await page.getByRole("button", {name: "Сохранить профиль закупки"}).click();
    await page.reload();
    assert.equal(await page.locator("select[name=platform]").inputValue(), "windows");
    assert.equal(await page.getByRole("spinbutton", {name: "Рабочих мест"}).inputValue(), "50");
    await input.fill("антивирус для рабочих станций");
    await page.getByRole("button", {name: "Найти"}).click();
    const first = page.locator("#results article").first();
    await first.getByRole("button", {name: "На согласование"}).click();
    await page.getByRole("button", {name: "Черновик запроса КП"}).click();
    assert.match(await page.locator("#quote-text").inputValue(), /покрытие 50 рабочих мест/);
    for (const [button, expected, content] of [
      ["Скачать CSV", "waive-approval.csv", /"Рабочие места".*"50"/s],
      ["Скачать черновик TXT", "waive-quote-request.txt", /покрытие 50 рабочих мест/],
    ]) {
      const started = page.waitForEvent("download");
      await page.getByRole("button", {name: button, exact: true}).click();
      const result = await started;
      assert.equal(result.suggestedFilename(), expected);
      assert.match(await readFile(await result.path(), "utf8"), content);
    }
    await filters.click();
    await page.locator("select[name=platform]").selectOption("linux");
    await input.fill("офисный пакет");
    assert.equal(await page.locator("select[name=platform]").inputValue(), "windows");
    await page.reload();
    await page.getByText("Список на согласование · 1").waitFor();
    assert.ok(process.env.BUNDLE_SMOKE_OFFER);
    await page.getByRole("button", {name: "Удалить профиль"}).click();
    await filters.click();
    await page.locator("select[name=platform]").selectOption("");
    await page.locator("select[name=term]").selectOption("");
    await input.fill(process.env.BUNDLE_SMOKE_OFFER);
    await page.getByRole("button", {name: "Найти"}).click();
    await page.locator("#results article").first().getByRole("button", {name: "Подобрать комплект"}).click();
    assert.equal(await more.isVisible(), false);
    const bundle = page.getByRole("region", {name: "Комплект для компании"});
    await bundle.getByText(/проверена совместимость по платформе/).waitFor();
    await bundle.locator("article").nth(1).getByRole("button", {name: "На согласование"}).click();
    await page.getByText("Список на согласование · 2").waitFor();
    await page.getByRole("link", {name: "К поиску по каталогу", exact: true}).click();
    assert.equal(await page.getByText("Список на согласование · 2").isVisible(), false);
    assert.equal(await input.inputValue(), process.env.BUNDLE_SMOKE_OFFER);
    await page.getByRole("link", { name: "Аналитика поиска" }).click();
    await page.getByLabel("Ключ аналитики").fill(process.env.ANALYTICS_TOKEN);
    await page.getByLabel("Только демо-запросы").check();
    await page.getByRole("button", {name: "Обновить"}).click();
    await page.getByRole("cell", {name: /Сузило выдачу условие «срок лицензии»/}).first().waitFor();
    console.log("Проверены подсказки, сравнение, условия закупки и отчёт по демо-запросам");
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exit(1); });
