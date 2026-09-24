import { useState } from 'react'
import { setTaskStatus } from '../services/backendService'

const BADGE = { IN_PROGRESS: ['in-progress', 'In Progress'], DONE: ['done', 'Complete'], PAUSED: ['pending', 'Paused'] }
const NEXT = {
  SCHEDULED: [['IN_PROGRESS', 'Start']],
  BACKLOG: [['IN_PROGRESS', 'Start']],
  PAUSED: [['IN_PROGRESS', 'Resume'], ['DONE', 'Done']],
  IN_PROGRESS: [['PAUSED', 'Pause'], ['DONE', 'Done']],
}

export default function Tasks({ tasks = [], onChanged, onError }) {
  const [busy, setBusy] = useState(null)

  const change = async (task, status) => {
    setBusy(task.id)
    try {
      await setTaskStatus(task.id, status)
      await onChanged?.()
    } catch (error) {
      onError?.(`Task update failed: ${error.message}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Current shift</p>
        <p className="display-title large">Tasks</p>
      </div>

      {tasks.length === 0 && <p className="muted-text">No tasks for this shift.</p>}

      <div className="task-list">
        {tasks.map((task) => {
          const [cls, label] = BADGE[task.status] || ['pending', 'Scheduled']
          return (
            <div key={task.id} className="panel panel-light task-card">
              <div className="task-row-top">
                <div className="task-copy">
                  <span className={`task-badge ${cls}`}>{label}</span>
                  <p className="panel-title">{task.title}</p>
                  <p className="muted-text">{task.location} · from {task.scheduledStart}</p>
                </div>

                {task.prediction && (
                  <div className="eta-box">
                    <p className="eyebrow">ETA</p>
                    <p className="eta-value">{task.eta}</p>
                    {task.etaLabel && <p className="muted-text" style={{ fontSize: '0.7rem' }}>{task.etaLabel}</p>}
                  </div>
                )}
              </div>

              {task.prediction?.drivers?.length > 0 && (
                <p className="muted-text" style={{ fontSize: '0.75rem' }}>
                  Drivers: {task.prediction.drivers.map((d) => d.feature).join(', ')}
                </p>
              )}

              <div className="btn-row">
                {(NEXT[task.status] || []).map(([status, text]) => (
                  <button
                    key={status}
                    type="button"
                    className={`btn-small ${status === 'IN_PROGRESS' ? 'primary' : ''}`}
                    disabled={busy === task.id}
                    onClick={() => change(task, status)}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
