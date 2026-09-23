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
import { ALERTS, NAV, TASKS as FALLBACK_TASKS } from './data/mockData'
import { BACKEND_STATE } from './data/backendContract'
import { createIncident, fetchOperatorData, getAuthToken, loginWithBadgeAndPin, submitReadiness } from './services/backendService'

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [exitGuardOpen, setExitGuardOpen] = useState(false)
  const [hazardReportOpen, setHazardReportOpen] = useState(false)
  const [readinessOpen, setReadinessOpen] = useState(false)
  // const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getAuthToken()))
  const [isAuthenticated, setIsAuthenticated] = useState(true)
  const [proximityAlertOpen, setProximityAlertOpen] = useState(true)
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
  })
  const lastSeatbeltRef = useRef('')
  const lastCriticalAlertRef = useRef('')
  const audioContextRef = useRef(null)

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

  useEffect(() => {
    if (!isAuthenticated) return undefined

    let isMounted = true

    const load = async () => {
      const next = await fetchOperatorData()
      if (isMounted) {
        setLiveData(next)
      }
    }

    load()
    const interval = window.setInterval(load, 15000)

    return () => {
      isMounted = false
      window.clearInterval(interval)
    }
  }, [isAuthenticated])

  useEffect(() => {
    const seatbeltStatus = String(
      liveData.state?.sensor_health?.seatbelt
      || liveData.state?.seatbelt
      || liveData.state?.seatbelt_status
      || '',
    ).toUpperCase()

    if (!seatbeltStatus) return

    const previousSeatbelt = lastSeatbeltRef.current
    const unsafeSeatbeltStates = new Set(['UNFASTENED', 'NOT_FASTENED', 'FAULT', 'FAIL', 'LOOSE'])

    if (previousSeatbelt && previousSeatbelt !== seatbeltStatus && unsafeSeatbeltStates.has(seatbeltStatus)) {
      setExitGuardOpen(true)
      playAlertTone()
    }

    lastSeatbeltRef.current = seatbeltStatus
  }, [liveData.state?.sensor_health?.seatbelt, liveData.state?.seatbelt, liveData.state?.seatbelt_status])

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
    const alreadyCompleted = window.localStorage.getItem('cat-readiness-onboarded') === 'true'
    if (!alreadyCompleted) {
      setReadinessOpen(true)
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem('cat-readiness-state', JSON.stringify(readinessState))
  }, [readinessState])

  useEffect(() => {
    const proximityState = String(liveData.state?.sensor_health?.proximity || '').toUpperCase()
    if (proximityState === 'WARN' || proximityState === 'WARNING' || proximityState === 'ALERT') {
      setProximityAlertOpen(true)
    }
  }, [liveData.state?.sensor_health?.proximity])

  if (!isAuthenticated) {
    return (
      <AuthScreen
        onUnlock={async ({ badgeId, pin }) => {
          const result = await loginWithBadgeAndPin(badgeId, pin)
          if (result?.token) {
            setIsAuthenticated(true)
          }
        }}
      />
    )
  }

  return (
    <div className="app-shell">
      <SafetyStrip alerts={liveData.alerts} />

      <header className="app-header">
        <p className="brand-mark">Operator Companion</p>
        <div className="online-indicator">
          <span className="status-dot success" />
          <span className="online-text">Online</span>
        </div>
      </header>

      {!readinessOpen && (
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

      {proximityAlertOpen && (
        <div className="proximity-overlay" role="dialog" aria-modal="true">
          <div className="proximity-alert-card">
            <div className="proximity-alert-topbar">
              <div className="proximity-pill">
                <span className="proximity-icon">🔊</span>
                Voice alert on
              </div>
              <button type="button" className="proximity-close" onClick={() => setProximityAlertOpen(false)}>
                ☾
              </button>
            </div>

            <div className="proximity-title-wrap">
              <div className="proximity-badge">⚠</div>
              <h2>PERSON<br />BEHIND</h2>
            </div>

            <p className="proximity-subtitle">Stop swinging now</p>

            <div className="proximity-visual">
              <div className="proximity-zone" />
              <div className="proximity-machine">
                <div className="machine-body" />
                <div className="machine-pointer" />
              </div>
              <div className="rear-camera-tag">Rear camera</div>
            </div>

            <div className="proximity-distance">2.9 m</div>
            <div className="proximity-caption">away · behind you, left side</div>

            <div className="proximity-status-row">
              <span className="proximity-status danger">● Danger 0 - 3.6 m</span>
              <span className="proximity-status warning">● Watch 3.6 - 8 m</span>
            </div>
          </div>
        </div>
      )}

      {hazardReportOpen && (
        <HazardReportModal
          onClose={() => setHazardReportOpen(false)}
          onSubmit={async ({ type, description }) => {
            await createIncident({
              type: type?.toUpperCase().replace(/\s+/g, '_') || 'INCIDENT',
              description: description || `Reported by operator: ${type || 'Incident'}`,
              machine_id: liveData.state?.machine_id || 'EXC-001',
            })
            setHazardReportOpen(false)
          }}
        />
      )}
      {exitGuardOpen && <ExitGuardModal onClose={() => setExitGuardOpen(false)} />}
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
