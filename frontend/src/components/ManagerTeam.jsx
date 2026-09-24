import { useEffect, useState } from 'react'
import { fetchOperator, normalizeAlert, taskBadge } from '../services/backendService'
import { formatMessage } from '../data/i18n'

const SHIFT_DOT = { ACTIVE: 'success', PLANNED: 'info', CLOSED: 'caution' }
const READINESS_PILL = { RED: 'danger', YELLOW: 'warning', GREEN: '' }
const LOCKED = ['IN_PROGRESS', 'DONE']

const when = (iso) =>
  iso ? new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '—'

function shiftLine(op) {
  if (!op.shift) return 'No shift today'
  return `${op.shift.machine_id} · ${op.shift.status.toLowerCase()}`
}

function OperatorRow({ op, onOpen }) {
  const t = op.tasks_today
  return (
    <div role="button" tabIndex={0} className="alert-item" style={{ cursor: 'pointer' }} onClick={() => onOpen(op.operator_id)}>
      <span className={`status-dot ${SHIFT_DOT[op.shift?.status] || 'warning'}`} />
      <div className="alert-copy">
        <p className="alert-title">{op.name}</p>
        <p className="alert-message">
          {op.operator_id} · {shiftLine(op)} · {t.done || 0}/{t.total} tasks done
        </p>
      </div>
      <div className="alert-meta">
        {op.readiness && <span className={`status-pill ${READINESS_PILL[op.readiness.rating] ?? ''}`}>{op.readiness.rating}</span>}
        <p className="alert-time">{op.alerts_7d} alerts · 7d</p>
      </div>
    </div>
  )
}

