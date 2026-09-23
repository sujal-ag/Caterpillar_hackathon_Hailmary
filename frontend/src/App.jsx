import { useEffect, useRef, useState } from 'react'
import SafetyStrip from './components/SafetyStrip'
import Dashboard from './components/Dashboard'
import Safety from './components/Safety'
import Tasks from './components/Tasks'
import Training from './components/Training'
import Scorecard from './components/Scorecard'
import SiteMap from './components/SiteMap'
import AuthScreen from './components/AuthScreen'
import ExitGuardModal from './components/ExitGuardModal'
import HazardReportModal from './components/HazardReportModal'
import ReadinessModal from './components/ReadinessModal'
import AlertWhyModal from './components/AlertWhyModal'
import SimControlBar from './components/SimControlBar'
import { ALERTS, NAV, TASKS as FALLBACK_TASKS } from './data/mockData'
import { BACKEND_STATE } from './data/backendContract'
import {
  createIncident,
  fetchOperatorData,
  getAuthToken,
  getStoredMachineId,
  loginWithBadgeAndPin,
  normalizeAlert,
  submitReadiness,
  acknowledgeAlert,
} from './services/backendService'
import { LiveWebSocketClient } from './services/liveSocket'
import { formatMessage } from './data/i18n'

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [exitGuardOpen, setExitGuardOpen] = useState(false)
  const [hazardReportOpen, setHazardReportOpen] = useState(false)
  const [readinessOpen, setReadinessOpen] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem('cat-readiness-onboarded') !== 'true'
  })
  const [selectedAlertForWhy, setSelectedAlertForWhy] = useState(null)
  const [machineId, setMachineId] = useState(() => getStoredMachineId())
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getAuthToken()))
  const [wsStatus, setWsStatus] = useState('disconnected') // 'connecting' | 'connected' | 'disconnected'
  const [proximityAlertOpen, setProximityAlertOpen] = useState(false)
  const [proximityDetails, setProximityDetails] = useState({
    distance_m: 2.9,
    sector: 'behind you, left side',
    action: 'Stop swinging now',
  })

  const [readinessState, setReadinessState] = useState(() => {
    if (typeof window === 'undefined') return { score: 88, rating: 'GREEN', reasons: [] }
    const saved = window.localStorage.getItem('cat-readiness-state')
    if (!saved) return { score: 88, rating: 'GREEN', reasons: [] }
    try {
      return JSON.parse(saved)
    } catch {
      return { score: 88, rating: 'GREEN', reasons: [] }
    }
  })

  const [liveData, setLiveData] = useState({
    state: BACKEND_STATE,
    alerts: ALERTS,
    tasks: FALLBACK_TASKS,
    nudge: null,
    isLive: false,
  })

  const lastCriticalAlertRef = useRef('')
  const audioContextRef = useRef(null)
  const wsClientRef = useRef(null)

  const playAlertTone = () => {
    if (typeof window === 'undefined') return
    const AudioContextClass = window.AudioContext || window.webkitAudioContext
    if (!AudioContextClass) return

    try {
      const context = audioContextRef.current ?? new AudioContextClass()
      audioContextRef.current = context

      if (context.state === 'suspended') {
        context.resume()
      }

      const oscillator = context.createOscillator()
      const gainNode = context.createGain()

      oscillator.type = 'sawtooth'
      oscillator.frequency.setValueAtTime(820, context.currentTime)
      oscillator.frequency.exponentialRampToValueAtTime(420, context.currentTime + 0.26)

      gainNode.gain.setValueAtTime(0.0001, context.currentTime)
      gainNode.gain.exponentialRampToValueAtTime(0.08, context.currentTime + 0.02)
      gainNode.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.42)

      oscillator.connect(gainNode)
      gainNode.connect(context.destination)
      oscillator.start()
      oscillator.stop(context.currentTime + 0.44)
    } catch (error) {
      console.warn('Audio alert unavailable on this device:', error)
    }
  }

  const speakNudge = (text) => {
    if (typeof window === 'undefined' || !window.speechSynthesis) return
    try {
      window.speechSynthesis.cancel() // Cancel prior speech to stay low latency
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.rate = 1.05
      utterance.pitch = 0.95
      window.speechSynthesis.speak(utterance)
    } catch (error) {
      console.warn('Speech synthesis unavailable:', error)
    }
  }

  // 1. Initial REST fetch + Polling fallback
  useEffect(() => {
    if (!isAuthenticated) return undefined

    let isMounted = true

    const load = async () => {
      const next = await fetchOperatorData(machineId)
      if (isMounted) {
        setLiveData((prev) => ({
          ...prev,
          state: next.state,
          alerts: next.alerts,
          tasks: next.tasks.length ? next.tasks : prev.tasks,
          isLive: next.isLive,
        }))
      }
    }

    load()
    const interval = window.setInterval(load, 15000)

    return () => {
      isMounted = false
      window.clearInterval(interval)
    }
  }, [isAuthenticated, machineId])

  // 2. Real-Time WebSocket streaming (/ws/live)
  useEffect(() => {
    if (!isAuthenticated) return undefined

    const client = new LiveWebSocketClient({
      machineId,
      onStatusChange: (status) => {
        setWsStatus(status)
      },
      onSnapshot: (snapshot) => {
        if (snapshot?.state) {
          setLiveData((prev) => ({
            ...prev,
            state: snapshot.state,
            alerts: (snapshot.alerts || []).map(normalizeAlert),
            isLive: true,
          }))
        }
      },
      onState: (state) => {
        setLiveData((prev) => ({
          ...prev,
          state: { ...prev.state, ...state },
        }))
      },
      onAlert: (envelope) => {
        const { action } = envelope
        const normalized = normalizeAlert(envelope)

        setLiveData((prev) => {
          let nextAlerts = [...prev.alerts]
          if (action === 'RAISED') {
            if (!nextAlerts.some((a) => a.id === normalized.id)) {
              nextAlerts.unshift(normalized)
            }
          } else if (action === 'CLEARED') {
            nextAlerts = nextAlerts.filter((a) => a.id !== normalized.id)
            if (normalized.rule === 'R03') {
              setExitGuardOpen(false)
            }
            if (normalized.rule === 'R12' || normalized.rule === 'R13') {
              setProximityAlertOpen(false)
            }
          } else if (action === 'ACKED' || action === 'UPDATED') {
            nextAlerts = nextAlerts.map((a) => (a.id === normalized.id ? normalized : a))
          }
          return { ...prev, alerts: nextAlerts }
        })
      },
      onNudge: (nudge) => {
        setLiveData((prev) => ({ ...prev, nudge }))

        const spokenText = formatMessage(nudge.message_key, nudge.slots)

        // Hero Feature: R03 Exit Guard Fullscreen Modal
        if (nudge.display === 'FULLSCREEN' && nudge.rule_id === 'R03') {
          setExitGuardOpen(true)
          playAlertTone()
          if (spokenText) speakNudge(spokenText)
        }

        // Proximity Alert Nudge (R12 / R13)
        if (nudge.rule_id === 'R12' || nudge.rule_id === 'R13') {
          const slots = nudge.slots || {}
          setProximityDetails({
            distance_m: slots.distance_m ?? 2.9,
            sector: slots.sector ?? 'behind you, left side',
            action: nudge.rule_id === 'R13' ? 'Stop swinging now' : 'Watch perimeter',
          })
          setProximityAlertOpen(true)
          playAlertTone()
          if (spokenText) speakNudge(spokenText)
        }

        // Other audio nudges per policy
        if (nudge.rule_id !== 'R03' && nudge.rule_id !== 'R12' && nudge.rule_id !== 'R13') {
          if (nudge.channels?.includes('AUDIO') || nudge.level === 'CRITICAL') {
            playAlertTone()
            if (spokenText) speakNudge(spokenText)
          }
        }
      },
    })

    wsClientRef.current = client
    client.connect()

    return () => {
      client.disconnect()
    }
  }, [isAuthenticated, machineId])

  // Critical alert tone audio trigger
  useEffect(() => {
    const criticalAlert = liveData.alerts?.find(
      (alert) => String(alert.level).toLowerCase() === 'critical' || alert.rule === 'R03',
    )

    if (criticalAlert && criticalAlert.id !== lastCriticalAlertRef.current) {
      lastCriticalAlertRef.current = criticalAlert.id
      playAlertTone()
    }

    if (!criticalAlert) {
      lastCriticalAlertRef.current = ''
    }
  }, [liveData.alerts])

  useEffect(() => {
    window.localStorage.setItem('cat-readiness-state', JSON.stringify(readinessState))
  }, [readinessState])

  const handleLogout = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem('cat-jwt')
      window.localStorage.removeItem('cat-operator-id')
      window.localStorage.removeItem('cat-operator-role')
    }
    wsClientRef.current?.disconnect()
    setIsAuthenticated(false)
  }

  if (!isAuthenticated) {
    return (
      <AuthScreen
        onUnlock={async ({ badgeId, pin, machineId: chosenMachine }) => {
          const result = await loginWithBadgeAndPin(badgeId, pin, chosenMachine)
          if (result?.token) {
            setMachineId(chosenMachine)
            setIsAuthenticated(true)
          }
        }}
      />
    )
  }

  const isWsLive = wsStatus === 'connected'

  return (
    <div className="app-shell">
      <SafetyStrip alerts={liveData.alerts} />

      <header className="app-header">
        <div>
          <p className="brand-mark">Operator Companion</p>
          <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>Machine {machineId}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="online-indicator">
            <span className={`status-dot ${isWsLive ? 'success' : liveData.isLive ? 'warning' : 'danger'}`} />
            <span className="online-text">
              {isWsLive ? 'Live Stream' : liveData.isLive ? 'Polling' : 'Offline'}
            </span>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            style={{
              background: 'transparent',
              border: '1px solid #4b5563',
              borderRadius: '0.25rem',
              color: '#9ca3af',
              fontSize: '0.7rem',
              padding: '0.2rem 0.4rem',
              cursor: 'pointer',
            }}
          >
            Switch
          </button>
        </div>
      </header>

      {!readinessOpen && !exitGuardOpen && (
        <button
          type="button"
          className="sos-button global-sos-button"
          aria-label="Report hazard or incident"
          onClick={() => setHazardReportOpen(true)}
        >
          <span className="sos-icon">SOS</span>
          <span className="sos-copy">
            <span className="sos-main">Report Hazard / Event</span>
            <span className="sos-sub">Incident · Near miss · Unsafe condition</span>
          </span>
        </button>
      )}

      <main className="main-content">
        {tab === 'dashboard' && (
          <Dashboard
            showExitGuard={() => setExitGuardOpen(true)}
            showHazardReport={() => setHazardReportOpen(true)}
            showReadiness={() => setReadinessOpen(true)}
            onSelectAlert={(alert) => setSelectedAlertForWhy(alert)}
            alerts={liveData.alerts}
            state={{ ...liveData.state, readiness: readinessState.rating }}
            tasks={liveData.tasks}
          />
        )}
        {tab === 'safety' && <Safety state={liveData.state} alerts={liveData.alerts} />}
        {tab === 'tasks' && <Tasks tasks={liveData.tasks} />}
        {tab === 'training' && <Training />}
        {tab === 'site' && <SiteMap />}
        {tab === 'scorecard' && <Scorecard state={liveData.state} />}
      </main>

      <nav className="bottom-nav">
        {NAV.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={`nav-item ${tab === item.id ? 'active' : ''}`}
          >
            <span className="nav-label">{item.label}</span>
            {tab === item.id && <span className="nav-bar" />}
          </button>
        ))}
      </nav>

      {/* Simulator Control Bar for Hackathon Demos & Live Testing */}
      <SimControlBar machineId={machineId} />

      {/* Alert Explainer ("Why?") Modal */}
      {selectedAlertForWhy && (
        <AlertWhyModal
          alertId={selectedAlertForWhy.id}
          onClose={() => setSelectedAlertForWhy(null)}
          onAlertAcked={(ackedId) => {
            setLiveData((prev) => ({
              ...prev,
              alerts: prev.alerts.map((a) =>
                a.id === ackedId ? { ...a, acknowledgedAt: new Date().toISOString() } : a,
              ),
            }))
          }}
        />
      )}

      {/* Dynamic Proximity Alert */}
      {proximityAlertOpen && (
        <div className="proximity-overlay" role="dialog" aria-modal="true">
          <div className="proximity-alert-card">
            <div className="proximity-alert-topbar">
              <div className="proximity-pill">
                <span className="proximity-icon">🔊</span>
                Voice alert on
              </div>
              <button
                type="button"
                className="proximity-close"
                onClick={() => setProximityAlertOpen(false)}
                style={{
                  fontSize: '1.25rem',
                  fontWeight: 800,
                  width: '44px',
                  height: '44px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: '50%',
                  background: 'rgba(255, 255, 255, 0.2)',
                  border: 'none',
                  color: '#fff',
                  cursor: 'pointer',
                }}
                aria-label="Close proximity alert"
              >
                ✕
              </button>
            </div>

            <div className="proximity-title-wrap">
              <div className="proximity-badge">⚠</div>
              <h2>PERSON<br />BEHIND</h2>
            </div>

            <p className="proximity-subtitle">{proximityDetails.action}</p>

            <div className="proximity-visual">
              <div className="proximity-zone" />
              <div className="proximity-machine">
                <div className="machine-body" />
                <div className="machine-pointer" />
              </div>
              <div className="rear-camera-tag">Rear camera</div>
            </div>

            <div className="proximity-distance">{proximityDetails.distance_m} m</div>
            <div className="proximity-caption">away · {proximityDetails.sector}</div>

            <div className="proximity-status-row">
              <span className="proximity-status danger">● Danger 0 - 3.6 m</span>
              <span className="proximity-status warning">● Watch 3.6 - 8 m</span>
            </div>

            <button
              type="button"
              onClick={() => setProximityAlertOpen(false)}
              style={{
                width: '100%',
                marginTop: '1rem',
                padding: '0.85rem',
                borderRadius: '0.5rem',
                background: '#dc2626',
                color: '#fff',
                border: 'none',
                fontWeight: 800,
                fontSize: '1rem',
                letterSpacing: '0.05em',
                textTransform: 'uppercase',
                cursor: 'pointer',
                boxShadow: '0 4px 12px rgba(220, 38, 38, 0.4)',
              }}
            >
              Acknowledge & Stop Swing
            </button>
          </div>
        </div>
      )}

      {/* Incident / Hazard SOS Modal */}
      {hazardReportOpen && (
        <HazardReportModal
          onClose={() => setHazardReportOpen(false)}
          onSubmit={async ({ type, description }) => {
            await createIncident({
              type: type?.toUpperCase().replace(/\s+/g, '_') || 'INCIDENT',
              description: description || `Reported by operator: ${type || 'Incident'}`,
              machine_id: machineId,
            })
            setHazardReportOpen(false)
          }}
        />
      )}

      {/* Exit Guard Modal connected to live exit_checks */}
      {exitGuardOpen && (
        <ExitGuardModal
          onClose={() => setExitGuardOpen(false)}
          exitChecks={liveData.state?.exit_checks || {}}
          activeAlert={liveData.alerts?.find((a) => a.rule === 'R03' && a.active)}
          onAcknowledge={async () => {
            const r03 = liveData.alerts?.find((a) => a.rule === 'R03')
            if (r03?.id) {
              await acknowledgeAlert(r03.id)
              setLiveData((prev) => ({
                ...prev,
                alerts: prev.alerts.map((a) =>
                  a.id === r03.id ? { ...a, acknowledgedAt: new Date().toISOString() } : a,
                ),
              }))
            }
          }}
        />
      )}

      {/* Readiness Modal */}
      {readinessOpen && (
        <ReadinessModal
          onClose={() => setReadinessOpen(false)}
          onSubmit={async (next) => {
            const payload = await submitReadiness(next)
            setReadinessState({
              score: payload.score ?? next.score,
              rating: payload.rating ?? next.rating,
              reasons: payload.reasons ?? next.reasons,
            })
            window.localStorage.setItem('cat-readiness-onboarded', 'true')
          }}
        />
      )}
    </div>
  )
}
