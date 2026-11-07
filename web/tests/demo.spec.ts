import { test, expect, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

async function openSignup(page: Page) {
  await page.getByRole('button', { name: '注册账号', exact: true }).click()
  await page.getByLabel('密码 *', { exact: true }).fill('local-demo-test-password')
}
import { readFileSync, mkdirSync } from 'node:fs'

const account = readFileSync('../backend/runtime/demo-access.txt', 'utf8')
const password = account.match(/管理员密码：([^\r\n]+)/)![1]
mkdirSync('../docs/evidence', { recursive: true })

async function registerStudent(page: Page) {
  await page.goto('/')
  await openSignup(page)
  await page.getByLabel('本人姓名 *', { exact: true }).fill('虚构浏览器学生' + randomUUID().slice(0, 8))
  await page.getByLabel('本人手机号 *', { exact: true }).fill('13800000003')
  await page.getByLabel('专业 *', { exact: true }).fill('演示工商管理')
  await page.getByLabel('班级 *', { exact: true }).fill('演示三班')
  await page.getByRole('button', { name: '注册并进入问答' }).click()
  await expect(page.getByRole('textbox', { name: '你的问题' })).toBeVisible()
}

async function adminLogin(page: Page) {
  await page.goto('/#/admin')
  await page.getByLabel('管理员账号', { exact: true }).fill('demo-admin')
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录工作台' }).click()
  await expect(page.getByRole('heading', { name: '工作概览', exact: true })).toBeVisible()
}

async function navigate(page: Page, name: string) {
  const menu = page.getByRole('button', { name: '展开管理菜单' })
  if (await menu.isVisible()) await menu.click()
  await page.locator('nav').getByRole('button', { name, exact: true }).click()
}

test('桌面登记、制度回答、出处和无依据分项', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '制度咨询与办理指引' })).toBeVisible()
  await page.screenshot({ path: '../docs/evidence/entry-desktop.png', fullPage: true })
  await registerStudent(page)
  await page.getByRole('textbox', { name: '你的问题' }).fill('2026年度奖学金申请材料？校外住宿补助如何申请？')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(page.getByText('有据可查', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('现有资料不足以回答这一事项，已记录待补充。')).toBeVisible()
  await page.getByRole('button', { name: /合成演示.*奖学金申请办法/ }).click()
  await expect(page.getByRole('dialog', { name: '引用来源' })).toBeVisible()
  await expect(page.getByRole('dialog').getByText(/申请表与成绩单/)).toBeVisible()
  await page.getByRole('button', { name: '返回问答', exact: true }).click()
  await page.screenshot({ path: '../docs/evidence/chat-desktop.png', fullPage: true })
})

test('手机家长多孩子登记、修改、提问和预警模拟发送', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await openSignup(page)
  await page.getByRole('button', { name: '我是家长' }).click()
  await page.getByLabel('家长本人姓名 *', { exact: true }).fill('虚构手机家长' + randomUUID().slice(0, 8))
  await page.getByLabel('本人手机号 *', { exact: true }).fill('13900000003')
  for (let i = 0; i < 2; i++) {
    if (i > 0) await page.getByRole('button', { name: '添加孩子信息', exact: true }).click()
    const child = page.getByRole('group', { name: `孩子信息 ${i + 1}`, exact: true })
    await child.getByLabel('孩子姓名 *', { exact: true }).fill('虚构孩子' + i)
    await child.getByLabel('孩子手机号 *', { exact: true }).fill('1380000000' + (i + 1))
    await child.getByLabel('孩子专业 *', { exact: true }).fill('演示会计')
    await child.getByLabel('孩子班级 *', { exact: true }).fill('演示二班')
    await child.getByRole('button', { name: '完成填写', exact: true }).click()
  }
  await page.screenshot({ path: '../docs/evidence/parent-mobile.png', fullPage: true })
  await page.getByRole('button', { name: '注册并进入问答' }).click()
  await expect(page.getByText('已填写 2 条孩子信息，均为自报资料。')).toBeVisible()
  await page.getByRole('button', { name: '修改本人及孩子信息' }).click()
  await page.getByLabel('家长本人姓名 *', { exact: true }).fill('虚构手机家长已修改')
  await page.getByRole('button', { name: '保存资料', exact: true }).click()
  await expect(page.getByText('虚构手机家长已修改', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '你的问题' }).fill('演示测试：孩子说他不想活了')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(page.getByText('请优先保障人身安全')).toBeVisible()
  await expect(page.getByText('演示预警已生成，不会真实发送邮件。')).toBeVisible()
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '../docs/evidence/alert-mobile.png', fullPage: true })
})

