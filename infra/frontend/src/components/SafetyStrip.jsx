import StatusDot from './StatusDot'

export default function SafetyStrip({ alerts }) {
  const top = alerts[0]

  return (
    <div className="safety-strip">
      <StatusDot level={top.level} />
      <p className="safety-strip-title">{top.title}</p>
      <span className="safety-strip-rule">{top.rule}</span>
    </div>
  )
}
