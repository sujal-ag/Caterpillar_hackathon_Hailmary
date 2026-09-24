import { useState } from 'react'

const REPORT_TYPES = ['Incident', 'Near miss', 'Unsafe condition', 'Other']

export default function HazardReportModal({ onClose, onSubmit }) {
  const [selected, setSelected] = useState('Incident')

  return (
    <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="exit-modal">
        <div className="exit-modal-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ flex: 1 }}>
            <p className="modal-title">Report Hazard / Event</p>
            <p className="modal-subtitle">Choose the issue type and notify the team</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close hazard report"
            style={{
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              border: '1px solid rgba(255,255,255,0.25)',
              background: 'rgba(255,255,255,0.12)',
              color: '#fff',
              fontSize: '1.4rem',
              fontWeight: 800,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            ✕
          </button>
        </div>

        <div className="check-list">
          {REPORT_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => setSelected(type)}
              className={`check-item ${selected === type ? 'done' : ''}`}
            >
              <span className={`check-bullet ${selected === type ? 'done' : ''}`}>
                {selected === type ? '✓' : ''}
              </span>
              <span className={`check-label ${selected === type ? 'done' : ''}`}>
                {type}
              </span>
            </button>
          ))}
        </div>

        <div className="modal-actions" style={{ display: 'flex', gap: '10px', padding: '14px 16px 20px' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              flex: 1,
              padding: '16px 18px',
              borderRadius: '14px',
              border: '1px solid #d4d4d8',
              background: '#f4f4f5',
              color: '#3f3f46',
              fontSize: '17px',
              fontWeight: 900,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              await onSubmit?.({
                type: selected,
                description: `${selected} reported from operator companion`,
              })
              onClose()
            }}
            className="primary-action enabled"
            style={{ flex: 2 }}
          >
            Submit report
          </button>
        </div>
      </div>
    </div>
  )
}
