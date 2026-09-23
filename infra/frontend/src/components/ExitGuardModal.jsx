import { useState } from 'react'
import { EXIT_CHECKS } from '../data/mockData'

export default function ExitGuardModal({ onClose }) {
  const [checks, setChecks] = useState(EXIT_CHECKS)
  const allDone = checks.every((check) => check.done)

  function toggleCheck(id) {
    setChecks((prev) =>
      prev.map((item) => (item.id === id ? { ...item, done: !item.done } : item)),
    )
  }

  return (
    <div className="modal-backdrop">
      <div className="exit-modal">
        <div className="exit-modal-header">
          <p className="modal-title">Exit Guard</p>
          <p className="modal-subtitle">Complete all checks before leaving</p>
        </div>

        <div className="check-list">
          {checks.map((check) => (
            <button
              key={check.id}
              type="button"
              onClick={() => toggleCheck(check.id)}
              className={`check-item ${check.done ? 'done' : ''}`}
            >
              <span className={`check-bullet ${check.done ? 'done' : ''}`}>
                {check.done ? '✓' : ''}
              </span>
              <span className={`check-label ${check.done ? 'done' : ''}`}>
                {check.label}
              </span>
            </button>
          ))}
        </div>

        <div className="modal-actions">
          <button
            type="button"
            onClick={onClose}
            disabled={!allDone}
            className={`primary-action ${allDone ? 'enabled' : 'disabled'}`}
          >
            {allDone ? 'Safe to Exit' : `${checks.filter((item) => !item.done).length} checks remaining`}
          </button>
        </div>
      </div>
    </div>
  )
}
