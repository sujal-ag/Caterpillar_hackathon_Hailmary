const MODEL = { EXC: 'CAT 320', WL: 'CAT 950 GC' }
const EXIT_LABEL = { UNSAFE: 'Unsafe', SAFE_ENGINE_ON: 'Engine on', SAFE: 'Secured', NONE: 'In cab' }
const EXIT_TYPE = { UNSAFE: 'danger', SAFE_ENGINE_ON: 'warning', SAFE: 'success', NONE: 'success' }
const READINESS_TYPE = { RED: 'danger', YELLOW: 'warning', GREEN: 'success' }
const READINESS_COPY = {
  RED: 'Take a break and retest before continuing.',
  YELLOW: 'Continue with caution and monitor conditions.',
  GREEN: 'Fit for duty and ready to work.',
}

export default function Dashboard({
  machineId,
  operatorId,
  operatorName,
  showExitGuard,
  showHazardReport,
  showReadiness,
  onSelectAlert,
  alerts = [],
  state,
  telemetry,
  tasks = [],
  lastReadiness,
}) {
  const currentTask = tasks.find((t) => t.status === 'IN_PROGRESS') || tasks.find((t) => t.status !== 'DONE') || null
  const engine = telemetry?.engine?.state // null/undefined = unknown, never assumed
  const readiness = state?.readiness || lastReadiness?.rating || null
  const exitState = state?.exit_state
  const model = MODEL[machineId.replace(/\d+$/, '')] || ''
  // Live state is the source of truth for who's actually in the machine — a supervisor
  // viewing another operator's active shift must not see their own name shown as "the operator".
  const liveOperatorId = state?.operator_id
  const operatorLabel =
    !liveOperatorId || liveOperatorId === operatorId ? operatorName || liveOperatorId || 'No operator' : liveOperatorId

  const metrics = [
    { label: 'Readiness', value: readiness || '—', type: READINESS_TYPE[readiness] || 'warning' },
    { label: 'Exit', value: EXIT_LABEL[exitState] || 'Unknown', type: EXIT_TYPE[exitState] || 'warning' },
    { label: 'Alerts', value: `${alerts.length}`, type: alerts.length > 0 ? 'danger' : 'success' },
  ]

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark hero-scene">
        <div className="machine-header">
          <div>
            <p className="eyebrow">Live scene · {state?.class || 'UNKNOWN'}</p>
            <p className="display-title">{model} · {machineId}</p>
            <p className="muted-text">{operatorLabel} · {state?.shift_id || 'no active shift'}</p>
          </div>
          <div className="engine-status-wrap">
            <p className="eyebrow">Engine</p>
            <div className="engine-status">
              <span className={`status-dot ${engine === 'RUNNING' ? 'success' : engine ? 'info' : 'warning'}`} />
              <span className="engine-running">{engine ? engine[0] + engine.slice(1).toLowerCase() : 'Unknown'}</span>
            </div>
          </div>
        </div>

        <div className="metric-grid">
          {metrics.map((metric) => (
            <div key={metric.label} className={`metric-box metric-box-${metric.type}`}>
              <p className="metric-value">{metric.value}</p>
              <p className="metric-label">{metric.label}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="panel panel-light readiness-card compact-readiness-card">
        <div className="readiness-top-row">
          <div>
            <p className="eyebrow">Operator readiness</p>
            <p className="panel-title compact-title">
              {readiness || 'Not checked'}
              {lastReadiness?.score != null && ` · ${lastReadiness.score}`}
            </p>
          </div>
          <button type="button" className="inline-readiness-button" onClick={showReadiness}>
            Check now
          </button>
        </div>
        <p className="muted-text">{READINESS_COPY[readiness] || 'Take the 1-minute check before you start. Advice only.'}</p>
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Current Task</p>
        <div className="task-header-row">
          <div>
            <p className="panel-title">{currentTask ? currentTask.title : 'No active task'}</p>
            <p className="muted-text">{currentTask ? currentTask.location : 'Awaiting task assignment'}</p>
          </div>
          <div className="eta-box">
            <p className="eyebrow">ETA</p>
            <p className="eta-value">{currentTask?.eta || '—'}</p>
            {currentTask?.etaLabel && <p className="muted-text" style={{ fontSize: '0.7rem' }}>{currentTask.etaLabel}</p>}
          </div>
        </div>

        {currentTask && (
          <div className="progress-block">
            <div className="progress-header-row">
              <span>Progress</span>
              <span className="progress-badge">{currentTask.progress}% complete</span>
            </div>
          </div>
        )}
      </div>

      <div className="quick-actions">
        <button type="button" className="action-card dark" onClick={showExitGuard}>
          <p className="eyebrow">Pre-exit</p>
          <p className="card-heading">Exit Guard</p>
          <div className="alert-inline">
            <span className={`status-dot ${exitState === 'UNSAFE' ? 'critical' : 'success'}`} />
            <span className="alert-inline-text">{exitState === 'UNSAFE' ? 'Action required' : 'View checks'}</span>
          </div>
        </button>

        <button type="button" className="action-card light" onClick={showHazardReport}>
          <p className="eyebrow">Safety Event</p>
          <p className="card-heading">Report Hazard</p>
          <div className="alert-inline">
            <span className="status-dot warning" />
            <span className="alert-inline-text" style={{ color: '#d97706' }}>Log incident</span>
          </div>
        </button>
      </div>

      <div>
        <p className="section-label">Active Alerts</p>
        <div className="alert-list">
          {alerts.length === 0 && <p className="muted-text">No active alerts.</p>}
          {alerts.slice(0, 5).map((alert) => (
            <div
              key={alert.id}
              role="button"
              tabIndex={0}
              className="alert-item"
              style={{ cursor: 'pointer' }}
              onClick={() => onSelectAlert?.(alert)}
              title="Why this alert?"
            >
              <span className={`status-dot ${alert.level}`} />
              <div className="alert-copy">
                <p className="alert-title">{alert.title}</p>
                <p className="alert-message">{alert.message}</p>
              </div>
              <div className="alert-meta">
                <span className="alert-rule">{alert.rule}</span>
                <p className="alert-time">{alert.acknowledgedAt ? 'Acked' : alert.time}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
