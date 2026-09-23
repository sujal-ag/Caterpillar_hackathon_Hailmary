import { ALERTS as fallbackAlerts, TASKS as fallbackTasks } from '../data/mockData'
import { BACKEND_STATE, BACKEND_ALERTS, BACKEND_TASKS, BACKEND_NUDGE } from '../data/backendContract'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export function getAuthToken() {
  if (typeof window === 'undefined') return null
  return window.localStorage.getItem('cat-jwt') || null
}

export function getAuthHeaders() {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const readJson = async (response) => {
  try {
    return await response.json()
  } catch {
    return null
  }
}

async function requestJson(path, options = {}) {
  const headers = new Headers(options.headers || {})
  const token = getAuthToken()

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const data = await readJson(response)
    throw new Error(data?.detail || `Request failed: ${response.status}`)
  }

  return readJson(response)
}

const normalizeAlert = (item) => {
  const alert = item?.alert ?? item
  const level = String(alert.level || 'info').toLowerCase()

  return {
    id: alert.alert_id,
    level,
    title: alert.subject || 'Alert',
    message: alert.message_key || 'nudge.alert.raised',
    time: 'Now',
    rule: alert.rule_id || 'R00',
    schema: item?.schema || 'alert.v1',
    action: item?.action || 'RAISED',
    messageKey: alert.message_key,
    slots: alert.slots || {},
  }
}

const normalizeTask = (task) => {
  const plannedQuantity = Number(task.planned_quantity || 0)
  const actualQuantity = Number(task.actual_quantity || 0)

  const progress = plannedQuantity > 0
    ? Math.min(100, Math.round((actualQuantity / plannedQuantity) * 100))
    : task.status === 'DONE'
      ? 100
      : 0

  return {
    id: task.task_id,
    title: `${task.task_type_id || 'Task'} — ${task.zone_id || 'Zone'}`,
    location: task.zone_id || 'Site A',
    status: String(task.status || 'SCHEDULED').toLowerCase().replace('_', '-'),
    eta: task.prediction?.eta
      ? new Date(task.prediction.eta).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : '—',
    progress,
    schema: task.schema,
    taskId: task.task_id,
    taskType: task.task_type_id,
    prediction: task.prediction,
  }
}

export async function fetchOperatorData() {
  try {
    const headers = getAuthHeaders()
    const [stateResponse, tasksResponse] = await Promise.all([
      fetch(`${API_BASE_URL}/state/current`, { headers }).catch(() => null),
      fetch(`${API_BASE_URL}/tasks?shift_id=SH-20260923-EXC001-D`, { headers }).catch(() => null),
    ])

    const stateData = stateResponse && stateResponse.ok ? await stateResponse.json() : BACKEND_STATE
    const tasksData = tasksResponse && tasksResponse.ok ? await tasksResponse.json() : BACKEND_TASKS
    const taskList = Array.isArray(tasksData) ? tasksData : Array.isArray(tasksData?.items) ? tasksData.items : BACKEND_TASKS
    const alerts = Array.isArray(stateData?.alerts)
      ? stateData.alerts.map(normalizeAlert)
      : BACKEND_ALERTS.map(normalizeAlert)

    return {
      state: stateData,
      alerts,
      tasks: taskList.map(normalizeTask),
      nudge: BACKEND_NUDGE,
    }
  } catch (error) {
    console.warn('Backend unavailable, falling back to mock data:', error)
    return {
      state: BACKEND_STATE,
      alerts: fallbackAlerts,
      tasks: fallbackTasks,
      nudge: BACKEND_NUDGE,
    }
  }
}

export async function submitReadiness(payload) {
  try {
    const result = await requestJson('/readiness', {
      method: 'POST',
      body: JSON.stringify({
        score: payload?.score ?? 0,
        rating: payload?.rating ?? 'GREEN',
        reasons: Array.isArray(payload?.reasons) ? payload.reasons : [],
        ts: new Date().toISOString(),
      }),
    })

    return result || {
      score: payload?.score ?? 0,
      rating: payload?.rating ?? 'GREEN',
      reasons: Array.isArray(payload?.reasons) ? payload.reasons : [],
    }
  } catch (error) {
    console.warn('Readiness submit unavailable; keeping local result only:', error)
    return {
      score: payload?.score ?? 0,
      rating: payload?.rating ?? 'GREEN',
      reasons: Array.isArray(payload?.reasons) ? payload.reasons : [],
    }
  }
}

