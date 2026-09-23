export default function StatusDot({ level }) {
  const cls =
    level === 'critical'
      ? 'status-dot critical'
      : level === 'warning'
        ? 'status-dot warning'
        : level === 'info'
          ? 'status-dot info'
          : 'status-dot success'

  return <span className={cls} />
}
