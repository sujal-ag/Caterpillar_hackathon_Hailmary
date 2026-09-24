// REST client for edge-api (contracts/rest.md). No mock fallbacks: when the edge is
// unreachable the UI shows "offline" instead of made-up data (I1, I6).
import { formatMessage } from '../data/i18n'

// Default: same origin through the Vite proxy (/api -> :8000, /ws -> :8000), so the tablet
// only needs to reach the dev/preview server and no CORS setup is needed.
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
export const WS_BASE_URL = (
  import.meta.env.VITE_WS_BASE_URL ||
  `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
).replace(/\/$/, '')

const store = window.localStorage
export const AUTH_EXPIRED_EVENT = 'cat-auth-expired'

export const getAuthToken = () => store.getItem('cat-jwt')
export const getStoredMachineId = () => store.getItem('cat-machine-id') || 'EXC001'
export const getSession = () => ({
  operatorId: store.getItem('cat-operator-id'),
  operatorName: store.getItem('cat-operator-name'),
  role: store.getItem('cat-operator-role') || 'operator',
})

export function clearSession() {
  ;['cat-jwt', 'cat-operator-id', 'cat-operator-name', 'cat-operator-role'].forEach((k) => store.removeItem(k))
}

// FastAPI's `detail` is a string for a plain HTTPException, or a pydantic validation array
// (`[{loc, msg, ...}]`) for a 422 — turn either into one line, never raw JSON on screen.
function formatDetail(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const lines = detail.map((e) => {
      const field = Array.isArray(e?.loc) ? e.loc.filter((p) => p !== 'body').join('.') : ''
      return field ? `${field}: ${e.msg}` : e?.msg || 'invalid'
    })
    if (lines.length) return lines.join('; ')
  }
  return 'Request failed'
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(formatDetail(detail))
    this.status = status
  }
}

async function request(path, { body, method = body ? 'POST' : 'GET', auth = true } = {}) {
  const headers = {}
  const token = getAuthToken()
  if (auth && token) headers.Authorization = `Bearer ${token}`
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body && !(body instanceof FormData) ? JSON.stringify(body) : body,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 && auth) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    throw new ApiError(response.status, data?.detail || `Request failed: ${response.status}`)
  }
  return data
}

const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—')

// alert.v1 envelope or a bare SafetyAlert record -> view model
export function normalizeAlert(item) {
  const alert = item?.alert ?? item
  return {
    id: alert.alert_id,
    level: String(alert.level || 'INFO').toLowerCase(),
    title: alert.subject || 'Alert',
    message: formatMessage(alert.message_key, alert.slots),
    time: clock(alert.ts),
    ts: alert.ts,
    rule: alert.rule_id,
    acknowledgedAt: alert.acknowledged_at || null,
    active: alert.active ?? true,
    channels: alert.channels || ['VISUAL'],
  }
}

const LEVEL_RANK = { critical: 0, warning: 1, caution: 2, info: 3 }
export const sortAlerts = (alerts) =>
  [...alerts].sort((a, b) => (LEVEL_RANK[a.level] ?? 9) - (LEVEL_RANK[b.level] ?? 9) || String(b.ts).localeCompare(String(a.ts)))

// task.v1 -> view model
export function normalizeTask(task) {
  const planned = Number(task.planned_quantity || 0)
  const actual = Number(task.actual_quantity || 0)
  const pred = task.prediction
  return {
    id: task.task_id,
    title: `${task.task_type_id} — ${task.zone_id || 'Zone'}`,
    location: [task.zone_id, task.soil_type].filter(Boolean).join(' · '),
    status: task.status,
    eta: clock(pred?.eta),
    etaLabel: pred?.label || (pred ? `p50 ${Math.round(pred.p50_min)} min` : null),
    progress: task.status === 'DONE' ? 100 : planned > 0 ? Math.min(100, Math.round((actual / planned) * 100)) : 0,
    prediction: pred,
    scheduledStart: clock(task.scheduled_start),
  }
}

export async function login(badgeId, pin, machineId) {
  const result = await request('/auth/login', { body: { badge_id: badgeId, pin, machine_id: machineId }, auth: false })
  store.setItem('cat-jwt', result.token)
  store.setItem('cat-machine-id', machineId)
  store.setItem('cat-operator-id', result.operator.operator_id)
  store.setItem('cat-operator-name', result.operator.name)
  store.setItem('cat-operator-role', result.operator.role)
  return result
}

export const startShift = (machineId) => request('/shift/start', { body: { machine_id: machineId } })
export const endShift = (machineId, handoverNote) =>
  request('/shift/end', { body: { machine_id: machineId, handover_note: handoverNote || null } })

export const fetchSnapshot = (machineId) => request(`/state/current?machine=${encodeURIComponent(machineId)}`)
export const fetchTasks = (machineId) => request(`/tasks?machine=${encodeURIComponent(machineId)}`)
export const setTaskStatus = (taskId, status) => request(`/tasks/${taskId}/status`, { body: { status } })

export const acknowledgeAlert = (alertId) => request(`/alerts/${alertId}/ack`, { method: 'POST' })
export const fetchAlertWhy = (alertId) => request(`/alerts/${alertId}/why`)

// body: readiness inputs (edge/api/readiness.py ReadinessIn); the server computes the score.
export const submitReadiness = (body) => request('/readiness', { body })

export const fetchAssignedLessons = () => request('/lessons/assigned')
export const fetchLesson = (assignmentId) => request(`/lessons/${assignmentId}`)
export const completeLesson = (assignmentId, score, answers = []) =>
  request(`/lessons/${assignmentId}/complete`, { body: { score, answers } })

// incident: {type, category, severity_self, transcript?, machine_id?, create_hazard?}
export function createIncident(incident, { voice, photos = [] } = {}) {
  const form = new FormData()
  form.append('incident', JSON.stringify(incident))
  if (voice) form.append('voice', voice)
  photos.slice(0, 3).forEach((p) => form.append('photos', p))
  return request('/incidents', { body: form })
}

// Alarm explainer (Phase 8): catalogue cards need no LLM; ask falls back to the catalogue template.
export const fetchDiagnostics = (machineId) => request(`/diagnostics/active?machine=${encodeURIComponent(machineId)}`)
export const askAssistant = (question, { contextCode, machineId } = {}) =>
  request('/assistant/ask', { body: { question, context_code: contextCode || null, machine: machineId || null } })

export const fetchSimScenarios = () => request('/sim/scenarios')
export const triggerSimScenario = (name, speed, machineId) =>
  request('/sim/scenario', { body: { name, speed, machine_id: machineId } })
export const stopSimScenario = () => request('/sim/stop', { method: 'POST' })
