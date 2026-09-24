import StatusDot from './StatusDot'

export default function SafetyStrip({ alerts = [] }) {
  const top = alerts && alerts.length > 0 ? alerts[0] : null

  if (!top) {
    return (
      <div className="safety-strip" style={{ background: '#064e3b', color: '#ecfdf5' }}>
        <StatusDot level="success" />
        <p className="safety-strip-title" style={{ color: '#ecfdf5' }}>All Systems Normal · Safe Operations</p>
        <span className="safety-strip-rule" style={{ background: '#047857', color: '#ecfdf5' }}>NOMINAL</span>
      </div>
    )
  }

  return (
    <div className="safety-strip">
      <StatusDot level={top.level} />
      <p className="safety-strip-title">{top.title}</p>
      <span className="safety-strip-rule">{top.rule}</span>
    </div>
  )
}
