import { test, expect } from '@playwright/test'

for (const scenario of [
  { name: 'desktop', width: 1280, reducedMotion: 'no-preference' },
  { name: 'mobile', width: 390, reducedMotion: 'no-preference' },
  { name: 'reduced-motion', width: 320, reducedMotion: 'reduce' },
] as const) {
test('密码与弹出式进度提示 - ' + scenario.name, async ({ page }) => {
  await page.setViewportSize({ width: scenario.width, height: 900 })
  await page.emulateMedia({ reducedMotion: scenario.reducedMotion })
  const profile = { id: 'student-progress', role: 'student', name: '进度测试学生', phone: '13800000003',
    major: '工商管理', class_name: '测试一班', children: [] }
  const question = { id: 'question-progress', text: '奖学金申请材料', status: 'pending',
    answer: { items: [] }, created_at: '2026-11-16T01:00:00', alert: null, progress: 'received' }
  let registrations = 0
  const answer = { items: [{ question: question.text, status: 'answered', content: '能加，先把条件看清楚 (｡•̀ᴗ-)✧\n需提交申请表和成绩单。', citations: [] }] }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/me') return route.fulfill({ status: 401, json: { detail: '请登录' } })
    if (path === '/api/modes') return route.fulfill({ json: { environment: 'test', model: 'mock', material: 'synthetic', mail: 'mock' } })
    if (path === '/api/register') {
      registrations++
      return route.fulfill({ json: profile })
    }
    if (path === '/api/questions') {
      await new Promise(resolve => setTimeout(resolve, 500))
      return route.fulfill({ status: 202, json: question })
    }
    if (path === '/api/questions/question-progress/stream') {
      const event = (kind: string, data: unknown) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`
      const frames = ['received', 'searching', 'checking', 'composing'].map(stage => event('progress', { stage }))
      frames.push(event('item', { index: 0, item: { ...answer.items[0], content: '', citations: [] } }))
      for (const text of answer.items[0].content) frames.push(event('delta', { index: 0, text }))
      frames.push(event('complete', { ...question, status: 'complete', progress: null, answer }))
      return route.fulfill({ contentType: 'text/event-stream', body: frames.join('') })
    }
    throw new Error('Unexpected API request: ' + path)
  })
  // Model a chunked network response so the real parser and gradual rendering are exercised.
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window)
    window.fetch = async (...args) => {
      const response = await originalFetch(...args)
      if (!String(args[0]).endsWith('/stream')) return response
      const frames = (await response.text()).split('\n\n').filter(Boolean)
      return new Response(new ReadableStream({ async start(controller) {
        for (const frame of frames) {
          controller.enqueue(new TextEncoder().encode(frame + '\n\n'))
          await new Promise(resolve => setTimeout(resolve, frame.includes('event: progress') ? 900 : 8))
        }
        controller.close()
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
  })
  await page.goto('/')
  await expect(page.locator('.tactile-art .octopus-mascot')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `../docs/evidence/tactile-landing-${scenario.name}-20261117.png`, fullPage: true })
  await page.getByRole('button', { name: '注册账号', exact: true }).click()
  await page.getByLabel('本人姓名 *', { exact: true }).fill(profile.name)
  await page.getByLabel('本人手机号 *', { exact: true }).fill(profile.phone)
  await page.getByLabel('专业 *', { exact: true }).fill(profile.major)
  await page.getByLabel('班级 *', { exact: true }).fill(profile.class_name)
  for (const password of ['12345678', 'abcdefgh', 'abc1234']) {
    await page.getByLabel('密码 *', { exact: true }).fill(password)
    await page.getByRole('button', { name: '注册并进入问答' }).click()
    await expect(page.locator('.field-error')).toContainText('需同时包含英文字母和数字')
    expect(registrations).toBe(0)
  }
  await page.getByLabel('密码 *', { exact: true }).fill('abcdefg1')
  await page.getByRole('button', { name: '注册并进入问答' }).click()
  await expect(page.getByRole('textbox', { name: '你的问题' })).toBeVisible()
  expect(registrations).toBe(1)
  await page.screenshot({ path: `../docs/evidence/tactile-chat-${scenario.name}-20261117.png`, fullPage: true })
  await page.getByRole('textbox', { name: '你的问题' }).fill(question.text)
  const lengths: number[] = []
  await page.exposeFunction('recordAnswerLength', (length: number) => lengths.push(length))
  await page.evaluate(() => {
    new MutationObserver(() => {
      const text = document.querySelector('.answer-item .pre-wrap')?.textContent
      if (text) (window as unknown as { recordAnswerLength: (n: number) => void }).recordAnswerLength(text.length)
    }).observe(document.querySelector('.messages')!, { childList: true, subtree: true, characterData: true })
  })
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  for (const [stage, text] of Object.entries({ received: '收到问题啦', searching: '正在查找学院资料',
    checking: '正在核对适用年级', composing: '正在整理回答' })) {
    const notice = page.getByRole('status')
    await expect.poll(() => notice.textContent(), { intervals: [25, 50, 75] }).toBe(text)
    await expect(notice).toHaveAttribute('data-stage', stage)
    await expect(notice).toHaveCSS('border-top-width', '1px')
    await expect(notice.locator('.progress-notice-label')).toHaveCSS('animation-name',
      scenario.reducedMotion === 'reduce' ? 'none' : 'progress-message-pop')
    await expect(notice.locator('.progress-notice-dots i')).toHaveCount(3)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
  await page.screenshot({ path: `../docs/evidence/octopus-progress-${scenario.name}-20261117.png`, fullPage: true })
  await expect(page.locator('.answer-item .pre-wrap')).toHaveText('能加，先把条件看清楚 (｡•̀ᴗ-)✧\n需提交申请表和成绩单。')
  expect(lengths.some(length => length > 0 && length < answer.items[0].content.length)).toBe(true)
  await expect(page.getByRole('status')).toHaveCount(0)
  await expect(page.getByText('正在处理，请稍候…', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: `../docs/evidence/octopus-answer-${scenario.name}-20261117.png`, fullPage: true })
})
}
