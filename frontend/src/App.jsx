import { useCallback, useEffect, useRef, useState } from 'react'
import SafetyStrip from './components/SafetyStrip'
import Dashboard from './components/Dashboard'
import Safety from './components/Safety'
import Tasks from './components/Tasks'
import Training from './components/Training'
import Assistant from './components/Assistant'
import SiteMap from './components/SiteMap'
import AuthScreen from './components/AuthScreen'
import ExitGuardModal from './components/ExitGuardModal'
import HazardReportModal from './components/HazardReportModal'
import ReadinessModal from './components/ReadinessModal'
import AlertWhyModal from './components/AlertWhyModal'
import SimControlBar from './components/SimControlBar'
import Manager from './components/Manager'
import {
  AUTH_EXPIRED_EVENT,
  acknowledgeAlert,
  clearSession,
  endShift,
  fetchSnapshot,
  fetchTasks,
  getAuthToken,
  getSession,
  getStoredMachineId,
  isManager,
  login,
  normalizeAlert,
  normalizeTask,
  sortAlerts,
  startShift,
} from './services/backendService'
import { LiveWebSocketClient } from './services/liveSocket'
import { formatMessage } from './data/i18n'

const NAV = [
  { id: 'dashboard', label: 'Home' },
  { id: 'safety', label: 'Safety' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'training', label: 'Learn' },
  { id: 'help', label: 'Help' },
  { id: 'site', label: 'Map' },
]

const EMPTY_LIVE = { state: null, telemetry: null, alerts: [], hazards: [], sync: null, asOf: null }

function playTone(ctxRef, critical) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext
  if (!AudioContextClass) return
  try {
    const ctx = ctxRef.current ?? new AudioContextClass()
    ctxRef.current = ctx
    if (ctx.state === 'suspended') ctx.resume()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = critical ? 'sawtooth' : 'sine'
    osc.frequency.setValueAtTime(critical ? 820 : 660, ctx.currentTime)
    osc.frequency.exponentialRampToValueAtTime(420, ctx.currentTime + 0.26)
    gain.gain.setValueAtTime(0.0001, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(critical ? 0.1 : 0.05, ctx.currentTime + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.42)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.44)
  } catch (error) {
    console.warn('Audio alert unavailable:', error)
  }
}

