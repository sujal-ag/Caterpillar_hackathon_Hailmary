import { useState } from 'react'
import { triggerSimScenario, stopSimScenario } from '../services/backendService'

const SCENARIOS = [
  { id: 'unsafe_exit', label: 'Unsafe Exit (R03 Hero)', speed: 4.0 },
  { id: 'safe_exit', label: 'Safe Exit', speed: 2.0 },
  { id: 'proximity_intrusion', label: 'Proximity Intrusion (R12/13)', speed: 2.0 },
  { id: 'dtc_1638_16', label: 'DTC Overheat Alert', speed: 2.0 },
  { id: 'drive_into_zone', label: 'Drive Into Hazard Zone', speed: 2.0 },
  { id: 'belt_bypass', label: 'Seatbelt Bypass (R07)', speed: 2.0 },
  { id: 'tilt_excursion', label: 'Slope/Tilt Caution', speed: 2.0 },
  { id: 'reset', label: 'Reset (Nominal)', speed: 1.0 },
]

export default function SimControlBar({ machineId = 'EXC001', onScenarioTriggered }) {
  const [activeScenario, setActiveScenario] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [isExpanded, setIsExpanded] = useState(false)
  const [msg, setMsg] = useState('')

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
    } catch {
      setMsg('Failed to stop scenario.')
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
            {SCENARIOS.map((sc) => (
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
