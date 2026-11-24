import { test, expect } from '@playwright/test'

test('手机问答显示纯文本与等高联网来源', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 690 })
  const content = '**测试项目**\n- 800米，连续3天\n计算：2*3*4\n[原通知](https://school.example/notice)'
  const web_sources = Array.from({ length: 4 }, (_, index) => ({
    id: String(index), title: `来源${index + 1}` + '体质健康测试安排及注意事项'.repeat(index + 1),
    url: `https://school.example/source/${index + 1}`, excerpt: '这段长摘要不应占据页面。'.repeat(50),
    published_at: '2022-04-13', retrieved_at: '2026-11-21',
  }))
  const question = { id: 'web-citations', text: '体测要求是啥', status: 'complete', alert: null,
    created_at: '2026-11-21T07:11:00', answer: { items: [{
      question: '体测要求是啥', status: 'web_answer', content, citations: [], web_sources,
    }] } }
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/me') return route.fulfill({ json: {
      id: 'web-source-student', role: 'student', name: '测试学生', phone: '13800000003',
      major: '工商管理', class_name: '测试一班', children: [],
    } })
    if (path === '/api/modes') return route.fulfill({ json: { environment: 'test', model: 'mock' } })
    if (path === '/api/question-examples') return route.fulfill({ json: [] })
    if (path === '/api/questions' || path === '/api/questions/web-citations') return route.fulfill({ json: question })
    if (path === '/api/questions/web-citations/stream') return route.fulfill({
      contentType: 'text/event-stream', body: `event: complete\ndata: ${JSON.stringify(question)}\n\n`,
    })
    throw new Error('Unexpected API request: ' + path)
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '你的问题' }).fill(question.text)
  await page.getByRole('button', { name: '发送问题', exact: true }).click()

  const answer = page.locator('.answer-item > .pre-wrap')
  await expect(answer).toHaveText('测试项目\n• 800米，连续3天\n计算：2*3*4\n原通知（https://school.example/notice）')
  const sources = page.getByRole('region', { name: '联网来源' })
  const visibleCards = sources.getByRole('link')
  await expect(visibleCards).toHaveCount(2)
  await expect(visibleCards.first()).toHaveAttribute('href', web_sources[0].url)
  await expect(sources).not.toContainText('这段长摘要')
  for (const card of await visibleCards.all()) expect((await card.boundingBox())?.height).toBe(60)
  await sources.getByText('查看更多来源（2）').click()
  await expect(visibleCards).toHaveCount(4)
  await expect(visibleCards.last()).toHaveAttribute('href', web_sources[3].url)
  expect((await visibleCards.last().boundingBox())?.height).toBe(60)
})

test('手机制度出处可滑动并始终能关闭', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 590 })
  const profile = { id: 'source-student', role: 'student', name: '制度测试学生', phone: '13800000003',
    major: '工商管理', class_name: '测试一班', children: [] }
  const citation = { chunk_id: 'scholarship-rule', document_id: 'scholarship-doc', title: '本科生奖学金评定管理办法',
    version: 'v1', location: '第1页', excerpt: '条款正文。\n'.repeat(100) + '制度末尾',
    source_status: 'published', url: '/api/sources/scholarship-rule' }
  const question = { id: 'source-question', text: '奖学金多少钱？', status: 'complete', alert: null,
    created_at: '2026-11-21T05:25:00', answer: { items: [{
      question: '奖学金多少钱？', status: 'answered', content: '学业奖学金分等级发放。', citations: [citation],
    }] } }
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/me') return route.fulfill({ json: profile })
    if (path === '/api/question-examples') return route.fulfill({ json: [] })
    if (path === '/api/modes') return route.fulfill({ json: { environment: 'test', model: 'mock' } })
    if (path === '/api/questions' || path === '/api/questions/source-question') return route.fulfill({ json: question })
    if (path === '/api/questions/source-question/stream') {
      return route.fulfill({ contentType: 'text/event-stream',
        body: `event: complete\ndata: ${JSON.stringify(question)}\n\n` })
    }
    if (path === citation.url) return route.fulfill({ json: citation })
    throw new Error('Unexpected API request: ' + path)
  })
  await page.goto('/')
  await page.getByRole('textbox', { name: '你的问题' }).fill(question.text)
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  const source = page.getByRole('button', { name: /本科生奖学金评定管理办法/ })
  await source.click()
  const dialog = page.getByRole('dialog', { name: '引用来源' })
  const body = dialog.locator('.source-dialog-scroll')
  await body.hover()
  await page.mouse.wheel(0, 800)
  await expect.poll(() => body.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  const close = dialog.getByRole('button', { name: '关闭', exact: true })
  await expect(close).toBeInViewport()
  await close.click()
  await expect(dialog).toHaveCount(0)
  await source.click()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

for (const scenario of [
  { name: 'desktop', width: 1280, reducedMotion: 'no-preference' },
  { name: 'mobile', width: 390, reducedMotion: 'no-preference' },
  { name: 'legacy-mobile', width: 390, reducedMotion: 'no-preference' },
  { name: 'reduced-motion', width: 320, reducedMotion: 'reduce' },
] as const) {
test('密码与弹出式进度提示 - ' + scenario.name, async ({ page }) => {
  await page.setViewportSize({ width: scenario.width, height: 900 })
  await page.emulateMedia({ reducedMotion: scenario.reducedMotion })
  if (scenario.name === 'legacy-mobile') await page.addInitScript(() => {
    Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', { value: undefined, configurable: true })
  })
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
    if (path === '/api/question-examples') return route.fulfill({ json: [] })
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
  await expect(page.getByRole('heading', { name: '管小智', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
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
  await expect(page.locator('.answer-item .pre-wrap')).toHaveText('能加，先把条件看清楚 (｡•̀ᴗ-)✧\n需提交申请表和成绩单。')
  expect(lengths.some(length => length > 0 && length < answer.items[0].content.length)).toBe(true)
  await expect(page.getByRole('status')).toHaveCount(0)
  await expect(page.getByText('正在处理，请稍候…', { exact: true })).toHaveCount(0)
})
}


test('缺少新取消接口时仍可取消流式读取', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ status: 401, json: { detail: '请登录' } }))
  await page.goto('/')
  const result = await page.evaluate(async () => {
    Object.defineProperty(AbortSignal.prototype, 'throwIfAborted', { value: undefined, configurable: true })
    const apiModule = '/src/api.ts'
    const { streamAnswer } = await import(apiModule)
    const originalFetch = window.fetch
    window.fetch = async () => new Response('event: delta\ndata: {"index":0,"text":"测"}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' },
    })
    const controller = new AbortController()
    let received = 0
    try {
      await streamAnswer('cancellation-test', () => { received++; controller.abort() }, controller.signal)
      return { error: '', received }
    } catch (error) {
      return { error: (error as Error).name, received }
    } finally {
      window.fetch = originalFetch
    }
  })
  expect(result).toEqual({ error: 'AbortError', received: 1 })
})