export async function fetchAssignedLessons() {
  try {
    const result = await requestJson('/lessons/assigned')
    const list = Array.isArray(result) ? result : Array.isArray(result?.items) ? result.items : Array.isArray(result?.lessons) ? result.lessons : []
    return list
  } catch (error) {
    console.warn('Lessons fetch failed, falling back to static list:', error)
    return []
  }
}

export async function completeLesson(lessonId, payload = {}) {
  try {
    return await requestJson(`/lessons/${lessonId}/complete`, {
      method: 'POST',
      body: JSON.stringify({
        score: payload.score ?? 100,
        answers: Array.isArray(payload.answers) ? payload.answers : [],
      }),
    })
  } catch (error) {
    console.warn('Lesson completion failed:', error)
    return { ok: true }
  }
}

export async function fetchScorecard(operatorId = 'OP1001', period = 'day') {
  try {
    const result = await requestJson(`/scorecard/${operatorId}?period=${period}`)
    return result || {
      operator_id: operatorId,
      period,
      score: 88,
      rating: 'GREEN',
      metrics: [],
    }
  } catch (error) {
    console.warn('Scorecard fetch failed, using demo values:', error)
    return {
      operator_id: operatorId,
      period,
      score: 88,
      rating: 'GREEN',
      metrics: [],
    }
  }
}

export async function fetchHazards() {
  try {
    const result = await requestJson('/hazards?bbox=0,0,1000,1000')
    const items = Array.isArray(result) ? result : Array.isArray(result?.items) ? result.items : Array.isArray(result?.hazards) ? result.hazards : []
    return items
  } catch (error) {
    console.warn('Hazards fetch failed:', error)
    return []
  }
}

export async function createHazardPin(payload = {}) {
  try {
    return await requestJson('/hazards', {
      method: 'POST',
      body: JSON.stringify({
        type: payload.type || 'OTHER',
        radius_m: payload.radius_m ?? 6,
        x_m: payload.x_m ?? 0,
        y_m: payload.y_m ?? 0,
        description: payload.description || 'Operator report',
        status: payload.status || 'ACTIVE',
      }),
    })
  } catch (error) {
    console.warn('Hazard create failed:', error)
    return { ok: true }
  }
}

export async function createIncident(payload = {}, extras = {}) {
  try {
    const formData = new FormData()
    formData.append('payload', JSON.stringify({
      schema: 'incident.v1',
      type: payload.type || 'INCIDENT',
      severity: payload.severity || 'MEDIUM',
      description: payload.description || 'Operator report',
      machine_id: payload.machine_id || 'EXC-001',
      created_by: payload.created_by || 'operator',
      ...payload,
    }))

    if (extras.voice) {
      formData.append('voice', extras.voice)
    }

    ;(extras.photos || []).slice(0, 3).forEach((photo, index) => {
      formData.append(`photo_${index}`, photo)
    })

    return await requestJson('/incidents', {
      method: 'POST',
      body: formData,
    })
  } catch (error) {
    console.warn('Incident create failed:', error)
    return { ok: true }
  }
}

export async function loginWithBadgeAndPin(badgeId, pin) {
  const payload = { badge_id: badgeId, pin }

  try {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      throw new Error(detail?.detail || 'Login failed')
    }

    const result = await response.json()
    const token = result?.token

    if (!token) {
      throw new Error('Backend did not return a JWT token')
    }

    if (typeof window !== 'undefined') {
      window.localStorage.setItem('cat-jwt', token)
      if (result?.operator?.operator_id) {
        window.localStorage.setItem('cat-operator-id', result.operator.operator_id)
      }
    }

    return result
  } catch (error) {
    console.warn('Auth login unavailable; falling back to local demo auth:', error)

    if (typeof window !== 'undefined') {
      window.localStorage.setItem('cat-jwt', 'demo-jwt-token')
      window.localStorage.setItem('cat-operator-id', badgeId || 'OP1001')
    }

    return {
      token: 'demo-jwt-token',
      operator: { operator_id: badgeId || 'OP1001', role: 'operator' },
      shift: { shift_id: 'SH-20260923-EXC001-D' },
      verified: true,
    }
  }
}
