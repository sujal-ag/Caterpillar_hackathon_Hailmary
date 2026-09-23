import { useState } from 'react'

export default function AuthScreen({ onUnlock }) {
  const [badgeId, setBadgeId] = useState('OP1001')
  const [pin, setPin] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!badgeId.trim() || !pin.trim()) return
    setIsSubmitting(true)
    try {
      await onUnlock?.({ badgeId: badgeId.trim(), pin: pin.trim() })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <p className="auth-kicker">Operator access</p>
        <h1 className="auth-title">Site access</h1>
        <p className="auth-subtitle">Sign in with your badge ID and PIN to continue to the live dashboard.</p>

        <div className="auth-pin-wrap">
          <label className="auth-field-label" htmlFor="badge-id">Badge ID</label>
          <input
            id="badge-id"
            className="auth-text-input"
            type="text"
            value={badgeId}
            onChange={(event) => setBadgeId(event.target.value)}
            placeholder="OP1001"
          />
        </div>

        <div className="auth-pin-wrap">
          <label className="auth-field-label" htmlFor="operator-pin">PIN</label>
          <input
            id="operator-pin"
            className="auth-text-input"
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={(event) => setPin(event.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="••••"
          />
        </div>

        <div className="auth-actions">
          <button type="button" className="auth-action secondary" onClick={() => { setPin(''); setBadgeId('OP1001') }}>
            Reset
          </button>
          <button type="button" className="auth-action primary" onClick={handleSubmit} disabled={isSubmitting}>
            {isSubmitting ? 'Signing in…' : 'Enter'}
          </button>
        </div>
      </div>
    </div>
  )
}
