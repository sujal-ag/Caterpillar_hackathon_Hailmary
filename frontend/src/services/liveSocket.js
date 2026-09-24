import { WS_BASE_URL, getAuthToken } from './backendService'

/**
 * Real-time WebSocket connection to edge-api (/ws/live)
 * Follows contracts/ws.md specifications
 */
export class LiveWebSocketClient {
  constructor({
    machineId = 'EXC001',
    onSnapshot,
    onState,
    onAlert,
    onNudge,
    onTelemetry,
    onStatusChange,
  } = {}) {
    this.machineId = machineId
    this.onSnapshot = onSnapshot
    this.onState = onState
    this.onAlert = onAlert
    this.onNudge = onNudge
    this.onTelemetry = onTelemetry
    this.onStatusChange = onStatusChange

    this.socket = null
    this.reconnectTimer = null
    this.isManualClosed = false
    this.reconnectAttempts = 0
    this.status = 'disconnected' // 'connecting' | 'connected' | 'disconnected'
  }

  setStatus(status) {
    if (this.status !== status) {
      this.status = status
      this.onStatusChange?.(status)
    }
  }

  connect() {
    if (typeof window === 'undefined') return
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return
    }

    this.isManualClosed = false
    this.setStatus('connecting')

    const token = getAuthToken()
    const query = new URLSearchParams({ machine: this.machineId })
    if (token) {
      query.set('token', token)
    }

    const wsUrl = `${WS_BASE_URL}/ws/live?${query.toString()}`

    try {
      this.socket = new WebSocket(wsUrl)
    } catch (err) {
      console.warn('WebSocket connection instantiation error:', err)
      this.scheduleReconnect()
      return
    }

    this.socket.onopen = () => {
      this.reconnectAttempts = 0
      this.setStatus('connected')
    }

    this.socket.onmessage = (event) => {
      try {
        const envelope = JSON.parse(event.data)
        const { type, data, ts } = envelope

        switch (type) {
          case 'snapshot':
            // Initial truth on connect/reconnect
            this.onSnapshot?.(data, ts)
            break
          case 'state':
            // State updates (exit_checks, class, sensor_health)
            this.onState?.(data, ts)
            break
          case 'alert':
            // Alert events (action: RAISED|UPDATED|CLEARED|ACKED, alert)
            this.onAlert?.(data, ts)
            break
          case 'nudge':
            // Audio/Visual/Fullscreen nudges
            this.onNudge?.(data, ts)
            break
          case 'telemetry':
            this.onTelemetry?.(data, ts)
            break
          case 'ping':
            // Server ping keepalive
            break
          default:
            break
        }
      } catch (err) {
        console.warn('Failed to parse incoming WebSocket message:', err)
      }
    }

    this.socket.onclose = (event) => {
      this.setStatus('disconnected')
      if (!this.isManualClosed) {
        // If close code is 4401 or 4403, auth failed; otherwise reconnect
        if (event.code === 4401 || event.code === 4403) {
          console.warn(`WebSocket closed with auth error (${event.code}). Please re-login.`)
        } else {
          this.scheduleReconnect()
        }
      }
    }

    this.socket.onerror = (err) => {
      console.warn('WebSocket error observed:', err)
      this.setStatus('disconnected')
    }
  }

  scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectAttempts++
    // Exponential backoff with jitter, capped at 10 seconds
    const delay = Math.min(10000, Math.pow(1.5, Math.min(this.reconnectAttempts, 8)) * 1000)
    this.reconnectTimer = setTimeout(() => {
      this.connect()
    }, delay)
  }

  disconnect() {
    this.isManualClosed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.socket) {
      this.socket.close()
      this.socket = null
    }
    this.setStatus('disconnected')
  }
}
