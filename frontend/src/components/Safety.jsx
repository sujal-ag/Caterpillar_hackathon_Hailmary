export default function Safety({ state = {}, alerts = [] }) {
  const checks = [
    { label: 'Seat Belt', value: state.sensor_health?.seatbelt === 'OK' ? 'Fastened' : 'Unknown', ok: state.sensor_health?.seatbelt === 'OK' },
    { label: 'Hydraulics', value: state.exit_checks?.HYD_UNLOCKED === 'FAIL' ? 'Unlocked' : 'Locked', ok: state.exit_checks?.HYD_UNLOCKED !== 'FAIL' },
    { label: 'Bucket Height', value: state.exit_checks?.IMPLEMENT_RAISED === 'FAIL' ? 'Raised' : 'Lowered', ok: state.exit_checks?.IMPLEMENT_RAISED !== 'FAIL' },
    { label: 'Parking Brake', value: state.exit_checks?.PARK_BRAKE_OFF === 'PASS' ? 'Applied' : 'Check', ok: state.exit_checks?.PARK_BRAKE_OFF === 'PASS' },
    { label: 'Engine', value: state.exit_checks?.ENGINE_RUNNING === 'FAIL' ? 'Running' : 'Stopped', ok: state.exit_checks?.ENGINE_RUNNING !== 'FAIL' },
    { label: 'Proximity Zone', value: 'Clear', ok: true },
  ]

  const activeRules = alerts.slice(0, 2).map((alert) => ({
    id: alert.rule,
    desc: alert.title,
    crit: alert.level === 'critical' || alert.level === 'warning',
  }))

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Live Status</p>
        <p className="display-title large">Safety Panel</p>
        <p className="muted-text">Updated every second</p>
      </div>

      <div className="status-list">
        {checks.map((item) => (
          <div key={item.label} className="status-row">
            <div className="status-copy">
              <p className="status-label">{item.label}</p>
              <p className={`status-value ${item.ok ? '' : 'danger'}`}>{item.value}</p>
            </div>
            <span className={`status-icon ${item.ok ? 'pass' : 'fail'}`}>
              {item.ok ? '✓' : '✗'}
            </span>
          </div>
        ))}
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Active Rules</p>
        <div className="rule-list">
          {activeRules.map((rule) => (
            <div key={rule.id} className="rule-item">
              <span className="rule-code">{rule.id}</span>
              <span className="rule-text">{rule.desc}</span>
              <span className={`rule-pill ${rule.crit ? 'danger' : 'info'}`} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
