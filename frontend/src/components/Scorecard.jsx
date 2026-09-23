import { useEffect, useState } from 'react'
import { fetchScorecard } from '../services/backendService'

const defaultMetrics = [
  { label: 'Cycles / Hour', value: '4.2', delta: '+0.3 vs avg', up: true },
  { label: 'Idle Fuel Share', value: '18%', delta: '−4% vs avg', up: true },
  { label: 'Unsafe Exits', value: '1', delta: 'per 100 exits', up: false },
  { label: 'Alerts / Hour', value: '3.1', delta: 'Target < 4', up: true },
]

export default function Scorecard({ state = {} }) {
  const [metrics, setMetrics] = useState(defaultMetrics)
  const [scoreData, setScoreData] = useState(null)
  const readinessLabel = state.readiness || scoreData?.rating || 'GREEN'
  const readinessText = readinessLabel === 'GREEN' ? 'Green — Fit for Duty' : readinessLabel === 'YELLOW' ? 'Yellow — Monitor' : 'Red — Stop and reassess'

  useEffect(() => {
    let isMounted = true

    const load = async () => {
      const operatorId = window.localStorage.getItem('cat-operator-id') || 'OP1001'
      const result = await fetchScorecard(operatorId, 'day')
      if (!isMounted) return

      setScoreData(result)

      if (Array.isArray(result?.metrics) && result.metrics.length) {
        setMetrics(result.metrics)
      }
    }

    load()
    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">{window.localStorage.getItem('cat-operator-id') || 'Ravi M.'} · Today</p>
        <p className="display-title large">Scorecard</p>
      </div>

      <div className="score-grid">
        {metrics.map((item) => (
          <div key={item.label} className="panel panel-light metric-card">
            <p className="eyebrow">{item.label}</p>
            <p className="score-value">{item.value}</p>
            <p className={`score-delta ${item.up ? 'up' : 'down'}`}>{item.delta}</p>
          </div>
        ))}
      </div>

      <div className="panel panel-light readiness-card">
        <p className="eyebrow">Shift Readiness</p>
        <div className="readiness-row">
          <div className="readiness-badge">
            <span>✓</span>
          </div>
          <div>
            <p className="panel-title">{readinessText}</p>
            <p className="muted-text">Readiness flag: {readinessLabel} · Sensor health: {state.sensor_health?.data || 'OK'}</p>
          </div>
        </div>
      </div>

      <div className="panel panel-light">
        <div className="anomaly-header">
          <p className="eyebrow">Anomaly Score</p>
          <span className="anomaly-value">{scoreData?.anomaly ?? '0.68'}</span>
        </div>
        <div className="progress-bar anomaly-bar">
          <div className="progress-fill" style={{ width: `${scoreData?.anomaly_pct ?? 68}%` }} />
        </div>
        <p className="anomaly-copy">Top factors: idle ratio (+2σ), fuel per productive hour (+1.4σ)</p>
      </div>
    </div>
  )
}