function Detail({ operatorId, refreshKey, onBack, onAssign, onReassign }) {
  const [op, setOp] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    fetchOperator(operatorId)
      .then((d) => {
        if (!live) return
        setOp(d.operator)
        setError('')
      })
      .catch((e) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [operatorId, refreshKey])

  if (!op) {
    return (
      <div className="stack gap-4">
        <button type="button" className="btn-small" style={{ alignSelf: 'flex-start' }} onClick={onBack}>← Team</button>
        <p className="muted-text" style={error ? { color: '#dc2626' } : undefined}>{error || 'Loading…'}</p>
      </div>
    )
  }

  const t = op.tasks_today
  const metrics = [
    { label: 'Tasks today', value: `${t.done || 0}/${t.total}` },
    { label: 'Alerts · 7d', value: op.alerts_7d },
    { label: 'Lessons due', value: op.lessons_pending },
  ]
  const canAssign = op.role === 'operator'

  return (
    <div className="stack gap-4">
      <button type="button" className="btn-small" style={{ alignSelf: 'flex-start' }} onClick={onBack}>← Team</button>

      <div className="panel panel-dark">
        <p className="eyebrow">{op.operator_id} · {op.employee_code} · {op.role}</p>
        <p className="display-title">{op.name}</p>
        <p className="muted-text">
          {[op.experience_level?.toLowerCase(), op.experience_years != null && `${op.experience_years} yrs`]
            .filter(Boolean)
            .join(' · ') || 'Experience not recorded'}
        </p>
        <p className="muted-text">
          Certified: {(op.certified_families || []).join(', ') || 'none'}
          {op.cert_expiry ? ` · until ${op.cert_expiry}` : ''}
        </p>
        <div className="metric-grid" style={{ marginTop: 16 }}>
          {metrics.map((m) => (
            <div key={m.label} className="metric-box">
              <p className="metric-value">{m.value}</p>
              <p className="metric-label">{m.label}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Today</p>
        <div className="task-header-row">
          <div>
            <p className="panel-title">{op.shift ? op.shift.machine_id : 'No shift'}</p>
            <p className="muted-text">{op.shift ? `${op.shift.shift_id} · ${op.shift.status.toLowerCase()}` : 'Assigning a task plans one.'}</p>
          </div>
          <div className="eta-box">
            <p className="eyebrow">Readiness</p>
            <p className="eta-value">{op.readiness?.rating || '—'}</p>
            <p className="muted-text" style={{ fontSize: '0.7rem' }}>{op.readiness ? when(op.readiness.ts) : 'Not checked'} · advice only</p>
          </div>
        </div>
        {canAssign && (
          <button
            type="button"
            className="primary-lesson-button"
            style={{ marginTop: 16 }}
            onClick={() => onAssign({ operatorId: op.operator_id, machineId: op.shift?.machine_id })}
          >
            Assign task to {op.name}
          </button>
        )}
      </div>

      <div>
        <p className="section-label">Tasks</p>
        <div className="task-list">
          {op.tasks.length === 0 && <p className="muted-text">No tasks assigned.</p>}
          {op.tasks.map((task) => {
            const [cls, label] = taskBadge(task.status)
            return (
              <div key={task.task_id} className="panel panel-light task-card">
                <div className="task-row-top">
                  <div className="task-copy">
                    <span className={`task-badge ${cls}`}>{label}</span>
                    <p className="panel-title" style={{ fontSize: 22 }}>{task.task_type_id} — {task.zone_id || 'Zone'}</p>
                    <p className="muted-text">
                      {task.machine_id} · {when(task.scheduled_start)} · {task.planned_quantity} {task.unit} · P{task.priority}
                    </p>
                  </div>
                </div>
                {!LOCKED.includes(task.status) && (
                  <div className="btn-row">
                    <button type="button" className="btn-small" onClick={() => onReassign(task)}>Reassign</button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div>
        <p className="section-label">Recent alerts</p>
        <div className="alert-list">
          {op.alerts.length === 0 && <p className="muted-text">No alerts recorded.</p>}
          {op.alerts.map((raw) => {
            const a = normalizeAlert(raw)
            return (
              <div key={a.id} className="alert-item">
                <span className={`status-dot ${a.level}`} />
                <div className="alert-copy">
                  <p className="alert-title">{a.title}</p>
                  <p className="alert-message">{a.message}</p>
                </div>
                <div className="alert-meta">
                  <span className="alert-rule">{a.rule}</span>
                  <p className="alert-time">{raw.machine_id} · {when(raw.ts)}</p>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Lessons</p>
        <div className="rule-list">
          {op.lessons.length === 0 && <p className="muted-text">No lessons assigned.</p>}
          {op.lessons.map((l) => (
            <div key={l.assignment_id} className="rule-item">
              <span className="rule-code">{l.format}</span>
              <span className="rule-text">
                {formatMessage(l.title_key)}
                {l.reason ? ` · ${l.reason}` : ''}
              </span>
              {l.completed_at ? (
                <span className="status-pill">Done{l.score != null ? ` · ${l.score}` : ''}</span>
              ) : (
                <span className="status-pill warning">Pending</span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Readiness history · advice only</p>
        <div className="rule-list">
          {op.readiness_history.length === 0 && <p className="muted-text">No checks yet.</p>}
          {op.readiness_history.map((r) => (
            <div key={r.check_id} className="rule-item">
              <span className="rule-code">{r.score}</span>
              <span className="rule-text">{when(r.ts)}</span>
              <span className={`status-pill ${READINESS_PILL[r.rating] ?? ''}`}>{r.rating}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function ManagerTeam({ operators, refreshKey, onAssign, onReassign }) {
  const [selected, setSelected] = useState(null)

  if (selected) {
    return (
      <Detail
        operatorId={selected}
        refreshKey={refreshKey}
        onBack={() => setSelected(null)}
        onAssign={onAssign}
        onReassign={onReassign}
      />
    )
  }

  const active = operators.filter((o) => o.shift?.status === 'ACTIVE').length
  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Site team</p>
        <p className="display-title large">Operators</p>
        <p className="muted-text">{active} on shift now · {operators.length} total</p>
      </div>

      <div className="alert-list">
        {operators.length === 0 && <p className="muted-text">No operators loaded.</p>}
        {operators.map((op) => (
          <OperatorRow key={op.operator_id} op={op} onOpen={setSelected} />
        ))}
      </div>
    </div>
  )
}
