import StatusDot from './StatusDot'

// Top strip: the highest active alert, or an honest "not live" state (I6) — never "all normal"
// when the data is stale or the stream is down.
export default function SafetyStrip({ alerts = [], stale, statusLabel }) {
  const top = alerts[0]

  if (top) {
    return (
      <div className="safety-strip">
        <StatusDot level={top.level} />
        <p className="safety-strip-title">{top.message || top.title}</p>
        <span className="safety-strip-rule">{top.rule}</span>
      </div>
    )
  }

  if (stale) {
    return (
      <div className="safety-strip" style={{ background: '#78350f', color: '#fef3c7' }}>
        <StatusDot level="warning" />
        <p className="safety-strip-title" style={{ color: '#fef3c7' }}>{statusLabel} — machine status unknown</p>
        <span className="safety-strip-rule" style={{ background: '#92400e', color: '#fef3c7' }}>UNKNOWN</span>
      </div>
    )
  }

  return (
    <div className="safety-strip" style={{ background: '#064e3b', color: '#ecfdf5' }}>
      <StatusDot level="success" />
      <p className="safety-strip-title" style={{ color: '#ecfdf5' }}>No active alerts</p>
      <span className="safety-strip-rule" style={{ background: '#047857', color: '#ecfdf5' }}>NOMINAL</span>
    </div>
  )
}
