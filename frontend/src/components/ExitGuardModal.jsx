import { useMemo } from 'react'

const CHECK_DEFINITIONS = [
  {
    key: 'IMPLEMENT_RAISED',
    passLabel: 'Bucket lowered to ground',
    failLabel: 'Lower bucket to ground',
  },
  {
    key: 'HYD_UNLOCKED',
    passLabel: 'Hydraulics locked',
    failLabel: 'Lock hydraulic lever',
  },
  {
    key: 'ENGINE_RUNNING',
    passLabel: 'Engine stopped',
    failLabel: 'Stop the engine',
  },
  {
    key: 'MOVING',
    passLabel: 'Machine stationary',
    failLabel: 'Bring machine to a stop',
  },
  {
    key: 'PARK_BRAKE_OFF',
    passLabel: 'Parking brake applied',
    failLabel: 'Apply parking brake',
  },
  {
    key: 'SLOPE',
    passLabel: 'Slope within safe limits',
    failLabel: 'Reposition machine onto flat ground',
  },
]

export default function ExitGuardModal({
  onClose,
  exitChecks = {},
  activeAlert = null,
  onAcknowledge,
}) {
  // Determine status of each check from live engine exit_checks
  const evaluatedChecks = useMemo(() => {
    return CHECK_DEFINITIONS.filter((def) => {
      // If park brake is not applicable (excavator), it will be omitted or UNKNOWN
      if (def.key === 'PARK_BRAKE_OFF' && !exitChecks[def.key]) {
        return false
      }
      return true
    }).map((def) => {
      const status = exitChecks[def.key]
      const isPass = status === 'PASS'
      return {
        key: def.key,
        label: isPass ? def.passLabel : def.failLabel,
        done: isPass,
        status: status || 'PENDING',
      }
    })
  }, [exitChecks])

  const allDone = evaluatedChecks.length > 0 && evaluatedChecks.every((check) => check.done)
  const remainingCount = evaluatedChecks.filter((item) => !item.done).length
  const isAcknowledged = Boolean(activeAlert?.acknowledgedAt)

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="exit-modal">
        <div className="exit-modal-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span className="proximity-badge" style={{ margin: 0 }}>⚠ R03</span>
              {activeAlert && (
                <span className={`status-pill ${isAcknowledged ? 'info' : 'danger'}`}>
                  {isAcknowledged ? 'Acknowledged' : 'CRITICAL'}
                </span>
              )}
            </div>
            <p className="modal-title" style={{ marginTop: '0.5rem' }}>Exit Guard</p>
            <p className="modal-subtitle">
              {allDone
                ? 'All checks satisfied. Safe to exit cab.'
                : 'Complete all steps before leaving the cab (HLD §4.4)'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close exit guard"
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
          {evaluatedChecks.map((check) => (
            <div
              key={check.key}
              className={`check-item ${check.done ? 'done' : 'fail'}`}
              style={{ cursor: 'default' }}
            >
              <span className={`check-bullet ${check.done ? 'done' : 'fail'}`}>
                {check.done ? '✓' : '✗'}
              </span>
              <div style={{ flex: 1 }}>
                <span className={`check-label ${check.done ? 'done' : ''}`}>
                  {check.label}
                </span>
                <span
                  style={{
                    display: 'block',
                    fontSize: '0.75rem',
                    color: check.done ? '#10b981' : '#f59e0b',
                    fontWeight: 600,
                  }}
                >
                  {check.status === 'PASS' ? 'SATISFIED' : 'ACTION REQUIRED'}
                </span>
              </div>
            </div>
          ))}
        </div>

        <div className="modal-actions" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '16px 20px 24px' }}>
          {activeAlert && !isAcknowledged && (
            <button
              type="button"
              onClick={onAcknowledge}
              className="secondary-action"
              style={{
                width: '100%',
                padding: '14px 18px',
                borderRadius: '14px',
                border: '1px solid #d4d4d8',
                background: '#f4f4f5',
                color: '#18181b',
                fontWeight: 800,
                fontSize: '15px',
                cursor: 'pointer',
                textAlign: 'center',
              }}
            >
              Acknowledge Alert (Silence Audio)
            </button>
          )}

          <button
            type="button"
            onClick={onClose}
            disabled={!allDone && !isAcknowledged}
            className={`primary-action ${allDone || isAcknowledged ? 'enabled' : 'disabled'}`}
            style={{
              width: '100%',
              padding: '16px 18px',
              borderRadius: '14px',
              fontSize: '17px',
              fontWeight: 900,
            }}
          >
            {allDone
              ? 'Safe to Exit'
              : `${remainingCount} check${remainingCount === 1 ? '' : 's'} remaining`}
          </button>
        </div>
      </div>
    </div>
  )
}
