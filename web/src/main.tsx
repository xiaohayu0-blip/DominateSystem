import { StrictMode, cloneElement, isValidElement, useId, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { ArrowUpRight, ArrowUp, BookOpen, FileText, ChevronRight, ShieldCheck, LogOut, Plus, X, LayoutDashboard, Inbox, CalendarDays, Siren, Files, Users, Search, Menu, RefreshCw, CheckCircle2 } from 'lucide-react'
import { api, ApiError, streamAnswer, label, stamp, type Profile, type Child, type Citation, type Question, type Issue, type Doc, type Weekly, type Emergency, type Modes, type Answer, type History } from './api'
import './style.css'
import './interface.css'
import './tactile.css'
import './admin-theme.css'

const octopusImage = new URL('./assets/octopus-reference.jpg', import.meta.url).href

function OctopusMascot({ className = '' }: { className?: string }) {
  return <svg className={'octopus-mascot ' + className} viewBox="0 584 1080 1080" role="img" aria-label="粉蓝小八爪鱼" focusable="false">
    <image href={octopusImage} width="1080" height="2344"/>
  </svg>
}

function ReplyHeader({ createdAt }: { createdAt: string }) {
  return <div className="reply-heading"><OctopusMascot/><div><b>小八来答</b><small>{stamp(createdAt)}</small></div></div>
}

function ErrorBox({ error }: { error: unknown }) { return error ? <div role="alert" className="error">{error instanceof Error ? error.message : String(error)}</div> : null }
function Empty({ text = '这里还没有记录。' }: { text?: string }) { return <div className="empty"><Inbox size={26}/><p>{text}</p></div> }
function Tag({ value }: { value: string }) { return <span className={'tag tag-' + value}>{label[value] ?? value}</span> }
function Field({ title, children, hint, invalid = false }: { title: string; children: ReactNode; hint?: string; invalid?: boolean }) {
  const id = useId()
  return <div className="field"><label htmlFor={id}>{title}</label>{isValidElement<{ id?: string; 'aria-invalid'?: boolean; 'aria-describedby'?: string }>(children) ? cloneElement(children, { id, 'aria-invalid': invalid || undefined, 'aria-describedby': hint ? id + '-hint' : undefined }) : children}{hint && <small id={id + '-hint'} className={invalid ? 'field-error' : undefined}>{hint}</small>}</div>
}
function HistoryList({ items }: { items: History[] }) { return <ol className="timeline">{items.map((h, i) => <li key={i}><small>{stamp(h.at)} · {h.by ?? '系统'} · {label[h.state ?? ''] ?? h.action}</small><p>{h.note}</p></li>)}</ol> }
function ProfileView({ person }: { person: Profile }) { return <div className="profile-detail"><span className="eyebrow">{person.role === 'parent' ? '家长自报资料' : '学生自报资料'}</span><p><strong>{person.name}</strong> · {person.phone}</p>{person.role === 'student' && <p>{person.major} / {person.class_name}</p>}{person.children.map((c, i) => <div className="child-summary" key={i}><b>孩子信息 {i + 1} · {c.name}</b><p>{c.major} / {c.class_name} · {c.phone}</p><small>填写关系：{c.relationship}（未核验）</small></div>)}</div> }

function isAdminRoute() {
  return location.hash.startsWith('#/')
    ? location.hash === '#/admin'
    : /^\/admin\/?$/.test(location.pathname)
}

function App() {
  const [isAdmin, setAdmin] = useState(isAdminRoute)
  const [modes, setModes] = useState<Modes | null>(null)
  useEffect(() => {
    const change = () => { if (location.hash !== '#main-content') setAdmin(isAdminRoute()) }
    addEventListener('hashchange', change)
    return () => removeEventListener('hashchange', change)
  }, [])
  useEffect(() => { api<Modes>('/modes').then(setModes).catch(() => setModes(null)) }, [])
  return <><a className="skip-link" href="#main-content">跳至主要内容</a><div className="environment">{modes ? modes.environment === 'production' ? '管理学院制度信息服务' : <><span className="demo-label">演示环境</span><span>{modes.material === 'synthetic' ? '制度资料为合成样本，请勿作为实际办理依据。' : '请核对制度适用范围。'}</span><span className="mode-detail">{modes.model === 'mock' ? '模拟问答' : 'DeepSeek问答'}{modes.embedding?.startsWith('BAAI/') ? ' · BGE 检索' : ''} · {modes.mail === 'mock' ? '邮件不外发' : 'SMTP通道'}</span></> : '服务状态暂未确认，请检查网络连接。'}</div>{isAdmin ? <AdminApp/> : <UserApp/>}</>
}

const blankProfile = (): Profile => ({ role: 'student', name: '', phone: '', major: '', class_name: '', children: [] })
const blankChild = (): Child => ({ name: '', phone: '', major: '', class_name: '', relationship: '母亲' })

function invalidInputs(scope: HTMLElement) {
  return Array.from(scope.querySelectorAll<HTMLInputElement>('input[name]'))
    .filter(input => !input.validity.valid)
}

function inputErrors(inputs: HTMLInputElement[]) {
  return Object.fromEntries(inputs.map(input => [input.name, input.validity.valueMissing
    ? '请填写此项'
    : input.name === 'password' ? '密码至少8位，需同时包含英文字母和数字'
    : input.name.endsWith('phone') ? '请输入有效的 11 位手机号'
    : '请检查填写格式']))
}

function UserLogin({ saved }: { saved: (p: Profile) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const invalid = invalidInputs(form)
    setFields(inputErrors(invalid))
    setError(null)
    if (invalid.length) { invalid[0].focus(); return }
    const data = new FormData(form)
    setBusy(true)
    try {
      saved(await api<Profile>('/login', 'POST', { username: data.get('username'), password: data.get('password') }))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  return <form className="registration-form" noValidate onSubmit={submit} onInput={event => {
    const field = (event.target as HTMLInputElement).name
    setFields(old => ({ ...old, [field]: '' }))
  }}>
    <Field title="本人姓名" hint={fields.username} invalid={!!fields.username}><input name="username" required maxLength={80} autoComplete="username"/></Field>
    <Field title="密码" hint={fields.password} invalid={!!fields.password}><input name="password" type="password" required maxLength={200} autoComplete="current-password"/></Field>
    <ErrorBox error={error}/>
    <div className="actions"><button className="primary" disabled={busy}>{busy ? '正在登录…' : '登录账号'}</button></div>
  </form>
}

function RegistrationForm({ initial, saved, cancel }: { initial?: Profile; saved: (p: Profile) => void; cancel?: () => void }) {
  const [data, setData] = useState<Profile>(initial ?? blankProfile())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [activeChild, setActiveChild] = useState<number | null>(initial ? null : 0)
  const change = (key: keyof Profile, value: string) => setData({ ...data, [key]: value })
  function validateVisibleForm(form: HTMLElement) {
    const invalid = invalidInputs(form)
    if (!invalid.length) return true
    const index = invalid[0].closest<HTMLElement>('[data-child-index]')?.dataset.childIndex
    flushSync(() => {
      setFields(inputErrors(invalid))
      if (index !== undefined) setActiveChild(Number(index))
    })
    invalid[0].focus()
    return false
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!validateVisibleForm(e.currentTarget as HTMLFormElement)) return
    setBusy(true); setError(null); setFields({})
    try {
      const { role, name, phone, major, class_name } = data
      const payload = { role, name, phone, major, class_name }
      const form = new FormData(e.target as HTMLFormElement)
      const credentials = initial ? {} : { password: form.get('password') }
      const p = await api<Profile>(initial ? '/me' : '/register', initial ? 'PUT' : 'POST', { ...payload, ...credentials, children: data.role === 'parent' ? data.children.map(({ name, phone, major, class_name, relationship }) => ({ name, phone, major, class_name, relationship })) : [] })
      saved(p)
    } catch (err) { setError(err); if (err instanceof ApiError) setFields(err.fields) } finally { setBusy(false) }
  }
  const input = (title: string, key: 'name' | 'phone' | 'major' | 'class_name', phone = false) => <Field title={title + ' *'} hint={fields[key]} invalid={!!fields[key]}><input name={key} required maxLength={phone ? 11 : 80} pattern={phone ? '1[3-9][0-9]{9}' : '.*\\S.*'} inputMode={phone ? 'tel' : 'text'} value={data[key]} onChange={e => change(key, e.target.value)} autoComplete={phone ? 'tel' : 'off'} /></Field>
  return <form onSubmit={submit} className="registration-form" noValidate onInput={event => {
    const field = (event.target as HTMLInputElement).name
    setFields(old => ({ ...old, [field]: '' }))
  }}>

    {!initial && <div className="segmented" aria-label="登记身份"><button type="button" aria-pressed={data.role === 'student'} onClick={() => setData({ ...data, role: 'student' })}>我是学生</button><button type="button" aria-pressed={data.role === 'parent'} onClick={() => { setData({ ...data, role: 'parent', children: data.children.length ? data.children : [blankChild()] }); if (!data.children.length) setActiveChild(0) }}>我是家长</button></div>}
    <div className="form-grid person-grid">{input(data.role === 'parent' ? '家长本人姓名' : '本人姓名', 'name')}{input('本人手机号', 'phone', true)}{data.role === 'student' && <>{input('专业', 'major')}{input('班级', 'class_name')}</>}</div>
    {!initial && <div className="form-grid person-grid">
      <Field title="密码 *" hint={fields.password ?? "密码至少8位，需同时包含英文字母和数字"} invalid={!!fields.password}><input name="password" type="password" required pattern="(?=.*[A-Za-z])(?=.*[0-9]).{8,}" minLength={8} maxLength={200} autoComplete="new-password"/></Field>
    </div>}
    {data.role === 'parent' && <div className="children-section"><div className="children-heading"><h3>孩子信息</h3><span>仅采集资料，不关联学生账号</span></div>{data.children.map((c, i) => <details className="child-card" key={i} open={activeChild === i} data-child-index={i}>
      <summary onClick={e => { e.preventDefault(); setActiveChild(activeChild === i ? null : i) }}><div><strong>孩子 {i + 1}{c.name ? ` · ${c.name}` : ''}</strong><small>{c.name && c.phone && c.major && c.class_name && c.relationship ? `${c.major} · ${c.class_name} · ${c.relationship}` : '请填写孩子的基本信息'}</small></div><span>{activeChild === i ? '收起' : c.name ? '修改' : '填写'}<ChevronRight size={15}/></span></summary>
      <fieldset className="child-fields"><legend className="visually-hidden">孩子信息 {i + 1}</legend><div className="form-grid child-grid">{(['name', 'relationship', 'phone', 'major', 'class_name'] as const).map(k => <div className={k === 'phone' ? 'full-field' : ''} key={k}><Field title={({ name: '孩子姓名', phone: '孩子手机号', major: '孩子专业', class_name: '孩子班级', relationship: '与孩子的关系' })[k] + ' *'} hint={fields[`children.${i}.${k}`]} invalid={!!fields[`children.${i}.${k}`]}><input name={`children.${i}.${k}`} required maxLength={k === 'phone' ? 11 : 80} inputMode={k === 'phone' ? 'tel' : 'text'} pattern={k === 'phone' ? '1[3-9][0-9]{9}' : '.*\\S.*'} value={c[k]} onChange={e => setData({ ...data, children: data.children.map((child, n) => n === i ? { ...child, [k]: e.target.value } : child) })}/></Field></div>)}</div><div className="child-actions">{data.children.length > 1 && <button type="button" className="text-button remove-child" onClick={() => { setData({ ...data, children: data.children.filter((_, n) => n !== i) }); setActiveChild(null) }}>移除此条孩子信息</button>}<button type="button" className="secondary" onClick={e => {
        const fields = e.currentTarget.closest('fieldset')!
        if (validateVisibleForm(fields)) setActiveChild(null)
      }}>完成填写</button></div></fieldset>
    </details>)}{data.children.length < 10 && <button type="button" className="text-button add-child" onClick={() => { setActiveChild(data.children.length); setData({ ...data, children: [...data.children, blankChild()] }) }}><Plus size={16}/>添加孩子信息</button>}</div>}
    <ErrorBox error={error}/><div className="actions"><button disabled={busy} className="primary">{busy ? '正在保存…' : initial ? '保存资料' : '注册并进入问答'}<ArrowUpRight size={18}/></button>{cancel && <button type="button" className="secondary" onClick={cancel}>返回问答</button>}</div>
  </form>
}

function IdentityCard({ profile, edit, logout }: { profile: Profile; edit: () => void; logout?: () => void }) {
  return (
    <div className="identity-card"><span className="status-dot"/><strong>{profile.name}</strong><Tag value={profile.role === 'parent' ? '家长本人' : '学生'}/><p>{profile.role === 'parent' ? `已填写 ${profile.children.length} 条孩子信息，均为自报资料。` : `${profile.major} / ${profile.class_name}`}</p><button className="text-button" onClick={edit}>修改{profile.role === 'parent' ? '本人及孩子信息' : '资料'} <ArrowUpRight size={14}/></button>{logout && <button type="button" className="text-button mobile-chat-logout" onClick={logout}><LogOut size={15}/>退出</button>}</div>
  )
}

function rebound(button: HTMLButtonElement) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  button.animate([
    { transform: 'scale(.96)' },
    { transform: 'scale(1.045)', offset: .62 },
    { transform: 'scale(1)' },
  ], { duration: 280, easing: 'ease-out' })
}

function UserApp() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [editing, setEditing] = useState(false)
  const [entryMode, setEntryMode] = useState<'register' | 'login' | null>(null)
  const [notesOpen, setNotesOpen] = useState(() => window.matchMedia('(min-width: 761px)').matches)
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 761px)')
    const syncNotes = (event: MediaQueryListEvent) => setNotesOpen(event.matches)
    desktop.addEventListener('change', syncNotes)
    return () => desktop.removeEventListener('change', syncNotes)
  }, [])
  useEffect(() => { api<Profile>('/me').then(setProfile).catch(e => { if (!(e instanceof ApiError && e.status === 401)) setError(e) }).finally(() => setLoading(false)) }, [])
  async function logout() { try { await api('/logout', 'POST'); setProfile(null); setEditing(false); setEntryMode(null); window.scrollTo(0, 0) } catch (e) { setError(e) } }
  function editProfile() {
    setEditing(true)
    window.scrollTo(0, 0)
  }
  const chatProfile = !loading && !editing ? profile : null
  const landing = !loading && !profile && !entryMode && !editing
  const userSiteRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const site = userSiteRef.current
    if (!landing || !site) return
    const viewport = window.visualViewport
    const fitLanding = () => {
      const visibleHeight = viewport?.scale === 1 ? viewport.height : window.innerHeight
      const pageTop = site.getBoundingClientRect().top + window.scrollY
      site.style.setProperty('--landing-height', `${Math.max(0, Math.floor(visibleHeight - pageTop))}px`)
    }
    fitLanding()
    window.addEventListener('resize', fitLanding)
    viewport?.addEventListener('resize', fitLanding)
    const banner = site.previousElementSibling
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fitLanding) : null
    if (banner) observer?.observe(banner)
    return () => {
      window.removeEventListener('resize', fitLanding)
      viewport?.removeEventListener('resize', fitLanding)
      observer?.disconnect()
      site.style.removeProperty('--landing-height')
    }
  }, [landing])
  return <div ref={userSiteRef} className={landing ? 'user-site user-site--landing' : 'user-site'}>
    <div className={chatProfile ? 'user-header user-header--chat' : 'user-header'}>
    <header className="site-header">
      <a className="brand" href="#/" aria-label="管理学院制度服务首页"><span className="brand-icon"><OctopusMascot/></span><span>管理学院<small>制度服务平台</small></span></a>
      <div className="header-actions"><nav className="public-nav" aria-label="网站导航"><a href="#/" aria-current="page">制度咨询</a><a href="#service-notes">使用说明</a></nav>{profile && <button className="text-button" onClick={logout}><LogOut size={16}/>退出</button>}</div>
    </header>
    {chatProfile && <div className="mobile-chat-profile"><IdentityCard profile={chatProfile} edit={editProfile} logout={logout}/></div>}
    </div>
    <ErrorBox error={error}/>
    {loading ? <main id="main-content" className="empty">正在连接服务…</main> : !profile || editing ? <main id="main-content" className={landing ? 'entry-layout entry-layout--landing' : 'entry-layout'}>
      {!profile && <section className="page-intro account-intro" aria-labelledby="account-intro-title">
        <h1 id="account-intro-title">制度咨询与办理指引</h1>
        <p className="account-intro-kicker">管理学院 · 你的校园小助手</p>
        <p>学生和家长可在这里查询学院制度，了解请假、综合测评和奖学金评定的办理条件。</p>
        {landing && <>
          <p>提问时可以注明业务年度。答复会列出引用的文件和条文位置，方便你核对原文及适用范围。</p>
          <p>现有资料暂时无法回答的咨询会记录到工作台，供学院后续核对和补充。</p>
        </>}
      </section>}
      <aside className="entry-intro" id="service-notes"><details className="service-disclosure" open={notesOpen} onToggle={e => setNotesOpen(e.currentTarget.open)}><summary>使用说明与服务范围<ChevronRight size={16}/></summary><div className="service-points">
        <section><BookOpen size={21} strokeWidth={1.5}/><div><h3>查询制度依据</h3><p>回答附有来源与条文定位，请结合业务年度、入学年级核对。</p></div></section>
        <section><FileText size={21} strokeWidth={1.5}/><div><h3>记录待处理问题</h3><p>资料不足或需要核实的问题将被记录，不自动向提问者发送通知。</p></div></section>
        <section><ShieldCheck size={21} strokeWidth={1.5}/><div><h3>妥善使用登记信息</h3><p>资料由本人填写。仅凭手机号不能核验身份，也不能查询他人记录。</p></div></section>
      </div><div className="boundary"><strong>服务范围</strong><p>本平台提供制度信息参考，不代替资格认定、审批或评审决定。</p></div></details>
      {landing && <a className="landing-admin-link" href="#/admin">管理工作台 <ArrowUpRight size={12}/></a>}</aside>
      <div className="account-entry">
        {landing && <div className="tactile-art"><OctopusMascot className="landing-octopus"/></div>}
        {landing && <ul className="account-intro-topics" aria-label="可咨询事项">
          <li>学生请假</li><li>综合测评</li><li>奖学金评定</li>
        </ul>}
        {!profile && <div className="account-actions" aria-label="账号操作">
          <button type="button" className="secondary account-action" aria-pressed={entryMode === 'register'} onClick={event => { rebound(event.currentTarget); setEntryMode('register') }}>注册账号</button>
          <button type="button" className="primary account-action" aria-pressed={entryMode === 'login'} onClick={event => { rebound(event.currentTarget); setEntryMode('login') }}>登录账号</button>
        </div>}
        {(editing || entryMode) && <section className="entry-card">
          <div className="section-heading">
            <h2>{editing ? '修改登记资料' : entryMode === 'register' ? '注册信息' : '账号登录'}</h2>
            {(editing || entryMode === 'register') && <p className="muted">请填写真实信息。</p>}
          </div>
          {!profile && entryMode === 'login' && <UserLogin saved={setProfile}/>}
          {(editing || entryMode === 'register') && <RegistrationForm initial={editing && profile ? profile : undefined} saved={p => { setProfile(p); setEditing(false) }} cancel={editing ? () => setEditing(false) : undefined}/>}
        </section>}
      </div>
    </main> : <Chat profile={profile} edit={editProfile}/>}
    <footer><div><strong>管理学院 · 制度服务平台</strong><span>回答仅供参考，请以适用的正式制度及学院审核结果为准。</span></div><a className="admin-link" href="#/admin">管理工作台 <ArrowUpRight size={14}/></a></footer>
  </div>
}

