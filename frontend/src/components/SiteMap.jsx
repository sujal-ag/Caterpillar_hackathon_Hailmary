// Local site metres (x east, y north). Hazard pins from hazards.v1, machine from telemetry.pos.
const COLOR = {
  WORKER_ZONE: '#f59e0b',
  OVERHEAD_LINE: '#ef4444',
  SOFT_GROUND: '#a16207',
  BURIED_UTILITY: '#8b5cf6',
  TRENCH: '#f97316',
  DROP_OFF: '#dc2626',
  OTHER: '#6b7280',
}

export default function SiteMap({ hazards = [], telemetry, machineId }) {
  const pos = telemetry?.pos?.x_m != null ? telemetry.pos : null
  const pts = []
  hazards.forEach((p) => {
    const g = p.geometry
    if (g.type === 'Point') {
      const r = p.radius_m || 10
      pts.push([g.coordinates[0] - r, g.coordinates[1] - r], [g.coordinates[0] + r, g.coordinates[1] + r])
    } else g.coordinates[0].forEach((c) => pts.push(c))
  })
  if (pos) pts.push([pos.x_m - 15, pos.y_m - 15], [pos.x_m + 15, pos.y_m + 15])
  if (!pts.length) pts.push([0, 0], [100, 100])

  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const pad = 10
  const minX = Math.min(...xs) - pad
  const maxY = Math.max(...ys) + pad
  const w = Math.max(...xs) + pad - minX
  const h = maxY - (Math.min(...ys) - pad)
  const tx = (x) => x - minX
  const ty = (y) => maxY - y // SVG y grows downward

  return (
    <div className="stack gap-4">
      <div className="site-map-card">
        <div className="site-map-header">
          <p className="site-map-title">Site map</p>
          <span className="site-map-chip">{pos ? 'Live position' : 'Position unknown'}</span>
        </div>

        <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', aspectRatio: '1', background: '#1f2937', borderRadius: '0.75rem' }}>
          {hazards.map((p) =>
            p.geometry.type === 'Point' ? (
              <circle
                key={p.pin_id}
                cx={tx(p.geometry.coordinates[0])}
                cy={ty(p.geometry.coordinates[1])}
                r={p.radius_m || 10}
                fill={COLOR[p.type]}
                fillOpacity="0.3"
                stroke={COLOR[p.type]}
              />
            ) : (
              <polygon
                key={p.pin_id}
                points={p.geometry.coordinates[0].map(([x, y]) => `${tx(x)},${ty(y)}`).join(' ')}
                fill={COLOR[p.type]}
                fillOpacity="0.3"
                stroke={COLOR[p.type]}
              />
            ),
          )}
          {pos && (
            <g transform={`translate(${tx(pos.x_m)} ${ty(pos.y_m)}) rotate(${pos.heading_deg ?? 0})`}>
              <circle r={Math.max(w, h) / 60 + 1.5} fill="#38bdf8" />
              <path d={`M0 ${-(Math.max(w, h) / 25 + 3)} L2 -1 L-2 -1 Z`} fill="#38bdf8" />
            </g>
          )}
        </svg>

        <div className="site-info-grid">
          <div className="site-info-box">
            <p className="eyebrow">Machine</p>
            <strong>{machineId}</strong>
          </div>
          <div className="site-info-box">
            <p className="eyebrow">Position</p>
            <strong>{pos ? `${pos.x_m.toFixed(0)}, ${pos.y_m.toFixed(0)} m` : 'Unknown'}</strong>
          </div>
          <div className="site-info-box">
            <p className="eyebrow">Hazards</p>
            <strong>{hazards.length} active</strong>
          </div>
        </div>
      </div>

      <div className="panel panel-light">
        <p className="eyebrow">Active hazard pins</p>
        {hazards.length === 0 && <p className="muted-text">None.</p>}
        {hazards.map((p) => (
          <p key={p.pin_id} className="muted-text">
            <span style={{ color: COLOR[p.type], fontWeight: 800 }}>●</span> {p.type.replaceAll('_', ' ').toLowerCase()}
            {p.radius_m ? ` · ${p.radius_m} m` : ''} · by {p.created_by}
            {p.expires_at ? ` · until ${new Date(p.expires_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}` : ''}
          </p>
        ))}
      </div>
    </div>
  )
}
