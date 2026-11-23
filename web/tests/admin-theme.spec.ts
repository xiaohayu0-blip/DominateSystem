import { test, expect } from '@playwright/test'

for (const width of [1440, 390, 320]) {
  test(`admin navigation and forms at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const person = { id: 'fixture-person', role: 'student', name: '体验学生', phone: '13800000000', major: '工商管理', class_name: '体验班', children: [] }
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url())
      const path = url.pathname.replace('/api', '')
      if (path === '/admin/weekly/export') {
        expect(url.searchParams.get('start')).toBe('2026-11-15')
        expect(url.searchParams.get('end')).toBe('2026-11-22')
        return route.fulfill({
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          body: 'mock workbook',
        })
      }
      const data: Record<string, unknown> = {
        '/modes': { environment: 'production', max_upload_mb: 20 },
        '/admin/login': { ok: true },
        '/admin/overview': { weekly: { new: 2 }, total_issue_groups: 6, pending_issue_groups: 2, open_events: 0, mail_attention: 0, pending_questions: 1, documents: 3 },
        '/admin/weekly': { start: '2026-11-16T00:00:00+08:00', end: '2026-11-23T00:00:00+08:00', new: [], backlog: [], counts: { new: 0, backlog: 0, by_reason: {} }, mail_enabled: false },
        '/admin/issues': [], '/admin/events': [], '/admin/people': [person],
        '/admin/documents': [{ id: 'fixture-document', title: '管理学院综合测评制度（体验资料）', version: 'v1', business_year: 2026, status: 'published', parse_status: 'ready' }],
        '/admin/questions': [{ id: 'fixture-question', text: '综合测评需要准备什么材料？', created_at: '2026-11-17T12:00:00+08:00', snapshot: person, answer: { items: [{ question: '综合测评需要准备什么材料？', status: 'answered', content: '请按照适用年级的制度准备材料。', citations: [] }] } }],
      }
      if (!(path in data)) throw new Error(`Unexpected API request: ${path}`)
      await route.fulfill({ json: data[path] })
    })
    const noOverflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.goto('/#/admin')
    await expect(page.getByRole('img', { name: '粉蓝小八爪鱼' })).toBeVisible()
    const mascot = await page.getByRole('img', { name: '粉蓝小八爪鱼' }).boundingBox()
    expect(mascot).not.toBeNull()
    expect(mascot!.width).toBeGreaterThanOrEqual(100)
    expect(Math.abs(mascot!.width - mascot!.height)).toBeLessThan(1)
    await noOverflow()
    await page.screenshot({ path: `test-results/admin-login-${width}.png`, fullPage: true })
    await page.getByLabel('管理员账号').fill('fixture-admin')
    await page.getByLabel('密码', { exact: true }).fill('fixture-password1')
    await page.getByRole('button', { name: '登录工作台' }).click()
    await expect(page.getByRole('heading', { name: '工作事项' })).toBeVisible()
    await expect(page.getByText(/发送上周新增问题/)).toHaveCount(0)
    await noOverflow()
    await page.screenshot({ path: `test-results/admin-overview-${width}.png`, fullPage: true })
    for (const title of ['后台周报', '紧急事件', '资料维护', '登记资料', '问答记录', '工作概览']) {
      if (width < 761) await page.getByRole('button', { name: '展开管理菜单' }).click()
      await page.locator('.admin-sidebar').getByRole('button', { name: title, exact: true }).click()
      await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
      await expect(page.locator('.loading')).toHaveCount(0)
      if (title === '后台周报') {
        await expect(page.getByText(/发送上周新增问题|该周邮件/)).toHaveCount(0)
        await page.getByRole('button', { name: '导出 Excel' }).click()
        await expect(page.getByLabel('开始日期')).toHaveValue('2026-11-16')
        await expect(page.getByLabel('结束日期（含当天）')).toHaveValue('2026-11-22')
        await noOverflow()
        await page.getByLabel('开始日期').fill('2026-11-15')
        const download = page.waitForEvent('download')
        await page.getByRole('button', { name: '下载 Excel' }).click()
        expect((await download).suggestedFilename()).toBe('问题组_2026-11-15_2026-11-22.xlsx')
      }
      if (title === '资料维护') {
        await expect(page.getByLabel('选择制度文件')).toBeVisible()
        await expect(page.getByText('管理学院综合测评制度（体验资料）')).toBeVisible()
      }
      if (title === '问答记录') {
        await page.locator('.admin-content > details > summary').click()
        await expect(page.getByText('请按照适用年级的制度准备材料。')).toBeVisible()
      }
      await noOverflow()
      await page.screenshot({ path: `test-results/admin-${title}-${width}.png`, fullPage: true })
    }
  })
}
