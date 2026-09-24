import { useEffect, useState } from 'react'
import { askAssistant, fetchDiagnostics } from '../services/backendService'
import { formatMessage } from '../data/i18n'

// Alarm explainer (HLD §4.9). action_class always comes from the catalogue, never the LLM (I3).
const ACTION = {
  STOP: { label: 'Stop', pill: 'danger' },
  MONITOR: { label: 'Monitor', pill: 'warning' },
  CONTINUE: { label: 'Continue', pill: 'info' },
  UNDOCUMENTED: { label: 'No guidance', pill: '' },
}
const SUGGESTIONS = ['Can I keep working?', 'How do I climb down safely?', 'What do I check on the walkaround?']
const DTC_RULES = ['R16', 'R17', 'R18']

const ActionPill = ({ value }) => {
  const a = ACTION[value] || ACTION.UNDOCUMENTED
  return <span className={`status-pill ${a.pill}`}>{a.label.toUpperCase()}</span>
}

function FaultCard({ card, onAsk }) {
  const [open, setOpen] = useState(card.action_class === 'STOP')
  return (
    <div className="panel panel-light lesson-card">
      <button type="button" className="lesson-toggle" onClick={() => setOpen(!open)}>
        <div className="lesson-main">
          <div className="lesson-title-row">
            <span className="rule-code">{card.code_id || `SPN ${card.spn} FMI ${card.fmi}`}</span>
            <ActionPill value={card.action_class} />
          </div>
          <p className="panel-title">{card.component || 'Undocumented fault code'}</p>
        </div>
        <span className="lesson-arrow">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="lesson-panel">
          {card.what_happened ? (
            <>
              <p className="eyebrow">What happened</p>
              <p className="lesson-content">{card.what_happened}</p>
              <p className="eyebrow">Why it matters</p>
              <p className="lesson-content">{card.why_it_matters}</p>
              <p className="eyebrow">What to do</p>
              <p className="lesson-content">{card.what_to_do}</p>
            </>
          ) : (
            <p className="lesson-content">{formatMessage('assistant.refusal')}</p>
          )}
          <p className="muted-text" style={{ fontSize: '0.75rem', marginBottom: '0.75rem' }}>Source: {card.source_doc || '—'}</p>
          {card.code_id && (
            <button type="button" className="btn-small" onClick={() => onAsk(card.code_id)}>
              Ask about this code
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function Assistant({ machineId, alerts = [] }) {
  const [diag, setDiag] = useState(null)
  const [diagError, setDiagError] = useState('')
  const [question, setQuestion] = useState('')
  const [contextCode, setContextCode] = useState(null)
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState(null)
  const [askError, setAskError] = useState('')

  // Refetch when a fault-code rule raises/clears, plus a slow poll for codes no rule covers.
  const dtcKey = alerts.filter((a) => DTC_RULES.includes(a.rule)).map((a) => a.id).join(',')
  useEffect(() => {
    let alive = true
    const load = () =>
      fetchDiagnostics(machineId)
        .then((d) => {
          if (!alive) return
          setDiag(d)
          setDiagError('')
        })
        .catch((e) => alive && setDiagError(e.message))
    load()
    const interval = setInterval(load, 20000)
    return () => {
      alive = false
      clearInterval(interval)
    }
  }, [machineId, dtcKey])

  const ask = async (text = question) => {
    const q = text.trim()
    if (!q || busy) return
    setQuestion(q)
    setBusy(true)
    setAskError('')
    try {
      setAnswer(await askAssistant(q, { contextCode }))
    } catch (e) {
      setAskError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const faults = diag?.diagnostics || []
  const refused = answer && answer.citations.length === 0

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Alarm Explainer</p>
        <p className="display-title large">Help</p>
        <p className="muted-text">Plain-language guidance from the fault catalogue and site safety notes.</p>
      </div>

      <div className="panel panel-light">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <p className="eyebrow">Active Fault Codes ({faults.length})</p>
          {diag?.as_of && (
            <span className="muted-text" style={{ fontSize: '0.75rem', margin: 0 }}>
              as of {new Date(diag.as_of).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
        {diagError ? (
          <p className="muted-text" style={{ color: '#dc2626' }}>Fault codes unavailable: {diagError}</p>
        ) : !diag ? (
          <p className="muted-text">Loading…</p>
        ) : faults.length === 0 ? (
          <p style={{ fontSize: '0.85rem', color: '#10b981', fontWeight: 600, padding: '0.5rem 0' }}>✓ No active fault codes.</p>
        ) : null}
      </div>

      {faults.length > 0 && (
        <div className="lesson-list">
          {faults.map((card) => (
            <FaultCard key={`${card.code_id}-${card.spn}-${card.fmi}`} card={card} onAsk={setContextCode} />
          ))}
        </div>
      )}

      <div className="panel panel-light">
        <p className="eyebrow">Ask the Manual</p>
        {contextCode && (
          <div className="btn-row" style={{ marginTop: '0.5rem' }}>
            <button type="button" className="chip selected" onClick={() => setContextCode(null)}>
              About {contextCode} ✕
            </button>
          </div>
        )}
        <div className="btn-row">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="chip" disabled={busy} onClick={() => ask(s)}>
              {s}
            </button>
          ))}
        </div>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), ask())}
          rows={2}
          maxLength={500}
          placeholder="e.g. What does error E001 mean?"
          style={{ width: '100%', borderRadius: '0.5rem', padding: '0.5rem', border: '1px solid #d4d4d8', margin: '0.75rem 0', fontSize: '15px', fontFamily: 'inherit', resize: 'vertical' }}
        />
        <button type="button" className="primary-lesson-button" disabled={busy || !question.trim()} onClick={() => ask()}>
          {busy ? 'Thinking…' : 'Ask'}
        </button>
        {askError && <p className="muted-text" style={{ color: '#dc2626' }}>Assistant unavailable: {askError}</p>}
      </div>

      {answer && (
        <div className="panel panel-light">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <p className="eyebrow">Answer</p>
            {answer.action_class !== 'UNDOCUMENTED' && <ActionPill value={answer.action_class} />}
          </div>
          <p className="lesson-content">{answer.answer}</p>
          {answer.model === 'template' && !refused && (
            <p className="muted-text" style={{ fontSize: '0.8rem', color: '#d97706' }}>{formatMessage('assistant.template_note')}</p>
          )}
          {answer.citations.length > 0 && (
            <div className="rule-list">
              {answer.citations.map((c) => (
                <div key={c.n} className="rule-item">
                  <span className="rule-code">[{c.n}]</span>
                  <span className="rule-text">{c.code_id || c.section}</span>
                </div>
              ))}
            </div>
          )}
          <p className="muted-text" style={{ fontSize: '0.75rem' }}>
            {answer.model === 'local' ? 'On-device model' : 'Standard guidance'} · {(answer.latency_ms / 1000).toFixed(1)} s
          </p>
        </div>
      )}

      <div
        style={{
          background: 'rgba(251, 191, 36, 0.1)',
          border: '1px solid rgba(251, 191, 36, 0.3)',
          borderRadius: '0.5rem',
          padding: '0.75rem 1rem',
          fontSize: '0.75rem',
          color: '#d97706',
          lineHeight: 1.4,
        }}
      >
        Advisory only. The action (Stop / Monitor / Continue) comes from the fault catalogue, never from the AI.
      </div>
    </div>
  )
}
