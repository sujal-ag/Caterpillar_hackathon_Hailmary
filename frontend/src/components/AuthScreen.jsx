import { useState } from 'react'

export default function AuthScreen({ onUnlock }) {
  const [badgeId, setBadgeId] = useState('EMP1001')
  const [pin, setPin] = useState('1234')
  const [machineId, setMachineId] = useState('EXC001')
  const [errorMsg, setErrorMsg] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!badgeId.trim() || !pin.trim()) return
    setIsSubmitting(true)
    setErrorMsg('')
    try {
      await onUnlock?.({ badgeId: badgeId.trim(), pin: pin.trim(), machineId: machineId.trim() })
    } catch (err) {
      setErrorMsg(err.message || 'Authentication failed. Please verify credentials.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const setPreset = (presetBadge, presetPin, presetMachine = 'EXC001') => {
    setBadgeId(presetBadge)
    setPin(presetPin)
    setMachineId(presetMachine)
    setErrorMsg('')
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', marginBottom: '12px' }}>
          <img src="/catops-excavator.svg" alt="CatOps" style={{ width: '56px', height: '56px', borderRadius: '12px' }} />
          <div>
            <p className="auth-kicker">Caterpillar Field Companion</p>
            <h1 className="auth-title" style={{ marginTop: '2px', fontSize: '28px' }}>CatOps Access</h1>
          </div>
        </div>
        <p className="auth-subtitle">Sign in with your employee badge code &amp; PIN to bind machine telemetry.</p>

        {errorMsg && (
          <div
            style={{
              padding: '0.75rem',
              marginBottom: '1rem',
              borderRadius: '0.375rem',
              background: '#7f1d1d',
              color: '#fecaca',
              fontSize: '0.875rem',
            }}
          >
            {errorMsg}
          </div>
        )}

        <div className="auth-pin-wrap">
          <label className="auth-field-label" htmlFor="machine-id">Machine</label>
          <select
            id="machine-id"
            className="auth-text-input"
            value={machineId}
            onChange={(e) => setMachineId(e.target.value)}
            style={{ background: '#111827', color: '#fff' }}
          >
            <option value="EXC001">CAT 320 Excavator (EXC001)</option>
            <option value="WL001">CAT 950 GC Wheel Loader (WL001)</option>
            <option value="EXC002">CAT 320 Excavator (EXC002)</option>
          </select>
        </div>

        <div className="auth-pin-wrap">
          <label className="auth-field-label" htmlFor="badge-id">Badge ID / Employee Code</label>
          <input
            id="badge-id"
            className="auth-text-input"
            type="text"
            value={badgeId}
            onChange={(event) => setBadgeId(event.target.value)}
            placeholder="EMP1001"
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

        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
          <button
            type="button"
            className="auth-action secondary"
            style={{ flex: 1, padding: '0.5rem', fontSize: '0.75rem' }}
            onClick={() => setPreset('EMP1001', '1234', 'EXC001')}
          >
            Demo Operator (EMP1001)
          </button>
          <button
            type="button"
            className="auth-action secondary"
            style={{ flex: 1, padding: '0.5rem', fontSize: '0.75rem' }}
            onClick={() => setPreset('SUP001', '9999', 'EXC001')}
          >
            Supervisor / Admin (SUP001)
          </button>
        </div>

        <div className="auth-actions">
          <button
            type="button"
            className="auth-action secondary"
            onClick={() => { setPin(''); setBadgeId('EMP1001') }}
          >
            Reset
          </button>
          <button
            type="button"
            className="auth-action primary"
            onClick={handleSubmit}
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Signing in…' : 'Enter Cab'}
          </button>
        </div>
      </div>
    </div>
  )
}
