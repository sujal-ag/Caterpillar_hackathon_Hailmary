import { useState } from 'react'
import { createIncident } from '../services/backendService'

// Values are the POST /incidents enums (edge/api/incidents.py, HLD §6.11).
const TYPES = [
  ['NEAR_MISS', 'Near miss'],
  ['INCIDENT', 'Incident'],
  ['INJURY', 'Injury'],
  ['EQUIPMENT_DAMAGE', 'Equipment damage'],
  ['HAZARD_OBSERVATION', 'Unsafe condition'],
]
const CATEGORIES = [
  ['PERSON_PROXIMITY', 'Person too close'],
  ['SLIP_FALL', 'Slip / fall'],
  ['ROLLOVER_RISK', 'Rollover risk'],
  ['POWER_LINE', 'Power line'],
  ['UTILITY_STRIKE', 'Utility strike'],
  ['OTHER', 'Other'],
]
const SEVERITY = [
  [1, 'Low'],
  [2, 'Medium'],
  [3, 'High'],
]
const PINS = [
  [null, 'No pin'],
  ['WORKER_ZONE', 'Worker zone'],
  ['SOFT_GROUND', 'Soft ground'],
  ['TRENCH', 'Trench'],
  ['DROP_OFF', 'Drop-off'],
  ['BURIED_UTILITY', 'Buried utility'],
  ['OTHER', 'Other hazard'],
]

function Chips({ options, value, onChange }) {
  return (
    <div className="chip-row">
      {options.map(([v, label]) => (
        <button key={String(v)} type="button" className={`chip ${value === v ? 'selected' : ''}`} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  )
}

export default function HazardReportModal({ machineId, onClose, onDone }) {
  const [type, setType] = useState('NEAR_MISS')
  const [category, setCategory] = useState('OTHER')
  const [severity, setSeverity] = useState(2)
  const [pin, setPin] = useState(null)
  const [transcript, setTranscript] = useState('')
  const [photos, setPhotos] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await createIncident(
        {
          type,
          category,
          severity_self: severity,
          transcript: transcript.trim() || null,
          machine_id: machineId,
          create_hazard: pin ? { type: pin } : null,
        },
        { photos },
      )
      onDone?.(
        res.hazard_error
          ? `Report saved. Hazard pin not placed: ${res.hazard_error}`
          : `Report saved${res.hazard ? ' and hazard pin placed at the machine' : ''}.`,
      )
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="exit-modal" style={{ maxHeight: '90vh', overflowY: 'auto' }}>
        <div className="exit-modal-header">
          <p className="modal-title">Report Hazard / Event</p>
          <p className="modal-subtitle">Position, machine state and recent alerts are attached automatically.</p>
        </div>

        <p className="field-label">What happened</p>
        <Chips options={TYPES} value={type} onChange={setType} />
        <p className="field-label">Category</p>
        <Chips options={CATEGORIES} value={category} onChange={setCategory} />
        <p className="field-label">Severity</p>
        <Chips options={SEVERITY} value={severity} onChange={setSeverity} />
        <p className="field-label">Warn others (hazard pin here)</p>
        <Chips options={PINS} value={pin} onChange={setPin} />

        <p className="field-label">Details (optional)</p>
        <div style={{ padding: '0 20px' }}>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={2}
            maxLength={5000}
            style={{ width: '100%', borderRadius: '0.5rem', padding: '0.5rem', border: '1px solid #d4d4d8' }}
          />
          <label className="field-label" style={{ display: 'block', padding: '0.5rem 0' }}>
            Photos (up to 3)
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              multiple
              onChange={(e) => setPhotos([...e.target.files].slice(0, 3))}
              style={{ display: 'block', marginTop: '0.35rem' }}
            />
          </label>
        </div>

        {error && <p style={{ color: '#dc2626', padding: '0 20px', fontSize: '0.85rem' }}>{error}</p>}

        <div className="modal-actions" style={{ display: 'flex', gap: '10px', padding: '14px 16px 20px' }}>
          <button type="button" onClick={onClose} className="btn-small" style={{ flex: 1, padding: '16px' }}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={busy} className="primary-action enabled" style={{ flex: 2 }}>
            {busy ? 'Sending…' : 'Submit report'}
          </button>
        </div>
      </div>
    </div>
  )
}