function AnswerView({ answer, open }: { answer: Answer; open?: (c: Citation) => void }) {
  const sources = new Map<string, Citation>()
  for (const item of answer.items ?? []) {
    for (const citation of item.citations) {
      if (!sources.has(citation.document_id)) sources.set(citation.document_id, citation)
    }
  }

  return <>
    {answer.items?.map((item, i) => <section className="answer-item" key={i}>
      <div className="answer-title"><Tag value={item.status}/><strong>{item.question}</strong></div>
      <p className="pre-wrap">{item.content}</p>
      {item.web_sources?.map(source => <details key={source.id} className="citation">
        <summary><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a>
          <small>发布日期：{source.published_at ?? '未提供'} · 查询日期：{source.retrieved_at}</small></summary>
        <p className="pre-wrap">网页摘录／搜索摘要：{source.excerpt}</p>
      </details>)}
    </section>)}
    {sources.size > 0 && <section className="answer-item">
      <span className="eyebrow">出处</span>
      {Array.from(sources.values(), c => <button className="citation" key={c.document_id} onClick={() => open?.(c)} disabled={!open}>
        <BookOpen size={16}/>
        <span><b>{c.title}</b><small>v{c.version} · {c.business_year ?? '长期制度'}</small></span>
        {open && <ChevronRight size={16}/>}
      </button>)}
    </section>}
    <p className="answer-notice">{answer.notice}</p>
  </>
}

