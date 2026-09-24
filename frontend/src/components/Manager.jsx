import { useCallback, useEffect, useState } from 'react'
import AssignTaskModal from './AssignTaskModal'
import ManagerTasks from './ManagerTasks'
import ManagerTeam from './ManagerTeam'
import SimControlBar from './SimControlBar'
import { fetchDayTasks, fetchManagerOptions, fetchOperators } from '../services/backendService'

const NAV = [
  { id: 'team', label: 'Team' },
  { id: 'plan', label: 'Tasks' },
]
const POLL_MS = 10000 // HLD §4.15: the supervisor view polls every 10 s

const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function Manager({ session, machineId, onLogout, onMachineView }) {
  const [tab, setTab] = useState('team')
  const [operators, setOperators] = useState([])
  const [options, setOptions] = useState(null)
  const [dayTasks, setDayTasks] = useState([])
  const [date, setDate] = useState(today)
  const [asOf, setAsOf] = useState(null)
  const [ok, setOk] = useState(false)
  const [notice, setNotice] = useState('')
  const [editor, setEditor] = useState(null) // {task} | {defaults}
  const [refreshKey, setRefreshKey] = useState(0)

  const load = useCallback(async () => {
    try {
      const [ops, day] = await Promise.all([fetchOperators(), fetchDayTasks(date)])
      setOperators(ops.operators)
      setDayTasks(day.tasks)
      setAsOf(ops.as_of)
      setOk(true)
      setRefreshKey((n) => n + 1)
    } catch (error) {
      setOk(false)
      console.warn('Manager data unavailable:', error.message)
    }
  }, [date])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- setState runs after the fetch resolves
    load()
    const interval = setInterval(load, POLL_MS)
    return () => clearInterval(interval)
  }, [load])

  const loadOptions = useCallback(
    () =>
      fetchManagerOptions()
        .then((o) => {
          setOptions(o)
          return o
        })
        .catch((e) => {
          setNotice(`Assign form unavailable: ${e.message}`)
          return null
        }),
    [],
  )

  useEffect(() => {
    loadOptions()
  }, [loadOptions])

  const openEditor = async (value) => {
    if (options || (await loadOptions())) setEditor(value)
  }

  const statusLabel = ok ? 'Live' : asOf ? 'Offline' : 'Waiting'

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="brand-mark">Operator Companion</p>
          <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>
            Supervisor · {session.operatorName || session.operatorId}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div className="online-indicator" title={asOf ? `As of ${new Date(asOf).toLocaleTimeString()}` : ''}>
            <span className={`status-dot ${ok ? 'success' : 'danger'}`} />
            <span className="online-text">
              {statusLabel}
              {!ok && asOf ? ` · as of ${new Date(asOf).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}` : ''}
            </span>
          </div>
          <button type="button" className="header-button" onClick={onMachineView}>Machine view</button>
          <button type="button" className="header-button" onClick={onLogout}>Switch</button>
        </div>
      </header>

      {notice && (
        <div className="notice-bar" onClick={() => setNotice('')} role="status">
          {notice} <span style={{ opacity: 0.6 }}>✕</span>
        </div>
      )}

      <main className="main-content">
        {tab === 'team' && (
          <ManagerTeam
            operators={operators}
            refreshKey={refreshKey}
            onAssign={(defaults) => openEditor({ defaults })}
            onReassign={(task) => openEditor({ task })}
          />
        )}
        {tab === 'plan' && (
          <ManagerTasks
            tasks={dayTasks}
            date={date}
            onDateChange={(d) => d && setDate(d)}
            onAssign={(defaults) => openEditor({ defaults })}
            onReassign={(task) => openEditor({ task })}
            onChanged={load}
            onError={setNotice}
          />
        )}
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

      {editor && (
        <AssignTaskModal
          options={options}
          operators={operators}
          task={editor.task}
          defaults={editor.defaults}
          onClose={() => setEditor(null)}
          onDone={(msg) => {
            setNotice(msg)
            load()
          }}
        />
      )}
    </div>
  )
}