function speak(text, critical) {
  if (!text || !window.speechSynthesis) return
  // A CRITICAL nudge interrupts whatever is being said; lower levels queue behind it (I4).
  if (critical) window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.rate = 1.05
  window.speechSynthesis.speak(utterance)
}

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [machineId, setMachineId] = useState(getStoredMachineId)
  const [session, setSession] = useState(() => (getAuthToken() ? getSession() : null))
  const [wsStatus, setWsStatus] = useState('disconnected')
  const [live, setLive] = useState(EMPTY_LIVE)
  const [tasks, setTasks] = useState([])
  const [lessonTick, setLessonTick] = useState(0)
  const [notice, setNotice] = useState('')
  const [machineView, setMachineView] = useState(false)
  const managerView = Boolean(session && isManager(session.role) && !machineView)

  const [exitGuardOpen, setExitGuardOpen] = useState(false)
  const [proximity, setProximity] = useState(null) // R12 FULLSCREEN nudge slots
  const [hazardReportOpen, setHazardReportOpen] = useState(false)
  const [readinessOpen, setReadinessOpen] = useState(false)
  const [lastReadiness, setLastReadiness] = useState(null)
  const [whyAlertId, setWhyAlertId] = useState(null)

  const audioRef = useRef(null)

  const logout = useCallback(() => {
    clearSession()
    setSession(null)
    setMachineView(false)
    setLive(EMPTY_LIVE)
    setTasks([])
    setExitGuardOpen(false)
    setProximity(null)
  }, [])

  useEffect(() => {
    window.addEventListener(AUTH_EXPIRED_EVENT, logout)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, logout)
  }, [logout])

  const loadTasks = useCallback(async () => {
    try {
      const data = await fetchTasks(machineId)
      setTasks(data.tasks.map(normalizeTask))
    } catch (error) {
      console.warn('Tasks unavailable:', error.message)
    }
  }, [machineId])

  const applySnapshot = useCallback((snap) => {
    const alerts = snap.alerts.map(normalizeAlert)
    setLive((prev) => ({
      ...prev,
      state: snap.state,
      alerts: sortAlerts(alerts),
      hazards: snap.hazards?.pins || [],
      sync: snap.sync,
      asOf: snap.as_of,
    }))
    // Reconnect mid-alert: the overlays follow the active alert, not a missed nudge.
    setExitGuardOpen((open) => open || alerts.some((a) => a.rule === 'R03'))
    if (!alerts.some((a) => a.rule === 'R12')) setProximity(null)
  }, [])

  // REST first paint + tasks; the WebSocket snapshot then keeps it current.
  useEffect(() => {
    if (!session || managerView) return undefined
    fetchSnapshot(machineId).then(applySnapshot).catch((e) => console.warn('Snapshot unavailable:', e.message))
    // eslint-disable-next-line react-hooks/set-state-in-effect -- setState runs after the fetch resolves
    loadTasks()
    const interval = setInterval(loadTasks, 60000)
    return () => clearInterval(interval)
  }, [session, managerView, machineId, applySnapshot, loadTasks])

  useEffect(() => {
    if (!session || managerView) return undefined
    const client = new LiveWebSocketClient({
      machineId,
      onStatusChange: setWsStatus,
      onAuthError: (code) => {
        if (code === 4401) logout()
        else setNotice(`Live stream refused (${code === 4403 ? 'not allowed for this machine' : 'unknown machine'})`)
      },
      handlers: {
        snapshot: applySnapshot,
        state: (state) => setLive((prev) => ({ ...prev, state, asOf: state.ts })),
        telemetry: (telemetry) => setLive((prev) => ({ ...prev, telemetry })),
        hazards: (h) => setLive((prev) => ({ ...prev, hazards: h.pins || [] })),
        sync: (sync) => setLive((prev) => ({ ...prev, sync })),
        eta: () => loadTasks(),
        lesson: (data) => {
          setLessonTick((n) => n + 1)
          if (data.deliverable) setNotice('A lesson is ready — open Learn.')
        },
        alert: ({ action, alert }) => {
          const a = normalizeAlert(alert)
          setLive((prev) => {
            const rest = prev.alerts.filter((x) => x.id !== a.id)
            return { ...prev, alerts: action === 'CLEARED' ? rest : sortAlerts([a, ...rest]) }
          })
          if (action === 'CLEARED') {
            if (a.rule === 'R03') setExitGuardOpen(false)
            if (a.rule === 'R12') setProximity(null)
          }
        },
        nudge: (nudge) => {
          const critical = nudge.level === 'CRITICAL'
          if (nudge.display === 'FULLSCREEN' && nudge.rule_id === 'R03') setExitGuardOpen(true)
          if (nudge.display === 'FULLSCREEN' && nudge.rule_id === 'R12') setProximity(nudge.slots || {})
          if (critical || nudge.channels?.includes('AUDIO')) {
            playTone(audioRef, critical)
            speak(formatMessage(nudge.message_key, nudge.slots), critical)
          }
        },
      },
    })
    client.connect()
    return () => client.disconnect()
  }, [session, managerView, machineId, applySnapshot, loadTasks, logout])

  const ackAlert = async (alertId) => {
    try {
      const record = await acknowledgeAlert(alertId)
      const a = normalizeAlert(record)
      setLive((prev) => ({ ...prev, alerts: prev.alerts.map((x) => (x.id === a.id ? a : x)) }))
    } catch (error) {
      setNotice(`Acknowledge failed: ${error.message}`)
    }
  }

  const handleEndShift = async () => {
    const note = window.prompt('Handover note for the next operator (optional):', '')
    if (note === null) return
    try {
      await endShift(machineId, note)
    } catch (error) {
      setNotice(`End shift failed: ${error.message}`)
      return
    }
    logout()
  }

  if (!session) {
    return (
      <AuthScreen
        onUnlock={async ({ badgeId, pin, machineId: chosen }) => {
          const { operator } = await login(badgeId, pin, chosen)
          setMachineId(chosen)
          setSession(getSession())
          if (isManager(operator.role)) return // a supervisor plans; they don't start the machine's shift
          try {
            const res = await startShift(chosen)
            setNotice(res.previous_handover_note ? `Handover note: ${res.previous_handover_note}` : '')
          } catch (error) {
            setNotice(`Shift not started: ${error.message}`)
          }
          setReadinessOpen(true)
        }}
      />
    )
  }

  if (managerView) {
    return <Manager session={session} machineId={machineId} onLogout={logout} onMachineView={() => setMachineView(true)} />
  }

  const state = live.state
  const isLive = wsStatus === 'connected' && state && !state.data_stale
  const statusLabel = wsStatus !== 'connected' ? 'Offline' : !state ? 'Waiting' : state.data_stale ? 'Data stale' : 'Live'
  const r03 = live.alerts.find((a) => a.rule === 'R03')
  const r12 = live.alerts.find((a) => a.rule === 'R12')

  return (
    <div className="app-shell">
      <SafetyStrip alerts={live.alerts} stale={!isLive} statusLabel={statusLabel} />

      <header className="app-header">
        <div>
          <p className="brand-mark">Operator Companion</p>
          <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>
            {machineId} · {session.operatorName || session.operatorId}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="online-indicator" title={live.asOf ? `As of ${new Date(live.asOf).toLocaleTimeString()}` : ''}>
            <span className={`status-dot ${isLive ? 'success' : wsStatus === 'connected' ? 'warning' : 'danger'}`} />
            <span className="online-text">
              {statusLabel}
              {!isLive && live.asOf ? ` · as of ${new Date(live.asOf).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}` : ''}
            </span>
          </div>
          {isManager(session.role) ? (
            <button type="button" className="header-button" onClick={() => setMachineView(false)}>Manager</button>
          ) : (
            <button type="button" className="header-button" onClick={handleEndShift}>End shift</button>
          )}
          <button type="button" className="header-button" onClick={logout}>Switch</button>
        </div>
      </header>

      {notice && (
        <div className="notice-bar" onClick={() => setNotice('')} role="status">
          {notice} <span style={{ opacity: 0.6 }}>✕</span>
        </div>
      )}

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
            machineId={machineId}
            operatorId={session.operatorId}
            operatorName={session.operatorName}
            showExitGuard={() => setExitGuardOpen(true)}
            showHazardReport={() => setHazardReportOpen(true)}
            showReadiness={() => setReadinessOpen(true)}
            onSelectAlert={(alert) => setWhyAlertId(alert.id)}
            alerts={live.alerts}
            state={state}
            telemetry={isLive ? live.telemetry : null}
            tasks={tasks}
            lastReadiness={lastReadiness}
          />
        )}
        {tab === 'safety' && (
          <Safety state={state} telemetry={isLive ? live.telemetry : null} alerts={live.alerts} onSelectAlert={(a) => setWhyAlertId(a.id)} />
        )}
        {tab === 'tasks' && <Tasks tasks={tasks} onChanged={loadTasks} onError={setNotice} />}
        {tab === 'training' && <Training refreshKey={lessonTick} />}
        {tab === 'help' && <Assistant machineId={machineId} alerts={live.alerts} />}
        {tab === 'site' && <SiteMap hazards={live.hazards} telemetry={isLive ? live.telemetry : null} machineId={machineId} />}
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

      {session.role === 'admin' && <SimControlBar machineId={machineId} />}

      {whyAlertId && (
        <AlertWhyModal alertId={whyAlertId} onClose={() => setWhyAlertId(null)} onAck={ackAlert} />
      )}

      {proximity && (
        <div className="proximity-overlay" role="alertdialog" aria-modal="true">
          <div className="proximity-alert-card">
            <div className="proximity-alert-topbar">
              <div className="proximity-pill">
                <span className="proximity-icon">🔊</span>
                Voice alert on
              </div>
            </div>

            <div className="proximity-title-wrap">
              <div className="proximity-badge">⚠</div>
              <h2>PERSON<br />BEHIND</h2>
            </div>

            <p className="proximity-subtitle">{formatMessage('nudge.proximity.person_behind', proximity)}</p>

            <div className="proximity-visual">
              <div className="proximity-zone" />
              <div className="proximity-machine">
                <div className="machine-body" />
                <div className="machine-pointer" />
              </div>
              <div className="rear-camera-tag">Rear sensor</div>
            </div>

            <div className="proximity-distance">
              {proximity.distance_m ?? live.telemetry?.proximity?.min_dist_m ?? '—'} m
            </div>
            <div className="proximity-caption">away · {proximity.sector ?? live.telemetry?.proximity?.sector ?? 'sector unknown'}</div>

            {r12 && !r12.acknowledgedAt ? (
              <button type="button" className="proximity-ack" onClick={() => ackAlert(r12.id)}>
                Acknowledge — stop swing
              </button>
            ) : (
              <p className="proximity-caption">Acknowledged — clears when the area is clear.</p>
            )}
            {!r12 && (
              <button type="button" className="proximity-ack" onClick={() => setProximity(null)}>Close</button>
            )}
          </div>
        </div>
      )}

      {hazardReportOpen && (
        <HazardReportModal machineId={machineId} onClose={() => setHazardReportOpen(false)} onDone={setNotice} />
      )}

      {exitGuardOpen && (
        <ExitGuardModal
          exitChecks={state?.exit_checks || {}}
          activeAlert={r03}
          stale={!isLive}
          onAcknowledge={() => r03 && ackAlert(r03.id)}
          onClose={() => setExitGuardOpen(false)}
        />
      )}

      {readinessOpen && (
        <ReadinessModal
          machineId={machineId}
          onClose={() => setReadinessOpen(false)}
          onSaved={setLastReadiness}
        />
      )}
    </div>
  )
}
