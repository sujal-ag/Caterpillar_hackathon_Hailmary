// Exit Guard (HLD hero feature). Ticks come live from state.exit_checks; UNKNOWN counts as
// FAIL (I1). While the R03 alert is active the overlay can be acknowledged but not dismissed:
// it closes when the alert is CLEARED (contracts/ws.md).
const CHECKS = [
  ['IMPLEMENT_RAISED', 'Bucket lowered to ground', 'Lower bucket to ground'],
  ['HYD_UNLOCKED', 'Hydraulics locked', 'Lock hydraulic lever'],
  ['ENGINE_RUNNING', 'Engine stopped', 'Stop the engine'],
  ['MOVING', 'Machine stationary', 'Bring machine to a stop'],
  ['PARK_BRAKE_OFF', 'Parking brake applied', 'Apply parking brake'],
  ['SLOPE', 'Slope within safe limits', 'Reposition onto flat ground'],
]

export default function ExitGuardModal({ onClose, exitChecks = {}, activeAlert, stale, onAcknowledge }) {
  const checks = CHECKS.filter(([key]) => key in exitChecks).map(([key, pass, fail]) => ({
    key,
    status: exitChecks[key],
    done: exitChecks[key] === 'PASS' && !stale,
    label: exitChecks[key] === 'PASS' ? pass : fail,
  }))
  const remaining = checks.filter((c) => !c.done).length
  const acked = Boolean(activeAlert?.acknowledgedAt)
  const locked = Boolean(activeAlert) // stays until CLEARED

  return (
    <div className="modal-backdrop" role="alertdialog" aria-modal="true">
      <div className="exit-modal">
        <div className="exit-modal-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span className="proximity-badge" style={{ margin: 0 }}>⚠ R03</span>
              {activeAlert && <span className={`status-pill ${acked ? 'info' : 'danger'}`}>{acked ? 'Acknowledged' : 'CRITICAL'}</span>}
              {stale && <span className="status-pill warning">DATA NOT LIVE</span>}
            </div>
            <p className="modal-title" style={{ marginTop: '0.5rem' }}>Exit Guard</p>
            <p className="modal-subtitle">
              {checks.length === 0
                ? 'No exit in progress. The checklist appears when you start to leave the cab.'
                : remaining === 0
                  ? 'All checks satisfied. Safe to exit cab.'
                  : 'Complete every step before leaving the cab.'}
            </p>
          </div>
        </div>

        <div className="check-list">
          {checks.map((check) => (
            <div key={check.key} className={`check-item ${check.done ? 'done' : 'fail'}`} style={{ cursor: 'default' }}>
              <span className={`check-bullet ${check.done ? 'done' : 'fail'}`}>{check.done ? '✓' : '✗'}</span>
              <div style={{ flex: 1 }}>
                <span className={`check-label ${check.done ? 'done' : ''}`}>{check.label}</span>
                <span style={{ display: 'block', fontSize: '0.75rem', color: check.done ? '#10b981' : '#f59e0b', fontWeight: 600 }}>
                  {check.done ? 'SATISFIED' : check.status === 'UNKNOWN' ? 'SENSOR UNKNOWN — CHECK MANUALLY' : 'ACTION REQUIRED'}
                </span>
              </div>
            </div>
          ))}
        </div>

        <div className="modal-actions" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '16px 20px 24px' }}>
          {activeAlert && !acked && (
            <button type="button" onClick={onAcknowledge} className="btn-small" style={{ padding: '14px 18px', fontSize: '15px' }}>
              Acknowledge
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            disabled={locked}
            className={`primary-action ${locked ? 'disabled' : 'enabled'}`}
            style={{ width: '100%', padding: '16px 18px', borderRadius: '14px', fontSize: '17px', fontWeight: 900 }}
          >
            {locked
              ? `${remaining} check${remaining === 1 ? '' : 's'} remaining`
              : 'Close'}
          </button>
        </div>
      </div>
    </div>
  )
}
