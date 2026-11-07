export class ApiError extends Error {
  status: number
  fields: Record<string, string>
  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message); this.status = status; this.fields = fields
  }
}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch('/api' + path, {
      method, credentials: 'same-origin',
      headers: body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    })
  } catch { throw new ApiError('网络连接失败，输入已保留，请重试。', 0) }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const fields = Object.fromEntries((data.errors ?? []).map((x: { field: string; message: string }) => [x.field, x.message]))
    const fallback = response.status >= 500
      ? '服务暂时无法连接，请稍后重试。'
      : '操作未完成，请检查输入后重试。'
    throw new ApiError(typeof data.detail === 'string' ? data.detail : fallback, response.status, fields)
  }
  return data as T
}
export type Child = { name: string; phone: string; major: string; class_name: string; relationship: string }
export type Profile = { id?: string; role: 'student' | 'parent'; name: string; phone: string; major: string; class_name: string; children: Child[] }
export type Citation = { chunk_id: string; document_id: string; title: string; publisher: string; version: string; location: string; excerpt: string; business_year: number | null; admission_year: number | null; effective_from: string | null; effective_to: string | null; source_status?: string; material_mode: string }
export type Answer = { items: { question: string; status: string; content: string; citations: Citation[] }[]; context?: { business_year: number; admission_year?: number }; notice?: string }
export type Question = { id: string; text: string; status: string; answer: Answer; created_at: string; snapshot?: Profile; alert: null | { message: string; mail_mode: string; status: string; notice: string } }
type WeeklyMail = { status: string; week_start: string; sent_at: string | null; attempts: number }
export type Occurrence = { issue_id: string; question_id: string; question: string; subquestion: string; snapshot: Profile; context: { business_year: number; admission_year: number | null }; answer: Answer; created_at: string; weekly_mail: WeeklyMail | null }
export type History = { at: string; by?: string; note?: string; state?: string; action?: string }
export type Issue = { id: string; text: string; reason: string; state: string; owner: string; revision: number; count: number; evidence_ids: string[]; occurrences: Occurrence[]; history: History[]; retests: { at: string; passed: boolean; evidence_ids: string[]; results: { answer: Answer }[] }[] }
export type Doc = { id: string; title: string; publisher: string; version: string; business_year: number | null; admission_year: number | null; effective_from: string | null; effective_to: string | null; status: string; parse_status: string; parse_note: string; confirmed_by: string; confirmed_on: string | null; basis: string; chunks: { id: string; location: string; text: string }[] }
export type Weekly = { mail_enabled: boolean; weekly_mail: WeeklyMail | null; start: string; end: string; new: Issue[]; backlog: Issue[]; counts: { new: number; backlog: number; by_reason: Record<string, number> } }
export type Emergency = { id: string; text: string; state: string; snapshot: Profile; history: History[]; created_at: string; mail: { id: string; mode: string; status: string; body: string; attempts: number; error_code: string; attempt_history: { at: string; result: string }[] } }
export type Modes = { environment: string; model: string; material: string; mail: string; embedding?: string }
export const label: Record<string, string> = {
  answered: '有据可查', missing_material: '待补充资料', needs_clarification: '待澄清', document_conflict: '资料冲突',
  validation_failed: '证据校验失败', system_error: '技术故障', out_of_scope: '服务范围外',
  open: '待处理', in_progress: '处理中', awaiting_material: '待补充资料', awaiting_retest: '待复测', completed: '已完成', dismissed: '无需处理',
  following: '跟进中', closed: '已结束', pending: '待发送', sending: '发送中', retry: '等待重试', failed: '失败',
  simulated_sent: '模拟已发送', submitted: '已提交邮件服务器', uncertain: '投递不确定·需人工核对', unconfigured: '未配置',
  draft: '草稿', published: '已发布', disabled: '已停用', ready: '解析就绪',
}
export const stamp = (value: string) => new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
