import { useEffect, useState } from 'react'
import { fetchAlertWhy, acknowledgeAlert } from '../services/backendService'

export default function AlertWhyModal({ alertId, onClose, onAlertAcked }) {
  const [whyData, setWhyData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [acking, setAcking] = useState(false)

  useEffect(() => {
    let isMounted = true
    const load = async () => {
      setLoading(true)
      const data = await fetchAlertWhy(alertId)
      if (isMounted) {
        setWhyData(data)
        setLoading(false)
      }
    }
    load()
    return () => { isMounted = false }
  }, [alertId])

  const handleAck = async () => {
    setAcking(true)
    await acknowledgeAlert(alertId)
    onAlertAcked?.(alertId)
    setAcking(false)
    onClose()
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="exit-modal" style={{ maxWidth: '480px' }}>
        <div className="exit-modal-header">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="proximity-badge" style={{ margin: 0 }}>
              {whyData?.rule_id || 'Alert'}
            </span>
            <span className={`status-pill ${whyData?.level?.toLowerCase() === 'critical' ? 'danger' : 'warning'}`}>
              {whyData?.level || 'Active'}
            </span>
          </div>
          <p className="modal-title" style={{ marginTop: '0.5rem' }}>
            {whyData?.name || 'Alert Explanation'}
          </p>
          <p className="modal-subtitle">
            Reference: {whyData?.hld_ref || 'HLD §4.4 Safety Rule'}
          </p>
        </div>

        {loading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#9ca3af' }}>
            Fetching engine telemetry & thresholds…
          </div>
        ) : whyData ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', margin: '1rem 0' }}>
            <div style={{ background: '#111827', padding: '0.75rem', borderRadius: '0.5rem' }}>
              <p className="eyebrow" style={{ color: '#9ca3af' }}>Live Trigger Inputs</p>
              <pre style={{ fontSize: '0.75rem', color: '#38bdf8', margin: 0, overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(whyData.inputs, null, 2)}
              </pre>
            </div>

            <div style={{ background: '#111827', padding: '0.75rem', borderRadius: '0.5rem' }}>
              <p className="eyebrow" style={{ color: '#9ca3af' }}>Configured Rule Thresholds</p>
              <pre style={{ fontSize: '0.75rem', color: '#a78bfa', margin: 0, overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(whyData.thresholds, null, 2)}
              </pre>
            </div>

            {whyData.acknowledged_at && (
              <p style={{ fontSize: '0.8rem', color: '#10b981', margin: 0 }}>
                ✓ Acknowledged at {new Date(whyData.acknowledged_at).toLocaleTimeString()}
              </p>
            )}
          </div>
        ) : (
          <div style={{ padding: '1rem', color: '#ef4444' }}>
            Unable to fetch alert diagnostic details.
          </div>
        )}

        <div className="modal-actions" style={{ display: 'flex', gap: '0.5rem' }}>
          {whyData?.active && !whyData?.acknowledged_at && (
            <button
              type="button"
              onClick={handleAck}
              disabled={acking}
              className="secondary-action"
              style={{
                flex: 1,
                padding: '0.75rem',
                borderRadius: '0.5rem',
                border: '1px solid #4b5563',
                background: '#374151',
                color: '#fff',
                cursor: 'pointer',
              }}
            >
              {acking ? 'Silencing…' : 'Acknowledge'}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="primary-action enabled"
            style={{ flex: 1 }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