const progressText = {
  received: '收到问题啦',
  searching: '正在查找学院资料',
  checking: '正在核对适用年级',
  composing: '正在整理回答',
}

function ProgressNotice({ stage = 'received' }: { stage?: NonNullable<Question['progress']> }) {
  const Icon = { received: CheckCircle2, searching: Search, checking: ShieldCheck, composing: FileText }[stage]
  return <div className="progress-notice" role="status" aria-atomic="true" data-stage={stage}>
    <span className="progress-notice-icon" aria-hidden="true"><Icon size={17}/></span>
    <span className="progress-notice-label" key={stage}>{progressText[stage]}</span>
    <span className="progress-notice-dots" aria-hidden="true"><i/><i/><i/></span>
  </div>
}

function newRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

function Chat({ profile, edit }: { profile: Profile; edit: () => void }) {
  const [draft, setDraft] = useState('')
  const [questions, setQuestions] = useState<Question[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [source, setSource] = useState<Citation | null>(null)
  const [year, setYear] = useState('')
  const [requestId, setRequestId] = useState(newRequestId())
  const [unresolved, setUnresolved] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const [showConditions, setShowConditions] = useState(false)
  const conditionsId = useId()
  const composerRef = useRef<HTMLDivElement>(null)
  const latestExchangeRef = useRef<HTMLElement>(null)
  const latestQuestion = questions.at(-1)
  const latestId = latestQuestion?.id
  const latestStatus = latestQuestion?.status
  useEffect(() => {
    if (!latestId) return
    latestExchangeRef.current?.scrollIntoView({ block: 'start', inline: 'nearest' })
  }, [latestId, latestStatus])
  useEffect(() => {
    const composer = composerRef.current
    const conversation = composer?.parentElement
    if (!composer || !conversation) return
    const reserveSpace = () => {
      conversation.style.setProperty('--composer-height', `${composer.offsetHeight}px`)
    }
    reserveSpace()
    const observer = new ResizeObserver(reserveSpace)
    observer.observe(composer)
    return () => observer.disconnect()
  }, [])
  const answerController = useRef<AbortController | null>(null)
  useEffect(() => () => answerController.current?.abort(), [])
  async function poll(id: string) {
    answerController.current?.abort()
    const controller = new AbortController()
    answerController.current = controller
    await streamAnswer(id, event => {
      if (event.type === 'complete') {
        const q = event.question
        setQuestions(old => old.map(x => x.id === id ? q : x))
        const context = q.answer.context
        if (year && context && context.business_year !== Number(year)) {
          setYear(String(context.business_year))
        }
        setUnresolved(null)
      } else {
        setQuestions(old => old.map(q => {
          if (q.id !== id) return q
          if (event.type === 'progress') return { ...q, status: 'pending', progress: event.stage }
          const items = event.type === 'item' && event.index === 0 ? [] : [...(q.answer.items ?? [])]
          if (event.type === 'item') items[event.index] = event.item
          else if (items[event.index]) items[event.index] = {
            ...items[event.index], content: items[event.index].content + event.text,
          }
          return { ...q, status: 'streaming', answer: { items } }
        }))
      }
    }, controller.signal)
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!draft.trim() || busy) return
    setBusy(true); setError(null)
    const optimisticId = 'local-' + requestId
    setQuestions(old => [...old, { id: optimisticId, text: draft, status: 'pending',
      answer: { items: [] }, progress: 'received', created_at: new Date().toISOString(), alert: null }])
    try {
      const q = await api<Question>('/questions', 'POST', { request_id: requestId, text: draft,
        business_year: year ? Number(year) : null,
        previous_id: follow && questions.length ? questions[questions.length - 1].id : null })
      if (window.matchMedia('(max-width: 760px)').matches) composerRef.current?.querySelector('textarea')?.blur()
      setQuestions(old => [...old.filter(x => x.id !== optimisticId && x.id !== q.id), q]); setUnresolved(q.id)
      setDraft(''); setRequestId(newRequestId()); setShowConditions(false); if (!q.alert) await poll(q.id); else setUnresolved(null)
    } catch (e) { setQuestions(old => old.filter(x => x.id !== optimisticId)); setError(e) } finally { setBusy(false) }
  }
  async function open(c: Citation) { try { setSource(await api<Citation>('/sources/' + c.chunk_id)) } catch (e) { setError(e) } }
  return <main id="main-content" className="chat-layout"><aside className="chat-aside"><span className="eyebrow">学生事务服务</span><h1>校园咨询</h1><p className="chat-description">查制度、聊知识，也可以轻松聊聊。</p><IdentityCard profile={profile} edit={edit}/><p className="muted">可连续追问办理条件、材料和时间；换个话题也能聊。</p><div className="boundary" id="service-notes">仅显示当前浏览器会话中的问答。退出后，完整记录仍由后台长期保存，不提供凭手机号找回。</div></aside><section className="conversation"><div className="conversation-top"><OctopusMascot/><b>小八 · 校园问答</b><span>陪你把事情理清楚</span></div><div className="messages" aria-live="polite">{questions.length === 0 && <div className="welcome"><span className="welcome-mark"><OctopusMascot/></span><h2>今天想了解点什么？</h2><p>奖学金、请假、综测，或是学习生活，都可以聊 (｡•̀ᴗ-)✧</p><div className="suggestions"><span className="suggestion-label">提问示例</span>{['2026年度奖学金申请需要哪些材料？', '2026年度请假流程是什么？', '校外实习补贴如何申请？'].map(t => <button key={t} onClick={() => { setDraft(t); setRequestId(newRequestId()) }}>{t}<ArrowUpRight size={14}/></button>)}</div></div>}{questions.map(q => <article className="exchange" key={q.id} ref={q.id === latestId ? latestExchangeRef : undefined}><div className="question-bubble">{q.text}</div>{q.alert && <div className="alert-card" role="alert"><Siren size={22}/><div><b>请优先保障人身安全</b><p>{q.alert.message}</p><small>{q.alert.notice}</small></div></div>} {!q.alert && <div className="response-bubble"><ReplyHeader createdAt={q.created_at}/>{q.status === 'pending' ? <ProgressNotice stage={q.progress ?? 'received'}/> : <AnswerView answer={q.answer} open={open}/>}</div>}</article>)}</div><div className="composer" ref={composerRef}><ErrorBox error={error}/>{unresolved && !busy && <button className="secondary" onClick={async () => { setBusy(true); try { await poll(unresolved); setError(null) } catch (e) { setError(e) } finally { setBusy(false) } }}>检查回答状态</button>}<form onSubmit={submit}><div id={conditionsId} className={showConditions ? "context-fields chat-conditions is-open" : "context-fields chat-conditions"}><label>业务年度 <input aria-label="业务年度" type="number" min="2000" max="2100" placeholder="默认本年" value={year} onChange={e => setYear(e.target.value)}/></label><label className="checkbox"><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)}/>连续对话（自动判断话题）</label></div><div className="composer-input-row"><button type="button" className="chat-conditions-toggle" aria-label="提问条件" aria-expanded={showConditions} aria-controls={conditionsId} onClick={() => setShowConditions(open => !open)}>{year ? year + "年" : "条件"}</button><div className="input-shell"><textarea aria-label="你的问题" required maxLength={2000} placeholder="想了解什么，问问小八…" value={draft} onChange={e => { setDraft(e.target.value); setRequestId(newRequestId()) }} rows={3}/><button className="send-button" aria-label="发送问题" disabled={busy || !draft.trim()}><ArrowUp size={20}/></button></div></div><small>{busy ? null : `${draft.length}/2000 · 资料不足的问题将进入后台，不自动通知原提问者。`}</small></form></div></section>{source && <div className="modal-backdrop" onClick={() => setSource(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="引用来源" onClick={e => e.stopPropagation()}><button className="close" aria-label="关闭来源" onClick={() => setSource(null)} autoFocus><X/></button><span className="eyebrow">引用来源 / 原文摘录</span><h2>{source.title}</h2><p>{source.publisher ? source.publisher + ' · ' : ''}版本 {source.version}</p><div className="meta-strip">业务年度 {source.business_year ?? '长期'} / 入学年级 {source.admission_year ?? '不限'}<br/>有效期 {source.effective_from ?? '未限定'} 至 {source.effective_to ?? '未限定'}</div>{source.source_status !== 'published' && <p className="error">该版本已停用或不可用，以下为当时引用。</p>}<blockquote>{source.excerpt}</blockquote><small>仅展示你已引用的原文片段。完整附件由管理员审核访问。</small><button className="secondary" onClick={() => setSource(null)}>返回问答</button></section></div>}</main>
}

const sections = [
  ['overview', '工作概览', LayoutDashboard], ['weekly', '后台周报', CalendarDays], ['events', '紧急事件', Siren], ['knowledge', '资料维护', Files], ['people', '登记资料', Users], ['questions', '问答记录', BookOpen],
] as const

function AdminApp() {
  const [authenticated, setAuth] = useState(false)
  const [section, setSection] = useState('overview')
  const activeSection = section === 'history' ? 'weekly' : section
  const [drawer, setDrawer] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setError(null); setBusy(true)
    const data = new FormData(e.currentTarget)
    try { await api('/admin/login', 'POST', { username: data.get('username'), password: data.get('password') }); setAuth(true) } catch (e) { setError(e) } finally { setBusy(false) }
  }
  if (!authenticated) return <div className="admin-entry"><main id="main-content" className="admin-login"><a className="text-button" href="#/">← 返回用户网站</a><div className="brand-icon"><OctopusMascot/></div><span className="eyebrow">管理学院 · 管理员入口</span><h1>管理工作台</h1><p className="muted">独立管理员认证。普通用户手机号不具备后台权限。</p><form onSubmit={login}><Field title="管理员账号"><input name="username" required autoComplete="username"/></Field><Field title="密码"><input name="password" required type="password" autoComplete="current-password" maxLength={200}/></Field><ErrorBox error={error}/><button className="primary" disabled={busy}>{busy ? '登录中…' : '登录工作台'}<ArrowUpRight size={18}/></button></form></main></div>
  return <div className="admin-shell"><aside id="admin-navigation" className={'admin-sidebar ' + (drawer ? 'expanded' : '')}><a className="brand" href="#/admin"><span className="brand-icon"><OctopusMascot/></span><span>管院制度服务<small>小八 · 管理工作台</small></span></a><span className="sidebar-caption">管理导航</span><nav>{sections.map(([id, title, Icon]) => <button key={id} aria-current={activeSection === id ? 'page' : undefined} onClick={() => { setSection(id); setDrawer(false); setError(null) }}><Icon size={18}/>{title}{activeSection === id && <ChevronRight size={14}/>}</button>)}</nav><div className="sidebar-bottom"><p>完整联系方式仅限授权管理使用。</p><a href="#/">用户网站 <ArrowUpRight size={14}/></a><button className="text-button" onClick={async () => { try { await api('/admin/logout', 'POST'); setAuth(false) } catch (e) { setError(e) } }}><LogOut size={16}/>退出后台</button></div></aside><div className="admin-main"><header className="admin-top"><button className="mobile-menu" aria-label={drawer ? "收起管理菜单" : "展开管理菜单"} aria-expanded={drawer} aria-controls="admin-navigation" onClick={() => setDrawer(!drawer)}>{drawer ? <X/> : <Menu/>}</button><span>管理学院 / {sections.find(x => x[0] === activeSection)?.[1]}</span><button className="text-button" onClick={() => setRefresh(refresh + 1)}><RefreshCw size={15}/>刷新数据</button></header><main id="main-content" className="admin-content"><div className="page-title"><div><span className="eyebrow">管理学院制度服务</span><h1>{sections.find(x => x[0] === activeSection)?.[1]}</h1></div><span className="muted">北京时间 · 后台处理</span></div><ErrorBox error={error}/><AdminSection key={section + refresh} section={section} navigate={setSection}/></main></div></div>
}

