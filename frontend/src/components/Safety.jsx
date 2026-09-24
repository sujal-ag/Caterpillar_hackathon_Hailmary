// Live values come from telemetry.v1; a missing/stale value shows "Unknown", never OK (I1).
const row = (label, value, ok) => ({ label, value: value ?? 'Unknown', ok: value == null ? null : ok })

export default function Safety({ state, telemetry, alerts = [], onSelectAlert }) {
  const t = telemetry || {}
  const has = (...rules) => alerts.some((a) => rules.includes(a.rule))
  const height = t.implement?.bucket_height_m
  const dist = t.proximity?.min_dist_m
  const isWheelLoader = state?.machine_id?.startsWith('WL')

  const checks = [
    row('Seat Belt', t.cab?.seatbelt, t.cab?.seatbelt !== 'FAULT' && !has('R01', 'R02', 'R07')),
    row('Hydraulics', t.hyd?.lockout, !has('R03')),
    row('Bucket Height', height != null ? `${height.toFixed(1)} m` : null, !has('R03')),
    row('Engine', t.engine?.state, true),
    isWheelLoader && row('Parking Brake', t.motion?.parking_brake, t.motion?.parking_brake === 'ON' || !has('R03')),
    row(
      'Slope / Stability',
      t.motion?.pitch_deg != null ? `pitch ${t.motion.pitch_deg.toFixed(1)}° · roll ${(t.motion.roll_deg ?? 0).toFixed(1)}°` : null,
      !has('R10', 'R11'),
    ),
    row(
      'Proximity',
      state?.sensor_health?.proximity !== 'OK' ? null : dist != null ? `${dist} m ${t.proximity.sector || ''}` : 'Clear',
      !has('R12', 'R13'),
    ),
  ].filter(Boolean)

  const health = state?.sensor_health || {}

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <p className="eyebrow">Machine Safety Telemetry</p>
          <span className={`status-pill ${state?.class === 'UNSAFE' ? 'danger' : state?.class === 'ATTENTION' ? 'warning' : ''}`}>
            {state?.class || 'UNKNOWN'}
          </span>
        </div>
        <p className="display-title large">Safety Panel</p>
        <p className="muted-text">
          Sensors: {Object.entries(health).map(([k, v]) => `${k} ${v}`).join(' · ') || 'unknown'}
        </p>
      </div>

      <div className="status-list">
        {checks.map((item) => (
          <div key={item.label} className="status-row">
            <div className="status-copy">
              <p className="status-label">{item.label}</p>
              <p className={`status-value ${item.ok === false ? 'danger' : ''}`}>{item.value}</p>
            </div>
            <span className={`status-icon ${item.ok ? 'pass' : 'fail'}`}>
              {item.ok == null ? '?' : item.ok ? '✓' : '✗'}
            </span>
          </div>
        ))}
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Active Risk Rules ({alerts.length})</p>
        {alerts.length === 0 ? (
          <p style={{ fontSize: '0.85rem', color: '#10b981', fontWeight: 600, padding: '0.5rem 0' }}>
            ✓ No risk rules currently active.
          </p>
        ) : (
          <div className="rule-list">
            {alerts.map((alert) => (
              <div key={alert.id} className="rule-item" onClick={() => onSelectAlert?.(alert)} style={{ cursor: 'pointer' }}>
                <span className="rule-code">{alert.rule}</span>
                <span className="rule-text">{alert.message}</span>
                <span className={`status-pill ${alert.level === 'critical' ? 'danger' : alert.level === 'info' ? 'info' : 'warning'}`}>
                  {alert.level.toUpperCase()}
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
        Advisory only. The companion never commands or interlocks the machine.
      </div>
    </div>
  )
}
