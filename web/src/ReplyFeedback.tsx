import { useId, useRef, useState, type FormEvent } from 'react'
import { MessageSquarePlus, ThumbsDown, ThumbsUp } from 'lucide-react'
import { api, stamp, type ReplyFeedback } from './api'
import './reply-feedback.css'

export function FeedbackControls({ questionId, index, initial }: {
  questionId: string; index: number; initial?: ReplyFeedback
}) {
  const [feedback, setFeedback] = useState(initial)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(initial?.comment ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const toggle = useRef<HTMLButtonElement>(null)
  const formId = useId()

  async function save(change: { rating: 'like' | 'dislike' } | { comment: string }) {
    setBusy(true)
    setStatus('')
    setError('')
    try {
      const saved = await api<ReplyFeedback>(`/questions/${encodeURIComponent(questionId)}/feedback/${index}`, 'PATCH', change)
      setFeedback(saved)
      if ('comment' in change) {
        setOpen(false)
        toggle.current?.focus()
        setStatus('反馈已提交')
      } else {
        setStatus('评价已保存')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败，请重试。')
    } finally {
      setBusy(false)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (draft.trim() && !busy) void save({ comment: draft.trim() })
  }

  return <div className="reply-feedback">
    <div className="reply-feedback-actions" role="group" aria-label="评价这条回复">
      {([['like', '赞', ThumbsUp], ['dislike', '踩', ThumbsDown]] as const).map(([rating, title, Icon]) =>
        <button type="button" key={rating} aria-label={title} title={title} aria-pressed={feedback?.rating === rating}
          disabled={busy} onClick={() => { if (feedback?.rating !== rating) void save({ rating }) }}>
          <Icon size={14} aria-hidden="true"/>
        </button>)}
      <button type="button" ref={toggle} aria-label="反馈具体问题" title="反馈具体问题" aria-expanded={open}
        aria-controls={formId} data-commented={Boolean(feedback?.comment)} onClick={() => setOpen(!open)}>
        <MessageSquarePlus size={14} aria-hidden="true"/>
      </button>
      {status && <small role="status">{status}</small>}
    </div>
    {open && <form className="reply-feedback-form" id={formId} onSubmit={submit}>
      <textarea aria-label="具体问题" placeholder="这条回复有什么问题？" rows={2} maxLength={1000} required
        value={draft} onChange={event => setDraft(event.target.value)}/>
      <button type="submit" className="secondary" disabled={busy || !draft.trim()}>{busy ? '提交中…' : '提交反馈'}</button>
    </form>}
    {error && <small className="reply-feedback-error" role="alert">{error}</small>}
  </div>
}

export function FeedbackRecord({ entry }: { entry: ReplyFeedback }) {
  return <div className="reply-feedback-record">
    <small>{entry.rating === 'like' ? '赞' : entry.rating === 'dislike' ? '踩' : '文字反馈'} · {stamp(entry.updated_at)}</small>
    {entry.comment && <p className="pre-wrap">{entry.comment}</p>}
  </div>
}
