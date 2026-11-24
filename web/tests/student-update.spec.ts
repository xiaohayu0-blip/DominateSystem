import { test, expect } from '@playwright/test'

test('public home and narrow dark-mode chat', async ({ page }) => {
  const question = '2027年寒假起止日期是什么？'
  const answer = { id: 'answer', status: 'complete', text: question,
    created_at: '2026-11-21T12:00:00', alert: null, answer: { items: [
      { question, status: 'validation_failed', content: '内部说明', citations: [] },
      { question, status: 'general_answer', content: '**我是小八**，头像是小八爪鱼。', citations: [] },
    ] } }
  let loggedIn = false
  await page.setViewportSize({ width: 320, height: 780 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const profile = { role: 'student', name: '测试学生', phone: '13800000000',
      major: '测试专业', class_name: '测试班', children: [] }
    if (path === '/api/modes') return route.fulfill({ json: { environment: 'production' } })
    if (path === '/api/me') return loggedIn ? route.fulfill({ json: profile }) : route.fulfill({ status: 401 })
    if (path === '/api/login') {
      loggedIn = true
      return route.fulfill({ json: profile })
    }
    if (path === '/api/question-examples') return route.fulfill({ json: [question] })
    if (path === '/api/questions') {
      expect(route.request().postDataJSON().conversation_id).toMatch(/^[a-f0-9]{32}$/)
      return route.fulfill({ status: 202, json: answer })
    }
    if (path === '/api/questions/answer/stream') return route.fulfill({
      contentType: 'text/event-stream', body: `event: complete\ndata: ${JSON.stringify(answer)}\n\n`,
    })
    throw new Error('Unexpected request ' + path)
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '管理学院AI助理学工' })).toBeVisible()
  await expect(page.getByRole('main').getByRole('img', { name: '粉蓝小八爪鱼' })).toBeVisible()
  await expect(page.getByRole('link', { name: '管理工作台' })).toHaveCount(0)
  await expect(page.locator('a[href="/admin"], a[href="#/admin"]')).toHaveCount(0)
  await expect(page.getByText(/工作台|管理后台/)).toHaveCount(0)
  await page.getByText('隐私协议', { exact: true }).click()
  await expect(page.getByText(/触发危机预警时/)).toBeVisible()
  await page.locator('.account-actions').getByRole('button', { name: '登录账号', exact: true }).click()
  await page.locator('input[name="username"]').fill('13800000000')
  await page.locator('input[name="password"]').fill('student1234')
  await page.locator('.entry-card').getByRole('button', { name: '登录账号', exact: true }).click()
  await expect(page.getByText('管小智 · 校园问答')).toBeVisible()
  await expect(page.getByPlaceholder('想了解什么，问问管小智…')).toBeVisible()
  const emoticon = page.locator('.welcome-emoticon')
  await expect(emoticon).toHaveCSS('white-space', 'nowrap')
  await expect(emoticon).toHaveCSS('display', 'block')
  await expect(page.locator('html')).toHaveCSS('color-scheme', /only light|light only/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(320)
  await page.getByRole('button', { name: question }).click()
  await expect(page.getByRole('textbox', { name: '你的问题' })).toHaveValue(question)
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('管小智来答')).toBeVisible()
  await expect(page.getByText('我是管小智，头像是小八爪鱼。')).toBeVisible()
  await expect(page.getByText(/暂时无法确认准确答案/)).toBeVisible()
  await expect(page.getByText('证据校验失败', { exact: true })).toHaveCount(0)
})

test('admin reviews context and changes publication from the workbench', async ({ page }) => {
  const profile = { role: 'student', name: '测试学生', phone: '13800000000',
    major: '测试专业', class_name: '测试班', children: [] }
  const created_at = '2026-11-21T12:00:00'
  const event = { id: 'review-event', question_id: 'trigger', text: '触发预警的测试发言',
    review: 'pending', state: 'open', snapshot: profile, created_at, history: [],
    mail: { mode: 'mock', status: 'simulated_sent', body: '管理学院危机预警\n请核实登记信息和对话。',
      attempts: 1, attempt_history: [], error_code: '' } }
  const doc = { id: 'test-policy', title: '测试制度资料', version: 'v1', status: 'published', parse_status: 'ready' }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/modes') return route.fulfill({ json: { environment: 'production' } })
    if (path === '/api/admin/login') return route.fulfill({ json: { ok: true } })
    if (path === '/api/admin/overview') return route.fulfill({ json: { weekly: { new: 0 } } })
    if (path === '/api/admin/events') return route.fulfill({ json: [event] })
    if (path === '/api/admin/events/review-event/conversation') return route.fulfill({ json: {
      complete: true, turns: [{ id: 'earlier', text: '前一轮学生发言', replies: ['前一轮小八答复'], created_at },
        { id: 'trigger', text: event.text, replies: [], created_at }],
    } })
    if (path === '/api/admin/events/review-event') {
      expect(request.postDataJSON()).toEqual({ review: 'false_alarm', state: 'open', note: '已核对完整对话' })
      event.review = 'false_alarm'
      return route.fulfill({ json: { ok: true } })
    }
    if (path === '/api/admin/documents') return route.fulfill({ json: [doc] })
    if (path === '/api/admin/documents/test-policy/disable' || path === '/api/admin/documents/test-policy/publish') {
      doc.status = path.endsWith('/disable') ? 'disabled' : 'published'
      return route.fulfill({ json: doc })
    }
    throw new Error('Unexpected request ' + path)
  })
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/admin')
  await page.getByLabel('管理员账号').fill('test-admin')
  await page.getByLabel('密码', { exact: true }).fill('password123')
  await page.getByRole('button', { name: '登录工作台' }).click()
  await page.getByRole('button', { name: '紧急事件', exact: true }).click()
  await page.getByText(event.text, { exact: true }).click()
  await expect(page.getByText('前一轮学生发言')).toBeVisible()
  await expect(page.getByText('前一轮管小智答复', { exact: false })).toBeVisible()
  await page.getByText('邮件正文预览').click()
  await expect(page.locator('.event-card pre')).toContainText('管理学院危机预警')
  await page.getByLabel('人工核实结论').selectOption('false_alarm')
  await page.getByLabel('核实或处置备注').fill('已核对完整对话')
  await page.getByRole('button', { name: '保存核实和处置' }).click()
  await expect(page.locator('.event-card summary').getByText('误报')).toBeVisible()

  await page.getByRole('button', { name: '资料维护', exact: true }).click()
  await page.getByRole('button', { name: '停用', exact: true }).click()
  await expect(page.getByText('已停用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新启用' }).click()
  await expect(page.getByText('已发布', { exact: true })).toBeVisible()
})
