export default function Safety({ state = {}, alerts = [] }) {
  const hasSeatbeltAlert = alerts.some(
    (a) => (a.rule === 'R01' || a.rule === 'R02' || a.rule === 'R07') && a.active !== false,
  )
  const hasProximityAlert = alerts.some(
    (a) => (a.rule === 'R12' || a.rule === 'R13') && a.active !== false,
  )
  const hasTiltAlert = alerts.some(
    (a) => (a.rule === 'R10' || a.rule === 'R11') && a.active !== false,
  )

  const checks = [
    {
      label: 'Seat Belt',
      value: hasSeatbeltAlert
        ? 'Unfastened'
        : state.sensor_health?.seatbelt === 'OK'
          ? 'Fastened'
          : 'Sensor Check',
      ok: !hasSeatbeltAlert && state.sensor_health?.seatbelt === 'OK',
    },
    {
      label: 'Hydraulics',
      value: state.exit_checks?.HYD_UNLOCKED === 'FAIL' ? 'Unlocked' : 'Locked',
      ok: state.exit_checks?.HYD_UNLOCKED !== 'FAIL',
    },
    {
      label: 'Bucket Height',
      value: state.exit_checks?.IMPLEMENT_RAISED === 'FAIL' ? 'Raised' : 'Lowered',
      ok: state.exit_checks?.IMPLEMENT_RAISED !== 'FAIL',
    },
    {
      label: 'Parking Brake',
      value: state.exit_checks?.PARK_BRAKE_OFF === 'PASS' ? 'Applied' : 'Released',
      ok: state.exit_checks?.PARK_BRAKE_OFF !== 'FAIL',
    },
    {
      label: 'Engine',
      value: state.exit_checks?.ENGINE_RUNNING === 'FAIL' ? 'Running' : 'Stopped',
      ok: state.exit_checks?.ENGINE_RUNNING !== 'FAIL',
    },
    {
      label: 'Slope / Stability',
      value: hasTiltAlert || state.exit_checks?.SLOPE === 'FAIL' ? 'Caution' : 'Stable',
      ok: !hasTiltAlert && state.exit_checks?.SLOPE !== 'FAIL',
    },
    {
      label: 'Proximity Zone',
      value: hasProximityAlert ? 'PERSON IN PERIMETER' : 'Clear',
      ok: !hasProximityAlert,
    },
  ]

  const activeRules = alerts.map((alert) => ({
    id: alert.rule,
    desc: alert.message || alert.title,
    crit: alert.level === 'critical' || alert.level === 'warning',
    level: alert.level,
  }))

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <p className="eyebrow">Machine Safety Telemetry</p>
          <span style={{ fontSize: '0.75rem', background: '#065f46', color: '#6ee7b7', padding: '0.2rem 0.5rem', borderRadius: '0.25rem', fontWeight: 700 }}>
            {state.class || 'ACTIVE'}
          </span>
        </div>
        <p className="display-title large">Safety Panel</p>
        <p className="muted-text">Real-time debounced sensor predicates · 1 Hz</p>
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
        <p className="eyebrow">Active Risk Rules ({activeRules.length})</p>
        {activeRules.length === 0 ? (
          <p style={{ fontSize: '0.85rem', color: '#10b981', fontWeight: 600, padding: '0.5rem 0' }}>
            ✓ No risk rules currently active. All parameters nominal.
          </p>
        ) : (
          <div className="rule-list">
            {activeRules.map((rule) => (
              <div key={rule.id} className="rule-item">
                <span className="rule-code">{rule.id}</span>
                <span className="rule-text">{rule.desc}</span>
                <span
                  style={{
                    fontSize: '0.7rem',
                    fontWeight: 800,
                    letterSpacing: '0.05em',
                    padding: '0.2rem 0.5rem',
                    borderRadius: '999px',
                    background: rule.crit ? '#fef2f2' : '#f0f9ff',
                    color: rule.crit ? '#dc2626' : '#0284c7',
                    border: `1px solid ${rule.crit ? '#fecaca' : '#bae6fd'}`,
                    textTransform: 'uppercase',
                    flexShrink: 0,
                  }}
                >
                  {rule.level?.toUpperCase() || 'INFO'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

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
        <strong>Safety Invariant I2:</strong> Advisory system only. The companion will never command or interlock machine controls. Manual operator authority is always absolute.
      </div>
    </div>
  )
}