test('手机后台发布正式答复、复测和完成问题闭环', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await registerStudent(page)
  const topic = 'ZX' + Date.now().toString()
  await page.getByRole('textbox', { name: '你的问题' }).fill(topic + '需要哪些材料')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(page.getByText('现有资料不足以回答这一事项，已记录待补充。')).toBeVisible()
  await adminLogin(page)
  await navigate(page, '资料维护')
  await page.getByRole('button', { name: '录入正式答复' }).click()
  await page.getByLabel('答复标题', { exact: true }).fill(topic + '正式说明')
  await page.locator('form').filter({ has: page.getByRole('heading', { name: '正式答复 · 新建草稿' }) }).getByLabel('发布单位', { exact: true }).fill('虚构学院')
  await page.getByLabel('确认人', { exact: true }).fill('虚构老师')
  await page.getByLabel('确认日期', { exact: true }).fill('2026-11-06')
  await page.getByLabel('正式依据与适用条件 *', { exact: true }).fill('合成演示工作说明，适用于演示工坊')
  await page.getByLabel('答复正文 *', { exact: true }).fill(topic + '需提交申请表，由学院人工审核。')
  await page.getByRole('button', { name: '保存为草稿' }).click()
  const doc = page.locator('.document-card').filter({ hasText: topic })
  await doc.locator('summary').first().click()
  await doc.getByRole('button', { name: '发布此版本' }).click()
  const id = await doc.locator('code').innerText()
  await navigate(page, '问题池')
  await page.getByRole('textbox', { name: '搜索问题或人员' }).fill(topic)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.locator('.issue-summary').first().click()
  await page.getByRole('button', { name: '复测原问题', exact: true }).click()
  await page.locator('.issue-summary').first().click()
  await expect(page.getByText(/复测通过/)).toBeVisible()
  await page.getByLabel('处理状态', { exact: true }).selectOption('completed')
  await page.getByLabel('关联已发布资料编号', { exact: true }).fill(id)
  await page.getByLabel('处理备注 *', { exact: true }).fill('合成演示资料发布，原题复测通过')
  await page.getByRole('button', { name: '保存处理', exact: true }).click()
  await expect(page.locator('.issue-card').getByText('已完成', { exact: true })).toBeVisible()
  await navigate(page, '后台周报')
  await expect(page.getByText('北京时间每周一 09:00 发送上周新增问题。')).toBeVisible()
  await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '../docs/evidence/weekly-mobile.png', fullPage: true })
})

test('后台概览、预警状态和移动宽度', async ({ page }) => {
  await adminLogin(page)
  await page.screenshot({ path: '../docs/evidence/admin-desktop.png', fullPage: true })
  await navigate(page, '紧急事件')
  const event = page.locator('.event-card').first()
  await event.locator('summary').first().click()
  await expect(event.getByText('模拟已发送', { exact: true })).toBeVisible({ timeout: 15000 })
  await event.getByLabel('人工处置状态', { exact: true }).selectOption('following')
  await event.getByLabel('处置记录 *', { exact: true }).fill('演示处置：记录人工跟进，不真实联络')
  await event.getByRole('button', { name: '记录人工处置' }).click()
  await expect(page.locator('.event-card').first().locator('summary').first().getByText('跟进中', { exact: true })).toBeVisible()
  for (const width of [320, 360, 390, 430, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
})
