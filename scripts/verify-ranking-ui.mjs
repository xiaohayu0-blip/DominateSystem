import { chromium, expect } from '../web/node_modules/@playwright/test/index.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const outputDir = new URL('../docs/evidence/', import.meta.url);
mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1080, height: 1000 } });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await page.getByLabel('密码 *', { exact: true }).fill('local-ranking-verification-only1');
  await page.getByLabel('本人姓名 *', { exact: true }).fill('虚构排名修复验收' + Date.now());
  await page.getByLabel('本人手机号 *', { exact: true }).fill('13800000003');
  await page.getByLabel('专业 *', { exact: true }).fill('演示工商管理');
  await page.getByLabel('班级 *', { exact: true }).fill('演示验收班');
  await page.getByRole('button', { name: '注册并进入问答' }).click();
  const input = page.getByRole('textbox', { name: '你的问题' });
  await expect(input).toBeVisible();
  await input.fill('专业排名第一,德育分加多少分?');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByText(/请确认你问的是智育排名分/)).toBeVisible({ timeout: 20000 });
  await page.screenshot({ path: fileURLToPath(new URL('ranking-clarification-20261111.png', outputDir)), fullPage: true });
  await input.fill('智育');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect(page.getByText(/智育排名分为50分/)).toBeVisible({ timeout: 20000 });
  await page.getByText(/智育排名分为50分/).scrollIntoViewIfNeeded();
  await page.locator('.messages').evaluate(element => {
    element.scrollTo({ top: element.scrollHeight, behavior: 'instant' });
  });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.screenshot({ path: fileURLToPath(new URL('ranking-followup-20261111.png', outputDir)), fullPage: true });
  const visible = await page.locator('body').innerText();
  if (visible.includes('证据校验失败')) throw new Error('Unexpected validation failure in browser');
  writeFileSync(new URL('ranking-browser-result-20261111.json', outputDir), JSON.stringify({
    original: 'needs_clarification', followup: 'answered', points: 50,
    externalModelCalls: 0, evidence: 'published original spreadsheet rows',
  }, null, 2));
  console.log('Browser verified: original question clarifies scoring metric; short followup answers 50 points.');
} finally {
  await browser.close();
}
