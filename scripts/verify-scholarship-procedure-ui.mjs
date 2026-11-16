import { chromium, expect } from '../web/node_modules/@playwright/test/index.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const outputDir = new URL('../docs/evidence/', import.meta.url);
mkdirSync(outputDir, { recursive: true });
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1080, height: 1300 } });
  let completed;
  page.on('response', async response => {
    if (/\/api\/questions\/[a-f0-9]+$/.test(response.url()) && response.request().method() === 'GET') {
      const result = await response.json().catch(() => null);
      if (result?.status === 'complete') completed = result;
    }
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await page.getByLabel('密码 *', { exact: true }).fill('local-procedure-verification-only');
  await page.getByLabel('本人姓名 *', { exact: true }).fill('虚构励志流程验收' + Date.now());
  await page.getByLabel('本人手机号 *', { exact: true }).fill('13800000003');
  await page.getByLabel('专业 *', { exact: true }).fill('演示工商管理');
  await page.getByLabel('班级 *', { exact: true }).fill('演示验收班');
  await page.getByRole('button', { name: '注册并进入问答' }).click();
  await page.getByRole('textbox', { name: '你的问题' }).fill('国家励志奖学金的评审程序有哪些?');
  await page.getByRole('button', { name: '发送问题', exact: true }).click();
  await expect.poll(() => completed?.status, { timeout: 65000 }).toBe('complete');
  writeFileSync(new URL('scholarship-procedure-real-browser-20261111.json', outputDir), JSON.stringify(completed, null, 2));
  const item = completed.answer.items[0];
  expect(item.status).toBe('answered');
  expect(item.content.length).toBeLessThanOrEqual(500);
  for (const stage of ['政策宣讲', '本人申请', '资格审查', '班级评议', '学院初评', '学校审定']) {
    expect(item.content).toContain(stage);
  }
  await expect(page.getByText('有据可查', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: fileURLToPath(new URL('scholarship-procedure-real-browser-20261111.png', outputDir)), fullPage: true });
  console.log(JSON.stringify({ status: item.status, chars: item.content.length, sources: item.citations.length,
    realModel: true, questionId: completed.id }));
} finally {
  await browser.close();
}
