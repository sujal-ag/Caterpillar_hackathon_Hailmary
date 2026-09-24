import { BACKEND_ALERTS, BACKEND_STATE, BACKEND_TASKS } from './backendContract'

const normalizeAlert = (item) => {
  const { alert } = item
  const level = alert.level.toLowerCase()

  return {
    id: alert.alert_id,
    level,
    title: alert.subject,
    message: alert.message_key,
    time: 'Now',
    rule: alert.rule_id,
    schema: item.schema,
    action: item.action,
    messageKey: alert.message_key,
    slots: alert.slots,
  }
}

const normalizeTask = (task) => {
  const progress = task.actual_quantity != null && task.planned_quantity
    ? Math.min(100, Math.round((task.actual_quantity / task.planned_quantity) * 100))
    : task.status === 'DONE'
      ? 100
      : 0

  const status = task.status.toLowerCase().replace('_', '-')

  return {
    id: task.task_id,
    title: `${task.task_type_id} — ${task.zone_id || 'Zone'}`,
    location: task.zone_id || 'Site A',
    status,
    eta: task.prediction ? new Date(task.prediction.eta).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—',
    progress,
    schema: task.schema,
    taskId: task.task_id,
    taskType: task.task_type_id,
    prediction: task.prediction,
  }
}

export const STATE = BACKEND_STATE
export const ALERTS = BACKEND_ALERTS.map(normalizeAlert)
export const TASKS = BACKEND_TASKS.map(normalizeTask)

export const LESSONS = [
  { id: 'l1', title: 'Safe Exit Procedure', duration: '60 sec', done: false, category: 'Safety' },
  { id: 'l2', title: 'Three-Point Contact', duration: '45 sec', done: true, category: 'Safety' },
  { id: 'l3', title: 'Idle Fuel Management', duration: '90 sec', done: false, category: 'Efficiency' },
  { id: 'l4', title: 'Coupler Confirm Protocol', duration: '60 sec', done: false, category: 'Equipment' },
]

export const EXIT_CHECKS = [
  { id: 'c1', label: 'Bucket lowered to ground', done: false },
  { id: 'c2', label: 'Hydraulics locked', done: false },
  { id: 'c3', label: 'Engine stopped', done: false },
  { id: 'c4', label: 'Parking brake applied', done: false },
]

export const NAV = [
  { id: 'dashboard', label: 'Home' },
  { id: 'safety', label: 'Safety' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'training', label: 'Learn' },
  { id: 'site', label: 'Map' },
  { id: 'scorecard', label: 'Score' },
]
