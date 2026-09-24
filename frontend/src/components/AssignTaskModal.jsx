import { useState } from 'react'
import { assignTask, updateTask } from '../services/backendService'

const PRIORITY = [1, 2, 3, 4, 5].map((p) => [p, p === 1 ? '1 · Urgent' : p === 5 ? '5 · Low' : String(p)])

const pad = (n) => String(n).padStart(2, '0')
// datetime-local works in the browser's zone; the server gets an offset-aware ISO string.
function toInput(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function nextHour(offsetH = 0) {
  const d = new Date()
  d.setMinutes(0, 0, 0)
  d.setHours(d.getHours() + 1 + offsetH)
  return d
}

function Chips({ options, value, onChange }) {
  return (
    <div className="chip-row">
      {options.map(([v, label]) => (
        <button key={String(v)} type="button" className={`chip ${value === v ? 'selected' : ''}`} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  )
}

function Field({ label, children }) {
  return (
    <>
      <p className="field-label">{label}</p>
      <div style={{ padding: '0 20px' }}>{children}</div>
    </>
  )
}

// task: a task.v1 to reassign/reschedule; otherwise a new task (defaults preselect the form).
export default function AssignTaskModal({ options, operators, task, defaults = {}, onClose, onDone }) {
  const editing = Boolean(task)
  const [operatorId, setOperatorId] = useState(task?.operator_id || defaults.operatorId || '')
  const [machineId, setMachineId] = useState(task?.machine_id || defaults.machineId || '')
  const [typeId, setTypeId] = useState(task?.task_type_id || '')
  const [zone, setZone] = useState(task?.zone_id || '')
  const [qty, setQty] = useState(task?.planned_quantity ?? '')
  const [soil, setSoil] = useState(task?.soil_type || null)
  const [priority, setPriority] = useState(task?.priority ?? 3)
  const [start, setStart] = useState(toInput(task ? new Date(task.scheduled_start) : nextHour()))
  const [end, setEnd] = useState(toInput(task ? new Date(task.scheduled_end) : nextHour(2)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const machine = options.machines.find((m) => m.machine_id === machineId)
  const types = options.task_types.filter((t) => !machine || !t.machines?.length || t.machines.includes(machine.model_id))
  const type = options.task_types.find((t) => t.task_type_id === typeId)
  const assignable = operators.filter((o) => o.role === 'operator')

  const submit = async () => {
    setError('')
    if (!operatorId || !machineId || (!editing && !typeId) || !(Number(qty) > 0) || !start || !end) {
      setError('Operator, machine, task type, quantity and times are required.')
      return
    }
    const when = { scheduled_start: new Date(start).toISOString(), scheduled_end: new Date(end).toISOString() }
    setBusy(true)
    try {
      const saved = editing
        ? await updateTask(task.task_id, {
            operator_id: operatorId,
            machine_id: machineId,
            zone_id: zone.trim() || null,
            planned_quantity: Number(qty),
            priority,
            ...when,
          })
        : await assignTask({
            operator_id: operatorId,
            machine_id: machineId,
            task_type_id: typeId,
            zone_id: zone.trim() || null,
            planned_quantity: Number(qty),
            soil_type: soil,
            priority,
            ...when,
          })
      const name = operators.find((o) => o.operator_id === operatorId)?.name || operatorId
      onDone?.(`${saved.task_type_id} ${editing ? 'updated' : 'assigned'} — ${name} on ${saved.machine_id}.`)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="exit-modal" style={{ maxHeight: '90vh', overflowY: 'auto' }}>
        <div className="exit-modal-header">
          <p className="modal-title">{editing ? 'Reassign Task' : 'Assign Task'}</p>
          <p className="modal-subtitle">
            {editing
              ? `${task.task_type_id} · only unstarted tasks can be changed.`
              : 'Goes on the machine’s shift for that day. The operator sees it on login.'}
          </p>
        </div>

        <Field label="Operator">
          <select className="auth-text-input" value={operatorId} onChange={(e) => setOperatorId(e.target.value)}>
            <option value="">Select operator</option>
            {assignable.map((o) => (
              <option key={o.operator_id} value={o.operator_id}>
                {o.name} · {o.operator_id} · {(o.certified_families || []).join(', ') || 'no certification'}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Machine">
          <select
            className="auth-text-input"
            value={machineId}
            onChange={(e) => {
              setMachineId(e.target.value)
              if (!editing) setTypeId('')
            }}
          >
            <option value="">Select machine</option>
            {options.machines.map((m) => (
              <option key={m.machine_id} value={m.machine_id}>
                {m.machine_id} · {m.model_id} · {m.family.replace('_', ' ').toLowerCase()}
                {m.status !== 'ACTIVE' ? ` · ${m.status}` : ''}
              </option>
            ))}
          </select>
        </Field>

        {!editing && (
          <>
            <p className="field-label">Task type</p>
            {machine ? (
              <Chips options={types.map((t) => [t.task_type_id, t.name])} value={typeId} onChange={setTypeId} />
            ) : (
              <p className="muted-text" style={{ padding: '0 20px', marginTop: 0 }}>Pick a machine first.</p>
            )}
          </>
        )}

        <Field label={`Planned quantity${type || task ? ` (${(type || task).unit})` : ''}`}>
          <input className="auth-text-input" type="number" min="0" step="any" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
        </Field>

        <Field label="Zone (optional)">
          <input className="auth-text-input" list="zone-options" maxLength={64} value={zone} onChange={(e) => setZone(e.target.value)} placeholder="e.g. Z-NORTH-CUT" />
          <datalist id="zone-options">
            {options.zones.map((z) => <option key={z} value={z} />)}
          </datalist>
        </Field>

        {!editing && (
          <>
            <p className="field-label">Soil / material (optional)</p>
            <Chips
              options={[[null, 'Unknown'], ...options.soil_types.map((s) => [s, s.replace('_', ' ').toLowerCase()])]}
              value={soil}
              onChange={setSoil}
            />
          </>
        )}

        <p className="field-label">Priority</p>
        <Chips options={PRIORITY} value={priority} onChange={setPriority} />

        <Field label="Start">
          <input className="auth-text-input" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="End">
          <input className="auth-text-input" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>

        {error && <p style={{ color: '#dc2626', padding: '12px 20px 0', fontSize: '0.85rem' }}>{error}</p>}

        <div className="modal-actions" style={{ display: 'flex', gap: '10px', padding: '14px 16px 20px' }}>
          <button type="button" onClick={onClose} className="btn-small" style={{ flex: 1, padding: '16px' }}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={busy} className="primary-action enabled" style={{ flex: 2 }}>
            {busy ? 'Saving…' : editing ? 'Save changes' : 'Assign task'}
          </button>
        </div>
      </div>
    </div>
  )
}
