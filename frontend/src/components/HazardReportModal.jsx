import { useState } from 'react'

const REPORT_TYPES = ['Incident', 'Near miss', 'Unsafe condition', 'Other']

export default function HazardReportModal({ onClose, onSubmit }) {
  const [selected, setSelected] = useState('Incident')

  return (
    <div className="modal-backdrop">
      <div className="exit-modal">
        <div className="exit-modal-header">
          <p className="modal-title">Report Hazard / Event</p>
          <p className="modal-subtitle">Choose the issue type and notify the team</p>
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

        <div className="modal-actions">
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
          >
            Submit report
          </button>
        </div>
      </div>
    </div>
  )
}