function Load<T>({ path, children, autoRefresh = false }: { path: string; children: (data: T, reload: () => void) => ReactNode; autoRefresh?: boolean }) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let active = true
    let loading = false
    async function fetchData(reportError: boolean) {
      if (loading) return
      loading = true
      try {
        const next = await api<T>(path)
        if (active) { setData(next); setError(null) }
      } catch (e) {
        if (active && reportError) setError(e)
      } finally {
        loading = false
      }
    }
    void fetchData(true)
    const timer = autoRefresh ? window.setInterval(() => {
      if (document.visibilityState === 'visible') void fetchData(false)
    }, 5000) : undefined
    return () => { active = false; if (timer !== undefined) window.clearInterval(timer) }
  }, [path, version, autoRefresh])
  const reload = () => { setData(null); setError(null); setVersion(x => x + 1) }
  if (error) return <><ErrorBox error={error}/><button className="secondary" onClick={reload}>重新加载</button></>
  if (data === null) return <div className="loading panel">正在读取实际记录…</div>
  return <>{children(data, reload)}</>
}

function AdminSection({ section, navigate }: { section: string; navigate: (s: string) => void }) {
  if (section === 'overview') return <Load<{ weekly: { new: number }; total_issue_groups: number; pending_issue_groups: number; open_events: number; mail_attention: number; pending_questions: number; documents: number }> autoRefresh path="/admin/overview">{d => <><div className="overview-banner"><div><span className="eyebrow">待办与处理进度</span><h2>工作事项</h2><p>当前待解决问题组 <strong>{d.pending_issue_groups} 组</strong></p></div><OctopusMascot className="admin-overview-mascot"/></div><div className="stats">{[[d.open_events, '未处理紧急事件', 'events'], [d.mail_attention, '邮件发送异常', 'events'], [d.weekly.new, '本周新增问题组', 'weekly'], [d.total_issue_groups, '全部历史问题组', 'history']].map(([n, title, target]) => <button key={String(title)} className="stat-card" onClick={() => navigate(String(target))}><span>{title}</span><strong>{n}</strong>{(target === 'weekly' || target === 'history') && <span>含已解决记录</span>}<ArrowUpRight size={20}/></button>)}</div><div className="two-columns"><section className="panel"><h3>资料维护与复测</h3><p>上传的资料解析后自动发布，系统会在后台复测相关待处理问题。</p><button className="secondary" onClick={() => navigate('knowledge')}>维护制度资料<ArrowUpRight size={16}/></button></section><section className="panel"><h3>当前待关注</h3><p>待生成回答：{d.pending_questions} 条</p><p>资料版本：{d.documents} 份</p><p className="muted">周报在北京时间每周一 09:00 发送上周新增问题。邮件提交成功不等于人工处置完成。</p></section></div></>}</Load>
  if (section === 'weekly' || section === 'history') return <><div className="filter-bar"><button className={section === 'weekly' ? 'primary' : 'secondary'} onClick={() => navigate('weekly')}>本周新增问题组</button><button className={section === 'history' ? 'primary' : 'secondary'} onClick={() => navigate('history')}>全部历史问题组</button></div>{section === 'weekly' ? <WeeklyPage/> : <IssueHistory/>}</>
  if (section === 'knowledge') return <Load<{ max_upload_mb: number }> path="/modes">{modes => <KnowledgePage uploadLimitMb={modes.max_upload_mb}/>}</Load>
  if (section === 'events') return <EventsPage/>
  if (section === 'people') return <Load<Profile[]> path="/admin/people">{data => data.length ? <div className="record-grid">{data.map((p, i) => <section className="panel" key={p.id ?? i}><ProfileView person={p}/><small>登记编号 {p.id}</small></section>)}</div> : <Empty/>}</Load>
  if (section === 'questions') return <Load<Question[]> path="/admin/questions">{data => data.length ? data.map(q => <details className="panel" key={q.id}><summary><b>{q.text}</b><small>{stamp(q.created_at)}</small></summary>{q.snapshot && <ProfileView person={q.snapshot}/>}<AnswerView answer={q.answer}/></details>) : <Empty/>}</Load>
  return null
}

