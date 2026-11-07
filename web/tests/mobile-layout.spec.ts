import { test, expect, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'

async function openSignup(page: Page) {
  await page.getByRole('button', { name: '注册账号', exact: true }).click()
  await page.getByLabel('密码 *', { exact: true }).fill('local-demo-test-password')
}

test('手机入口首屏可直接完成学生登记，说明默认折叠', async ({ page }) => {
  for (const width of [320, 390, 430]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/')
    await openSignup(page)
    const submit = page.getByRole('button', { name: '注册并进入问答' })
    await expect(submit).toBeVisible()
    const box = await submit.boundingBox()
    expect(box!.y + box!.height).toBeLessThanOrEqual(844)
    await expect(page.locator('.service-disclosure')).not.toHaveAttribute('open')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    if (width === 390) await page.screenshot({ path: '../docs/evidence/entry-mobile-compact.png', fullPage: true })
  }
})

test('孩子折叠保留草稿，提交时自动展开未填完整的信息', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await openSignup(page)
  await page.getByRole('button', { name: '我是家长' }).click()
  await page.getByLabel('家长本人姓名 *', { exact: true }).fill('虚构表单测试家长' + randomUUID().slice(0, 8))
  await page.getByLabel('本人手机号 *', { exact: true }).fill('13900000006')
  const first = page.locator('.child-card').first()
  await first.getByLabel('孩子姓名 *', { exact: true }).fill('虚构孩子甲')
  await first.locator('summary').click()
  await expect(first).not.toHaveAttribute('open')
  await page.getByRole('button', { name: '注册并进入问答' }).click()
  await expect(first).toHaveAttribute('open', '')
  await expect(first.getByLabel('孩子姓名 *', { exact: true })).toHaveValue('虚构孩子甲')
  await first.getByLabel('孩子手机号 *', { exact: true }).fill('13800000006')
  await first.getByLabel('孩子专业 *', { exact: true }).fill('会计')
  await first.getByLabel('孩子班级 *', { exact: true }).fill('一班')
  await first.getByRole('button', { name: '完成填写' }).click()
  await page.getByRole('button', { name: '添加孩子信息', exact: true }).click()
  const second = page.locator('.child-card').nth(1)
  await second.getByLabel('孩子姓名 *', { exact: true }).fill('虚构孩子乙')
  await second.getByLabel('孩子手机号 *', { exact: true }).fill('13800000007')
  await second.getByLabel('孩子专业 *', { exact: true }).fill('工商管理')
  await second.getByLabel('孩子班级 *', { exact: true }).fill('二班')
  await second.getByRole('button', { name: '完成填写' }).click()
  await expect(page.locator('.child-card[open]')).toHaveCount(0)
  await expect(first.locator('summary')).toContainText('虚构孩子甲')
  await expect(second.locator('summary')).toContainText('虚构孩子乙')
  await page.evaluate(() => window.scrollTo(0, 0))
  const box = await page.getByRole('button', { name: '注册并进入问答' }).boundingBox()
  expect(box!.y + box!.height).toBeLessThanOrEqual(844)
  await page.screenshot({ path: '../docs/evidence/parent-mobile-compact.png', fullPage: true })
  await page.getByRole('button', { name: '注册并进入问答' }).click()
  await expect(page.getByText('已填写 2 条孩子信息，均为自报资料。')).toBeVisible()
})
