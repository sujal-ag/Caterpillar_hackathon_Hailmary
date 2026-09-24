import { useState } from 'react'
import { normalizeTask, setTaskStatus, taskBadge } from '../services/backendService'

const LOCKED = ['IN_PROGRESS', 'DONE']
const hm = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

export default function ManagerTasks({ tasks, date, onDateChange, onAssign, onReassign, onChanged, onError }) {
  const [busy, setBusy] = useState(null)

  const backlog = async (task) => {
    setBusy(task.task_id)
    try {
      await setTaskStatus(task.task_id, 'BACKLOG')
      await onChanged()
    } catch (error) {
      onError(`Task update failed: ${error.message}`)
    } finally {
      setBusy(null)
    }
  }

  const count = (s) => tasks.filter((t) => t.status === s).length
  const metrics = [
    { label: 'Planned', value: tasks.length },
    { label: 'In progress', value: count('IN_PROGRESS') },
    { label: 'Done', value: count('DONE') },
  ]
  const byMachine = tasks.reduce((acc, t) => ({ ...acc, [t.machine_id]: [...(acc[t.machine_id] || []), t] }), {})

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Site plan</p>
        <p className="display-title large">Tasks</p>
        <div style={{ marginTop: 12 }}>
          <input className="auth-text-input" type="date" value={date} onChange={(e) => onDateChange(e.target.value)} />
        </div>
        <div className="metric-grid" style={{ marginTop: 16 }}>
          {metrics.map((m) => (
            <div key={m.label} className="metric-box">
              <p className="metric-value">{m.value}</p>
              <p className="metric-label">{m.label}</p>
            </div>
          ))}
        </div>
        <button type="button" className="primary-lesson-button" style={{ marginTop: 16 }} onClick={() => onAssign({})}>
          Assign new task
        </button>
      </div>

      {tasks.length === 0 && <p className="muted-text">No tasks planned for this day.</p>}

      {Object.entries(byMachine).map(([machineId, list]) => (
        <div key={machineId}>
          <p className="section-label">{machineId}</p>
          <div className="task-list">
            {list.map((task) => {
              const [cls, label] = taskBadge(task.status)
              const view = normalizeTask(task)
              return (
                <div key={task.task_id} className="panel panel-light task-card">
                  <div className="task-row-top">
                    <div className="task-copy">
                      <span className={`task-badge ${cls}`}>{label}</span>
                      <p className="panel-title">{view.title}</p>
                      <p className="muted-text">
                        {task.operator_name} · {hm(task.scheduled_start)}–{hm(task.scheduled_end)} · P{task.priority}
                      </p>
                      <p className="muted-text">
                        {task.actual_quantity ?? 0}/{task.planned_quantity} {task.unit}
                        {task.delay_reason ? ` · delayed: ${task.delay_reason.replace('_', ' ').toLowerCase()}` : ''}
                      </p>
                    </div>
                    {task.prediction && (
                      <div className="eta-box">
                        <p className="eyebrow">ETA</p>
                        <p className="eta-value">{view.eta}</p>
                        {view.etaLabel && <p className="muted-text" style={{ fontSize: '0.7rem' }}>{view.etaLabel}</p>}
                      </div>
                    )}
                  </div>
                  {!LOCKED.includes(task.status) && (
                    <div className="btn-row">
                      <button type="button" className="btn-small primary" onClick={() => onReassign(task)}>
                        Reassign
                      </button>
                      {task.status !== 'BACKLOG' && (
                        <button type="button" className="btn-small" disabled={busy === task.task_id} onClick={() => backlog(task)}>
                          Move to backlog
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