const reasons = ['missing_material', 'needs_clarification', 'document_conflict', 'validation_failed', 'system_error', 'out_of_scope']
const states = ['open', 'in_progress', 'awaiting_material', 'awaiting_retest', 'completed', 'dismissed']

function IssueStatusSummary({ title, items }: { title: string; items: Issue[] }) {
  const total = items.length
  const resolved = items.filter(item => item.state === 'completed').length
  const dismissed = items.filter(item => item.state === 'dismissed').length
  const categories = [
    { title: '已解决', count: resolved },
    { title: '无法解决', count: dismissed },
    { title: '待解决', count: total - resolved - dismissed },
  ]

  return <section className="panel issue-status-summary">
    <h2>{title} <strong>{total} 组</strong></h2>
    <ul>{categories.map(category => <li key={category.title}>
      {category.title} <strong>{category.count}</strong>
    </li>)}</ul>
  </section>
}

function IssueHistory() {
  const [reason, setReason] = useState('')
  const [state, setState] = useState('')
  const [keyword, setKeyword] = useState('')
  const [search, setSearch] = useState('')
  return <><form className="filter-bar" onSubmit={e => { e.preventDefault(); setSearch(keyword) }}><Search size={17}/><input aria-label="搜索问题或人员" placeholder="问题、姓名或手机号" value={keyword} onChange={e => setKeyword(e.target.value)}/><select aria-label="原因筛选" value={reason} onChange={e => setReason(e.target.value)}><option value="">全部原因</option>{reasons.map(r => <option key={r} value={r}>{label[r]}</option>)}</select><select aria-label="状态筛选" value={state} onChange={e => setState(e.target.value)}><option value="">全部状态</option>{states.map(s => <option key={s} value={s}>{label[s]}</option>)}</select><button className="secondary">查询</button></form><Load<Issue[]> autoRefresh path={'/admin/issues?' + new URLSearchParams({ reason, state, keyword: search })}>{(items, reload) => <><IssueStatusSummary title={reason || state || search ? '当前筛选结果' : '全部历史问题组'} items={items}/><IssueList items={items} reload={reload}/></>}</Load></>
}

