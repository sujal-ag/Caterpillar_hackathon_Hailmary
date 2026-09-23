export default function StatusDot({ level }) {
  const norm = String(level || '').toLowerCase()
  const cls =
    norm === 'critical' || norm === 'danger'
      ? 'status-dot critical'
      : norm === 'warning' || norm === 'caution'
        ? 'status-dot warning'
        : norm === 'info'
          ? 'status-dot info'
          : 'status-dot success'

  return <span className={cls} />
}
