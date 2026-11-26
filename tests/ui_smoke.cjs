const assert = require("node:assert/strict");
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
    await page.goto("http://127.0.0.1:8000/");
    const input = page.getByRole("combobox");
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
    console.log("Проверены подсказки, клавиатура, две карточки и сохранение выбора между запросами");
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exit(1); });