function IssueList({ items, reload }: { items: Issue[]; reload: () => void }) { return items.length ? <div className="issue-list">{items.map(i => <IssueCard key={i.id} issue={i} siblings={items} reload={reload}/>)}</div> : <Empty text="没有符合条件的问题。"/> }

function IssueCard({ issue, siblings, reload }: { issue: Issue; siblings: Issue[]; reload: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [mergeId, setMergeId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function action(path: string, method: string, body?: unknown) { setBusy(true); setError(null); try { await api(path, method, body); reload() } catch (e) { setError(e) } finally { setBusy(false) } }
  return <article className="issue-card"><button className="issue-summary" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><span className="issue-number">{issue.id.slice(0, 6)}</span><div><div className="tag-row"><Tag value={issue.reason}/><Tag value={issue.state}/><span className="tag">周报邮件 · {issue.occurrences.length === 1 ? (label[issue.occurrences[0].weekly_mail?.status ?? ''] ?? '尚未入批次') : '查看各条记录'}</span></div><h3>{issue.text}</h3><small>{issue.count} 次原始记录</small></div><ChevronRight className={expanded ? 'rotated' : ''} size={20}/></button>{expanded && <div className="issue-body">{issue.occurrences.map(o => <details className="occurrence" key={o.issue_id}><summary>{o.snapshot.name} · {o.snapshot.phone} · {stamp(o.created_at)}</summary><ProfileView person={o.snapshot}/><p>周报邮件 · {label[o.weekly_mail?.status ?? ''] ?? '尚未入批次'}{o.weekly_mail?.sent_at ? ' · ' + stamp(o.weekly_mail.sent_at) : ''}</p><p>业务年度 {o.context.business_year} / 入学年级 {o.context.admission_year ?? '未提供'}</p><p className="pre-wrap">原问题：{o.question}</p><AnswerView answer={o.answer}/></details>)}<HistoryList items={issue.history}/>{issue.retests.map((r, i) => <details className="retest" key={i}><summary>{stamp(r.at)} · 复测{r.passed ? '通过' : '未通过'}</summary><p>依据资料编号：{r.evidence_ids.join(', ')}</p>{r.results.map((x, j) => <AnswerView key={j} answer={x.answer}/>)}</details>)}<p className="muted">发布相关资料后，系统会自动复测。有出处的原问题全部通过后，这组问题会标为已解决。</p><ErrorBox error={error}/><div className="merge-controls"><h4>合并相似问题</h4><p className="muted">请核对年度、年级与原问题条件。所有人员和原始问题都会保留。</p><select aria-label="选择合并的问题" value={mergeId} onChange={e => setMergeId(e.target.value)}><option value="">选择相同原因的原始问题</option>{siblings.filter(x => x.id !== issue.id && x.reason === issue.reason).map(x => <option value={x.id} key={x.id}>{x.text} · {x.id.slice(0, 6)}</option>)}</select><button className="secondary" disabled={busy || !mergeId} onClick={() => { if (confirm('确认已核对条件？合并保留全部原始记录。')) void action(`/admin/issues/${issue.id}/merge`, 'POST', { source_ids: [mergeId], note: '管理员已确认合并相似问题' }) }}>合并到本问题</button></div></div>}</article>
}

function WeeklyPage() {
  const [day, setDay] = useState('')
  return <><div className="filter-bar"><Field title="选择历史周内任意日期"><input aria-label="周报日期" type="date" value={day} onChange={e => setDay(e.target.value)}/></Field><p className="muted">北京时间每周一 09:00 发送上周新增问题。</p></div><Load<Weekly> autoRefresh path={'/admin/weekly' + (day ? '?day=' + day : '')}>{(w, reload) => <><p className="week-range">{stamp(w.start)} — {stamp(w.end)}（结束时间不含）</p><p className="muted">该周邮件 · {w.mail_enabled ? (label[w.weekly_mail?.status ?? ''] ?? '尚未入批次') : '已停用'}{w.weekly_mail?.sent_at ? ' · ' + stamp(w.weekly_mail.sent_at) : ''}</p><div className="tag-row">{Object.entries(w.counts.by_reason).map(([k, n]) => <span className="tag" key={k}>{label[k]} {n}</span>)}</div><IssueStatusSummary title={day ? '所选周新增问题组' : '本周新增问题组'} items={w.new}/><IssueList items={w.new} reload={reload}/></>}</Load></>
}

function KnowledgePage({ uploadLimitMb }: { uploadLimitMb: number }) {
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [generation, setGeneration] = useState(0)
  const [notice, setNotice] = useState('')

  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const data = new FormData(form)
    const file = data.get('file')
    if (file instanceof File && file.size > uploadLimitMb * 1024 ** 2) {
      setNotice('')
      setError(new Error(`所选文件为 ${(file.size / 1024 ** 2).toFixed(1)} MB，超过单次上传限制 ${uploadLimitMb} MB。请压缩或拆分后上传。`))
      return
    }
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      const result = await api<Doc | { documents: Pick<Doc, 'id' | 'status' | 'parse_status'>[]; skipped: number }>(
        '/admin/documents/upload', 'POST', data)
      if ('documents' in result) {
        const published = result.documents.filter(doc => doc.status === 'published').length
        setNotice(`已自动发布 ${published} 份，${result.documents.length - published} 份处理失败，跳过 ${result.skipped} 个不支持的条目。`)
      } else {
        setNotice(result.status === 'published' ? '资料已自动发布，可以提问测试。' : (result.parse_note || '资料处理失败，请检查文件后重新上传。'))
      }
      form.reset()
      setGeneration(x => x + 1)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return <><div className="panel upload-panel"><div><h3>制度资料管理</h3><p>上传后自动解析并发布，能识别的业务年度会自动填入。上传结果会显示处理状态。</p></div><form onSubmit={upload}><input aria-label="选择制度文件" type="file" name="file" accept=".pptx,.xlsx,.zip,.pdf,.docx,.csv" required/><button className="primary" disabled={busy}>{busy ? '正在上传、识别与发布…' : '上传资料'}</button></form><small>支持 PPTX / XLSX / ZIP / PDF / DOCX / CSV，单次上传不超过 {uploadLimitMb} MB。ZIP 最多导入 20 份资料。自动识别扫描页及文档内的图片文字，处理成功后直接发布。</small></div>{notice && <p role="status">{notice}</p>}<ErrorBox error={error}/><Load<Doc[]> key={generation} path="/admin/documents">{docs => docs.length ? docs.map(d => <DocumentCard key={d.id} document={d}/>) : <Empty text="还没有资料，请上传制度文件。"/>}</Load></>
}

function DocumentCard({ document: d }: { document: Doc }) {
  return <article className="panel document-card">
    <div className="tag-row"><Tag value={d.status}/><Tag value={d.parse_status}/></div>
    <h3>{d.title}</h3>
    <small>版本 {d.version} · {d.business_year ?? '长期'} · 资料编号 <code>{d.id}</code></small>
  </article>
}

function EventsPage() {
  return <><p className="boundary">识别可能误报或漏报。邮件状态与人工处置分别记录；模拟邮件不会外发。投递不确定的任务需人工核对邮箱，系统不会自动重复发送。</p><Load<Emergency[]> path="/admin/events">{(events, reload) => { const visibleEvents = events.filter(event => event.state === 'open' || event.state === 'following'); return visibleEvents.length ? visibleEvents.map(e => <EventCard key={e.id} event={e} reload={reload}/>) : <Empty text="没有待处理或跟进中的紧急事件。"/> }}</Load></>
}

function EventCard({ event: e, reload }: { event: Emergency; reload: () => void }) {
  const [state, setState] = useState(e.state)
  const [note, setNote] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  async function action(retry = false) { setBusy(true); setError(null); try { await api('/admin/events/' + e.id + (retry ? '/retry' : ''), retry ? 'POST' : 'PATCH', retry ? undefined : { state, note }); reload() } catch (e) { setError(e) } finally { setBusy(false) } }
  return <details className="panel event-card"><summary><div><div className="tag-row"><Tag value={e.state}/><Tag value={e.mail.status}/><Tag value={e.mail.mode === 'mock' ? '模拟通道' : 'SMTP'}/></div><h3>{e.text}</h3><small>{stamp(e.created_at)} · 事件 {e.id.slice(0, 8)}</small></div><Siren size={22}/></summary><ProfileView person={e.snapshot}/><details className="source-preview"><summary>邮件正文预览</summary><pre>{e.mail.body}</pre></details><p>尝试次数 {e.mail.attempts} · {e.mail.error_code || '暂无错误'} </p>{e.mail.attempt_history.map((a, i) => <p className="muted" key={i}>{stamp(a.at)} · {label[a.result] ?? a.result}</p>)}<HistoryList items={e.history}/><form onSubmit={ev => { ev.preventDefault(); void action() }}><Field title="人工处置状态"><select value={state} onChange={ev => setState(ev.target.value)}>{['open', 'following', 'closed'].map(s => <option key={s} value={s}>{label[s]}</option>)}</select></Field><Field title="处置记录 *"><textarea value={note} maxLength={4000} required onChange={ev => setNote(ev.target.value)}/></Field><ErrorBox error={error}/><div className="actions"><button disabled={busy} className="primary"><CheckCircle2 size={16}/>记录人工处置</button>{['failed', 'unconfigured', 'retry'].includes(e.mail.status) && <button className="secondary" type="button" disabled={busy} onClick={() => { if (e.mail.mode === 'mock' || confirm('此操作将重试真实邮件任务，确认继续？')) void action(true) }}>{e.mail.mode === 'mock' ? '模拟重试' : '重试邮件'}</button>}</div></form></details>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>)
