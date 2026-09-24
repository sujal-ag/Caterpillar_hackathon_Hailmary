// Text comes from the contract (contracts/ws.md): contracts/i18n/en.json[message_key] + slots.
import MESSAGES from '../../../contracts/i18n/en.json'

export function formatMessage(key, slots = {}) {
  if (!key) return ''
  let text = MESSAGES[key] || key
  Object.entries(slots || {}).forEach(([name, value]) => {
    text = text.replaceAll(`{${name}}`, String(value ?? '—'))
  })
  return text
}
