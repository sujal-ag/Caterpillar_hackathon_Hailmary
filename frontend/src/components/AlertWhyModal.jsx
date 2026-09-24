import { useEffect, useState } from 'react'
import { fetchAlertWhy } from '../services/backendService'
import { formatMessage } from '../data/i18n'

export default function AlertWhyModal({ alertId, onClose, onAck }) {
  const [why, setWhy] = useState(null)
  const [error, setError] = useState('')
  const [acking, setAcking] = useState(false)

  useEffect(() => {
    let alive = true
    fetchAlertWhy(alertId)
      .then((data) => alive && setWhy(data))
      .catch((e) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [alertId])

  const handleAck = async () => {
    setAcking(true)
    await onAck?.(alertId)
    setAcking(false)
    onClose()
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="exit-modal" style={{ maxWidth: '480px' }}>
        <div className="exit-modal-header">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="proximity-badge" style={{ margin: 0 }}>{why?.rule_id || 'Alert'}</span>
            {why && <span className={`status-pill ${why.level === 'CRITICAL' ? 'danger' : 'warning'}`}>{why.level}</span>}
          </div>
          <p className="modal-title" style={{ marginTop: '0.5rem' }}>{why ? formatMessage(why.message_key, why.slots) : 'Why this alert?'}</p>
          {why && <p className="modal-subtitle">{why.name} · HLD {why.hld_ref}</p>}
        </div>

        {error ? (
          <div style={{ padding: '1rem', color: '#ef4444' }}>Unable to load the explanation: {error}</div>
        ) : !why ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#9ca3af' }}>Loading…</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', margin: '1rem 0' }}>
            <div style={{ background: '#111827', padding: '0.75rem', borderRadius: '0.5rem' }}>
              <p className="eyebrow" style={{ color: '#9ca3af' }}>Inputs when it fired</p>
              <pre style={{ fontSize: '0.75rem', color: '#38bdf8', margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(why.inputs, null, 2)}</pre>
            </div>
            <div style={{ background: '#111827', padding: '0.75rem', borderRadius: '0.5rem' }}>
              <p className="eyebrow" style={{ color: '#9ca3af' }}>Thresholds</p>
              <pre style={{ fontSize: '0.75rem', color: '#a78bfa', margin: 0, whiteSpace: 'pre-wrap' }}>{JSON.stringify(why.thresholds, null, 2)}</pre>
            </div>
            {why.acknowledged_at && (
              <p style={{ fontSize: '0.8rem', color: '#10b981', margin: 0 }}>
                ✓ Acknowledged at {new Date(why.acknowledged_at).toLocaleTimeString()}
              </p>
            )}
          </div>
        )}

        <div className="modal-actions" style={{ display: 'flex', gap: '0.5rem' }}>
          {why?.active && !why.acknowledged_at && (
            <button type="button" onClick={handleAck} disabled={acking} className="btn-small" style={{ flex: 1 }}>
              {acking ? 'Acknowledging…' : 'Acknowledge'}
            </button>
          )}
          <button type="button" onClick={onClose} className="primary-action enabled" style={{ flex: 1 }}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
