import { useEffect, useState } from 'react'
import { api, stamp } from './api'
import './vocabulary.css'

type Content = { source: string; target: string; kind: string; submitter?: string; reviewer?: string; version?: number; at?: string }
type Entry = { id: string; revision: number; state: string; draft: Content; published: Content | null; versions: Content[] }
type Change = { action: string; actor: string; at: string; note: string; before: Entry | null; after: Entry }
type Preview = { original: string; queries: string[]; applied: Content[]; hits: { document_id: string; title: string; location: string; excerpt: string; business_year?: number; admission_year?: number }[]; notice: string; draft_preview: boolean; status: string }
const root = '/admin/vocabulary'
const empty = (): Content => ({ source: '', target: '', kind: '简称' })
const states: Record<string, string> = { draft: '草稿', submitted: '待审核', returned: '退回修改', published: '已发布', disabled: '已停用' }
const actions: Record<string, string> = { create: '新建草稿', edit: '编辑草稿', submit: '提交审核', return: '退回修改', publish: '发布', disable: '停用', rollback: '回滚发布' }
const message = (e: unknown) => e instanceof Error ? e.message : '操作失败，请重试'

export function VocabularyPage({ review = true }: { review?: boolean }) {
  const [items, setItems] = useState<Entry[]>([])
  const [keyword, setKeyword] = useState('')
  const [kind, setKind] = useState('')
  const [state, setState] = useState('')
  const [selected, setSelected] = useState<Entry | null>(null)
  const [form, setForm] = useState<Content>(empty)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [question, setQuestion] = useState('')
  const [year, setYear] = useState(String(new Date().getFullYear()))
  const [grade, setGrade] = useState('')
  const [includeDraft, setIncludeDraft] = useState(true)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [history, setHistory] = useState<Change[] | null>(null)
  const [note, setNote] = useState('')
  const [legacy, setLegacy] = useState<{ literal: Record<string, string>; patterns: Record<string, string>; notice: string } | null>(null)
  const dirty = selected ? ['source', 'target', 'kind'].some(k => form[k as keyof Content] !== selected.draft[k as keyof Content]) : !!(form.source || form.target)

  async function reload() {
    const rows = await api<Entry[]>(root)
    setItems(rows); setLoaded(true)
  }
  useEffect(() => {
    let active = true
    api<Entry[]>(root).then(rows => { if (active) { setItems(rows); setLoaded(true) } }).catch(e => { if (active) setError(message(e)) })
    return () => { active = false }
  }, [])
  function choose(item: Entry | null) {
    if (dirty && !confirm('有尚未保存的输入，确认放弃这些输入？')) return
    setSelected(item); setForm(item ? { ...item.draft } : empty()); setHistory(null); setPreview(null); setNote(''); setError(''); setNotice('')
  }
  async function run(task: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await task() } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  async function save() {
    await run(async () => {
      const item = await api<Entry>(root + (selected ? '/' + selected.id : ''), selected ? 'PUT' : 'POST', {
        source: form.source, target: form.target, kind: form.kind, ...(selected ? { revision: selected.revision } : {}),
      })
      setSelected(item); setForm({ ...item.draft }); setHistory(null); setPreview(null)
      await reload(); setNotice('草稿已保存，尚未改变学生检索。')
    })
  }
  async function action(action: string, version?: number) {
    if (!selected) return
    if (['publish', 'disable', 'rollback'].includes(action) && !confirm(`${actions[action]}后会立即影响学生检索，确认继续？`)) return
    await run(async () => {
      const item = await api<Entry>(`${root}/${selected.id}/actions`, 'POST', { action, revision: selected.revision, note, version })
      setSelected(item); setForm({ ...item.draft }); setPreview(null); setHistory(null); setNote('')
      await reload(); setNotice(`${actions[action]}成功。${action === 'submit' || action === 'return' ? '现有生效版本继续使用。' : ''}`)
    })
  }
  const visible = items.filter(e => (!kind || e.draft.kind === kind) && (!state || e.state === state) && (e.draft.source + e.draft.target).includes(keyword))
  return <div className="vocabulary-page">
    <section className="panel vocabulary-intro"><div><span className="eyebrow">让日常说法找到正式资料</span><h2>把常用词，交给小八认识</h2><p>词条只帮助寻找资料，制度答案仍以原文为准。</p></div><ol className="vocabulary-steps"><li>保存草稿</li><li>试搜核对</li><li>提交审核</li><li>管理员发布</li></ol></section>
    <p className="boundary">草稿不参与学生检索。编辑已发布词条时，旧版本继续生效，直到新版本审核发布。短于两字、数字与条件表达、正则表达式不支持；不同解释请先核对，不自动覆盖。</p>
    {error && <div className="error" role="alert">{error}<button className="text-button" disabled={busy} onClick={() => void run(async () => { await reload(); setNotice('列表已刷新。请核对新版本；编辑区的输入仍保留，可复制后重新选择词条。') })}>刷新列表并保留输入</button></div>}
    {notice && <p className="vocabulary-notice" role="status">{notice}</p>}
    <div className="vocabulary-columns"><section className="panel vocabulary-list"><div className="vocabulary-heading"><h3>词条列表 <small>{visible.length} 条</small></h3><button className="secondary" disabled={busy} onClick={() => choose(null)}>新增词条</button></div>
      <div className="vocabulary-filters"><label>搜索词条<input placeholder="日常说法或正式名称" value={keyword} onChange={e => setKeyword(e.target.value)}/></label><label>类型筛选<select value={kind} onChange={e => setKind(e.target.value)}><option value="">全部类型</option>{['简称', '同义表达', '错别字'].map(k => <option key={k}>{k}</option>)}</select></label><label>状态筛选<select value={state} onChange={e => setState(e.target.value)}><option value="">全部状态</option>{Object.entries(states).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label></div>
      {!loaded ? <p>正在读取词条…</p> : !visible.length ? <p className="muted">暂无符合条件的词条。可新增草稿并试搜。</p> : visible.map(item => <article className={'vocabulary-item ' + (selected?.id === item.id ? 'is-selected' : '')} key={item.id}><div className="tag-row"><span className="tag">{item.draft.kind}</span><span className="tag">{states[item.state]}</span>{item.published && item.state !== 'published' && <span className="tag">旧版本仍生效</span>}</div><h4>{item.draft.source} <span aria-label="对应">→</span> {item.draft.target}</h4><p>提交人：{item.draft.submitter}<br/>审核人：{item.published?.reviewer ?? '尚未发布'}</p>{item.published && <small>当前生效：{item.published.source} → {item.published.target}</small>}<button className="text-button" disabled={busy} onClick={() => choose(item)}>编辑与试搜</button></article>)}
    </section><div className="vocabulary-detail"><section className="panel"><span className="eyebrow">{selected ? states[selected.state] : '新增草稿'}</span><h3>词条编辑</h3><form onSubmit={e => { e.preventDefault(); void save() }} className="vocabulary-form"><label>日常说法<input required minLength={2} maxLength={24} value={form.source} onChange={e => setForm({ ...form, source: e.target.value })} placeholder="例如：国励奖"/></label><label>对应正式名称<input required minLength={2} maxLength={48} value={form.target} onChange={e => setForm({ ...form, target: e.target.value })} placeholder="例如：国家励志奖学金"/></label><label>词条类型<select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}>{['简称', '同义表达', '错别字'].map(k => <option key={k}>{k}</option>)}</select></label><div className="vocabulary-actions"><button className="secondary" disabled={busy}>保存草稿</button><button type="button" className="secondary" disabled={busy || !selected || dirty || !['draft', 'returned', 'disabled'].includes(selected.state)} onClick={() => void action('submit')}>提交审核</button></div><small>保存和提交均不立即改变检索；试搜使用已保存的内容。</small></form>
      {selected && <div className="vocabulary-review"><h4>审核操作</h4><p>提交人：{selected.draft.submitter} · 审核人：{selected.published?.reviewer ?? '待审核'}</p>{selected.published && <p className="vocabulary-live">当前生效：{selected.published.source} → {selected.published.target}</p>}{review ? <><label>审核意见<textarea value={note} onChange={e => setNote(e.target.value)} maxLength={500} placeholder="退回时请说明修改原因"/></label><div className="vocabulary-actions"><button className="primary" disabled={busy || dirty || selected.state !== 'submitted'} onClick={() => void action('publish')}>发布</button><button className="secondary" disabled={busy || dirty || selected.state !== 'submitted' || !note.trim()} onClick={() => void action('return')}>退回修改</button><button className="secondary" disabled={busy || !selected.published} onClick={() => void action('disable')}>停用</button></div></> : <p className="muted">由管理员审核并发布，你可以保存草稿、试搜和提交审核。</p>}
      <button className="text-button" disabled={busy} onClick={() => void run(async () => { setHistory(await api<Change[]>(`${root}/${selected.id}/history`)) })}>查看历史记录</button>
      {history && <div className="vocabulary-history">{selected.versions.length > 0 && <><h4>已发布版本</h4>{selected.versions.map(v => <div className="vocabulary-version" key={v.version}><p>第 {v.version} 版 · {v.source} → {v.target}<br/><small>{stamp(v.at ?? '')} · 审核人 {v.reviewer}</small></p>{review && <button className="secondary" disabled={busy || dirty} onClick={() => void action('rollback', v.version)}>回滚至此版</button>}</div>)}</>}{[...history].reverse().map((h, i) => <details key={i}><summary>{actions[h.action]} · {h.actor} · {stamp(h.at)}</summary><p>{h.note}</p><p>修改前：{h.before ? `${h.before.draft.source} → ${h.before.draft.target}（${states[h.before.state]}）` : '无'}</p><p>修改后：{h.after.draft.source} → {h.after.draft.target}（{states[h.after.state]}）</p></details>)}</div>}
      </div>}</section>
      <section className="panel"><span className="eyebrow">检索预览 · 不调用大模型</span><h3>先试搜，再发布</h3><form className="vocabulary-form" onSubmit={e => { e.preventDefault(); void run(async () => { setPreview(await api<Preview>(root + '/preview', 'POST', { question, business_year: Number(year), admission_year: grade ? Number(grade) : null, ...(includeDraft && selected ? { draft_id: selected.id, draft_revision: selected.revision } : {}) })) }) }}><label>测试问题<textarea required maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="输入正例、反例或带年份和数值的问题"/></label><div className="vocabulary-filters"><label>业务年度<input type="number" required min="2000" max="2100" value={year} onChange={e => setYear(e.target.value)}/></label><label>入学年级<input type="number" min="2000" max="2100" placeholder="未指定" value={grade} onChange={e => setGrade(e.target.value)}/></label></div><label className="checkbox"><input type="checkbox" checked={includeDraft} onChange={e => setIncludeDraft(e.target.checked)}/>包含当前已保存草稿（仅本次预览）</label><button className="secondary" disabled={busy || !question.trim() || (includeDraft && dirty)}>开始试搜</button>{includeDraft && dirty && <small>请先保存草稿，再试搜其效果。</small>}</form>
      {preview && <div className="vocabulary-preview"><span className="tag">{preview.draft_preview ? '草稿预览，不影响学生' : '当前已发布词表'}</span><h4>原问题</h4><p>{preview.original}</p><h4>采用词条</h4>{preview.applied.length ? preview.applied.map((r, i) => <p key={i}>{r.source} → {r.target}</p>) : <p>未命中动态词条，仍使用现有兼容检索。</p>}<h4>检索输入</h4>{preview.queries.map((q, i) => <p key={i}>{q}</p>)}{preview.status === 'needs_clarification' && <p>存在多个合理解释，请补充具体事项。</p>}<h4>命中的已发布资料</h4>{preview.hits.length ? preview.hits.map((hit, i) => <article key={i}><b>{hit.title}</b><small>{hit.location} · 年度 {hit.business_year ?? '不限'} / 年级 {hit.admission_year ?? '不限'}</small><blockquote>{hit.excerpt}</blockquote></article>) : <p>未找到可在试搜中展示的资料。请核对反例是否误命中。</p>}<p className="muted">{preview.notice}</p></div>}
      </section></div></div>
    <details className="panel" onToggle={e => { if (e.currentTarget.open && !legacy) void api<typeof legacy>(root + '/legacy').then(setLegacy).catch(e => setError(message(e))) }}><summary>现有固定映射与兼容说明</summary>{legacy && <><p>{legacy.notice}</p>{Object.entries(legacy.literal).map(([s, t]) => <p key={s}>{s} → {t}</p>)}<details><summary>系统兼容模式（只读）</summary>{Object.entries(legacy.patterns).map(([s, t]) => <p key={s}>{s} → {t}</p>)}</details></>}</details>

  </div>
}
