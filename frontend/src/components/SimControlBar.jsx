import { useEffect, useState } from 'react'
import { fetchSimScenarios, triggerSimScenario, stopSimScenario } from '../services/backendService'

// Names come from GET /sim/scenarios; labels are cosmetic.
const LABELS = {
  unsafe_exit: 'Unsafe Exit (R03 Hero)',
  safe_exit: 'Safe Exit',
  proximity_intrusion: 'Proximity Intrusion (R12/13)',
  dtc_1638_16: 'DTC Alert',
  drive_into_zone: 'Drive Into Hazard Zone',
  belt_bypass: 'Seatbelt Bypass (R07)',
  tilt_excursion: 'Slope/Tilt Caution',
  reset: 'Reset (Nominal)',
}
const SPEED = 1.0 // real time: debounce windows and the 14 s correction play out as on the machine

export default function SimControlBar({ machineId = 'EXC001', onScenarioTriggered }) {
  const [activeScenario, setActiveScenario] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [isExpanded, setIsExpanded] = useState(false)
  const [msg, setMsg] = useState('')
  const [scenarios, setScenarios] = useState([])

  useEffect(() => {
    if (!isExpanded) return
    fetchSimScenarios()
      .then((d) => setScenarios(d.scenarios.map((id) => ({ id, label: LABELS[id] || id, speed: SPEED }))))
      .catch((e) => setMsg(`Scenarios unavailable: ${e.message}`))
  }, [isExpanded])

  const handleRun = async (scenario) => {
    setIsRunning(true)
    setActiveScenario(scenario.id)
    setMsg(`Starting ${scenario.id}…`)
    try {
      await triggerSimScenario(scenario.id, scenario.speed, machineId)
      setMsg(`Running ${scenario.id} (${scenario.speed}×)`)
      onScenarioTriggered?.(scenario.id)
    } catch (err) {
      setMsg(`Error: ${err.message || 'Failed'}`)
    } finally {
      setIsRunning(false)
    }
  }

  const handleStop = async () => {
    setIsRunning(true)
    try {
      await stopSimScenario()
      setActiveScenario('')
      setMsg('Stopped.')
    } catch (err) {
      setMsg(`Stop failed: ${err.message}`)
    } finally {
      setIsRunning(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        bottom: isExpanded ? '4.5rem' : '4.5rem',
        right: '1rem',
        zIndex: 50,
        maxWidth: 'calc(100% - 2rem)',
      }}
    >
      {!isExpanded ? (
        <button
          type="button"
          onClick={() => setIsExpanded(true)}
          style={{
            background: '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: '2rem',
            padding: '0.5rem 1rem',
            fontSize: '0.8rem',
            fontWeight: 700,
            boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
          }}
        >
          <span>⚡</span>
          <span>Sim Controls</span>
          {activeScenario && <span style={{ background: '#1d4ed8', padding: '0.1rem 0.4rem', borderRadius: '1rem' }}>{activeScenario}</span>}
        </button>
      ) : (
        <div
          style={{
            background: '#1f2937',
            border: '1px solid #374151',
            borderRadius: '0.75rem',
            padding: '0.85rem',
            boxShadow: '0 8px 24px rgba(0,0,0,0.6)',
            width: '320px',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#f9fafb', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Simulator Controls ({machineId})
            </span>
            <button
              type="button"
              onClick={() => setIsExpanded(false)}
              style={{ background: 'transparent', border: 'none', color: '#9ca3af', cursor: 'pointer', fontSize: '1rem' }}
            >
              ✕
            </button>
          </div>

          {msg && (
            <p style={{ fontSize: '0.75rem', color: '#38bdf8', marginBottom: '0.5rem', fontWeight: 600 }}>
              {msg}
            </p>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '0.35rem', maxHeight: '200px', overflowY: 'auto' }}>
            {scenarios.map((sc) => (
              <button
                key={sc.id}
                type="button"
                onClick={() => handleRun(sc)}
                disabled={isRunning}
                style={{
                  background: activeScenario === sc.id ? '#1e40af' : '#374151',
                  color: '#f9fafb',
                  border: '1px solid #4b5563',
                  borderRadius: '0.375rem',
                  padding: '0.4rem 0.6rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  textAlign: 'left',
                  cursor: isRunning ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <span>{sc.label}</span>
                <span style={{ fontSize: '0.65rem', color: '#9ca3af' }}>{sc.speed}×</span>
              </button>
            ))}
          </div>

          <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.5rem' }}>
            <button
              type="button"
              onClick={handleStop}
              disabled={isRunning || !activeScenario}
              style={{
                flex: 1,
                background: '#ef4444',
                color: '#fff',
                border: 'none',
                borderRadius: '0.375rem',
                padding: '0.4rem',
                fontSize: '0.75rem',
                fontWeight: 700,
                cursor: isRunning || !activeScenario ? 'not-allowed' : 'pointer',
                opacity: isRunning || !activeScenario ? 0.5 : 1,
              }}
            >
              Stop Scenario
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
