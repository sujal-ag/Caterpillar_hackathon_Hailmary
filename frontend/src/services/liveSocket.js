import { WS_BASE_URL, getAuthToken } from './backendService'

/**
 * /ws/live client (contracts/ws.md). `handlers[type](data, ts)` for every envelope type:
 * snapshot, state, telemetry, alert, nudge, event, hazards, sync, lesson, eta, health, ping.
 * Reconnects with backoff; each reconnect gets a fresh `snapshot` (current truth).
 * Close 4401/4403/4404 = auth or binding problem: no reconnect, `onAuthError(code)`.
 */
export class LiveWebSocketClient {
  constructor({ machineId, handlers = {}, onStatusChange, onAuthError }) {
    this.machineId = machineId
    this.handlers = handlers
    this.onStatusChange = onStatusChange
    this.onAuthError = onAuthError
    this.socket = null
    this.reconnectTimer = null
    this.closed = false
    this.attempts = 0
  }

  connect() {
    this.closed = false
    this.onStatusChange?.('connecting')
    const query = new URLSearchParams({ machine: this.machineId })
    const token = getAuthToken()
    if (token) query.set('token', token)

    const socket = new WebSocket(`${WS_BASE_URL}/ws/live?${query}`)
    this.socket = socket

    socket.onopen = () => {
      this.attempts = 0
      this.onStatusChange?.('connected')
    }
    socket.onmessage = (event) => {
      let envelope
      try {
        envelope = JSON.parse(event.data)
      } catch {
        return
      }
      this.handlers[envelope.type]?.(envelope.data, envelope.ts)
    }
    socket.onclose = (event) => {
      if (this.socket !== socket) return
      this.onStatusChange?.('disconnected')
      if (this.closed) return
      if ([4401, 4403, 4404].includes(event.code)) this.onAuthError?.(event.code)
      else this.scheduleReconnect()
    }
  }

  scheduleReconnect() {
    clearTimeout(this.reconnectTimer)
    this.attempts += 1
    const delay = Math.min(10000, 1.5 ** Math.min(this.attempts, 8) * 1000)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  disconnect() {
    this.closed = true
    clearTimeout(this.reconnectTimer)
    this.socket?.close()
    this.socket = null
  }
}
