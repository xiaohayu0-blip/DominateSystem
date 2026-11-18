import { test, expect } from '@playwright/test'

for (const width of [1440, 390, 320]) {
  test(`vocabulary draft preview and mobile layout ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const draft = { source: '国励奖', target: '国家励志奖学金', kind: '简称', submitter: '维护同学' }
    const entry = { id: 'term-1', revision: 1, state: 'draft', draft, published: null, versions: [] }
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname.replace('/api', '')
      const data: Record<string, unknown> = {
        '/modes': { environment: 'production' }, '/admin/login': { username: 'admin' },
        '/admin/vocabulary/access': { name: 'admin', review: true },
        '/admin/overview': { weekly: { new: 0 }, total_issue_groups: 0, pending_issue_groups: 0 },
        '/admin/vocabulary': route.request().method() === 'POST' ? entry : [entry],
        '/admin/vocabulary/legacy': { literal: { 综测: '综合测评' }, patterns: {}, notice: '现有兼容规则只读保留' },
        '/admin/vocabulary/preview': { original: '国励奖申请条件', queries: ['国励奖申请条件', '国家励志奖学金申请条件'], applied: [draft], hits: [{ document_id: 'doc1', title: '国家励志奖学金办法', location: '第1条', excerpt: '申请人须提交认定表。' }], notice: '仅检索，不调用大模型', draft_preview: true },
        '/admin/vocabulary/term-1/history': [],
      }
      if (!(path in data)) throw new Error(`Unexpected API: ${path}`)
      await route.fulfill({ json: data[path] })
    })
    await page.goto('/#/admin')
    await page.getByLabel('管理员账号').fill('admin')
    await page.getByLabel('密码', { exact: true }).fill('password1')
    await page.getByRole('button', { name: '登录工作台' }).click()
    if (width < 761) await page.getByRole('button', { name: '展开管理菜单' }).click()
    await page.locator('.admin-sidebar').getByRole('button', { name: '词语维护' }).click()
    await expect(page.getByRole('heading', { name: '词语维护', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '编辑与试搜' }).click()
    await expect(page.getByLabel('日常说法', { exact: true })).toHaveValue('国励奖')
    await page.getByLabel('测试问题').fill('国励奖申请条件')
    await page.getByRole('button', { name: '开始试搜' }).click()
    await expect(page.getByText('申请人须提交认定表。')).toBeVisible()
    await expect(page.getByText('国家励志奖学金申请条件', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/vocabulary-${width}.png`, fullPage: true })
  })
}
