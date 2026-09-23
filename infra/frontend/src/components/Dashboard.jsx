export default function Dashboard({ showExitGuard, showHazardReport, showReadiness, alerts = [], state = {}, tasks = [] }) {
  const activeAlerts = alerts.slice(0, 3)
  const currentTask = tasks[0] || null
  const machineId = state.machine_id || 'EXC-001'
  const operatorName = state.operator_id || 'Operator'
  const siteLabel = state.site_id || 'Site A'
  const engineRunning = state.exit_checks?.ENGINE_RUNNING === 'FAIL' || state.class === 'PRODUCTIVE' || state.class === 'ATTENTION_REQUIRED'
  const readinessValue = state.readiness || 'GREEN'
  const exitGuardRequired = state.exit_state && state.exit_state !== 'SAFE'
  const progressValue = currentTask?.progress ?? 0

  const metrics = [
    { label: 'Readiness', value: readinessValue },
    { label: 'Exit', value: exitGuardRequired ? 'Check' : 'Clear' },
    { label: 'Alerts', value: `${activeAlerts.length}` },
  ]

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark hero-scene">
        <div className="machine-header">
          <div>
            <p className="eyebrow">Live scene</p>
            <p className="display-title">CAT 320 · {machineId}</p>
            <p className="muted-text">{siteLabel} · {operatorName}</p>
          </div>
          <div className="engine-status-wrap">
            <p className="eyebrow">Engine</p>
            <div className="engine-status">
              <span className={`status-dot ${engineRunning ? 'success' : 'warning'}`} />
              <span className="engine-running">{engineRunning ? 'Running' : 'Idle'}</span>
            </div>
          </div>
        </div>

        <div className="metric-grid">
          {metrics.map((metric) => (
            <div key={metric.label} className="metric-box">
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
            <p className="panel-title compact-title">{state.readiness || 'GREEN'}</p>
          </div>
          <button type="button" className="inline-readiness-button" onClick={showReadiness}>
            Check now
          </button>
        </div>
        <p className="muted-text">
          {state.readiness === 'RED'
            ? 'Take a break and retest before continuing.'
            : state.readiness === 'YELLOW'
              ? 'Continue with caution and monitor conditions.'
              : 'Fit for duty and ready to work.'}
        </p>
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
          </div>
        </div>

        <div className="progress-block">
          <div className="progress-header-row">
            <span>Progress</span>
            <span className="progress-badge">{progressValue}% complete</span>
          </div>

          <div className="progress-hint">
            <div className="progress-value">{progressValue}%</div>
            <div className="progress-copy">done</div>
          </div>
        </div>
      </div>

      <div className="quick-actions">
        <button type="button" className="action-card dark" onClick={showExitGuard}>
          <p className="eyebrow">Pre-exit</p>
          <p className="card-heading">Exit Guard</p>
          <div className="alert-inline">
            <span className="status-dot critical" />
            <span className="alert-inline-text">Action required</span>
          </div>
        </button>
      </div>

      <div>
        <p className="section-label">Active Alerts</p>
        <div className="alert-list">
          {activeAlerts.map((alert) => (
            <div key={alert.id} className="alert-item">
              <span className={`status-dot ${alert.level}`} />
              <div className="alert-copy">
                <p className="alert-title">{alert.title}</p>
                <p className="alert-message">{alert.message}</p>
              </div>
              <div className="alert-meta">
                <span className="alert-rule">{alert.rule}</span>
                <p className="alert-time">{alert.time}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
