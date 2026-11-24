import { expect, test } from '@playwright/test'

const description = '可查询请假与学籍办理、校历、综合测评、奖学金及学科竞赛等校院制度。'

test('首页在手机和桌面上显示制度范围、章鱼及账号入口', async ({ page }) => {
  for (const { width, height } of [{ width: 320, height: 568 }, { width: 390, height: 780 }, { width: 1366, height: 900 }]) {
    await page.setViewportSize({ width, height })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '管理学院AI助理学工' })).toBeVisible()
    await expect(page.getByText(description)).toBeVisible()
    await expect(page.getByRole('main').getByRole('img', { name: '管小智的粉蓝章鱼头像' })).toBeVisible()
    const register = page.getByRole('button', { name: '注册账号', exact: true })
    const login = page.getByRole('button', { name: '登录账号', exact: true })
    await expect(register).toBeInViewport()
    await expect(login).toBeInViewport()
    const registerBox = (await register.boundingBox())!
    const loginBox = (await login.boundingBox())!
    expect(registerBox.y + registerBox.height).toBeLessThan(loginBox.y)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
})

test('首页协议可展开和收起，注册和登录仍进入原有表单', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/')
  const privacy = page.getByText('隐私协议', { exact: true })
  const policy = page.getByText('本平台使用账号和登记资料', { exact: false })
  await privacy.click()
  await expect(policy).toBeVisible()
  await privacy.click()
  await expect(policy).toBeHidden()
  await page.getByRole('button', { name: '注册账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '注册信息' })).toBeVisible()
  await expect(page.getByRole('button', { name: '注册并进入问答' })).toBeVisible()
  await page.getByRole('button', { name: '登录账号', exact: true }).click()
  await expect(page.getByRole('heading', { name: '账号登录' })).toBeVisible()
  await expect(page.getByLabel('密码', { exact: true })).toBeVisible()
})
