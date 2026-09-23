export default function SiteMap() {
  return (
    <div className="stack gap-4">
      <div className="site-map-card">
        <div className="site-map-header">
          <p className="site-map-title">Site map</p>
          <span className="site-map-chip">Live view</span>
        </div>

        <div className="site-map-board">
          <div className="site-road" />
          <div className="site-route" />
          <span className="site-node safe n1" />
          <span className="site-node n2" />
          <span className="site-node safe n3" />
          <span className="site-node alert n4" />
        </div>

        <div className="site-info-grid">
          <div className="site-info-box">
            <p className="eyebrow">Zone</p>
            <strong>North pit</strong>
          </div>
          <div className="site-info-box">
            <p className="eyebrow">Status</p>
            <strong>Clear</strong>
          </div>
          <div className="site-info-box">
            <p className="eyebrow">Crew</p>
            <strong>4 onsite</strong>
          </div>
          <div className="site-info-box">
            <p className="eyebrow">Alert</p>
            <strong>1 risk</strong>
          </div>
        </div>
      </div>
    </div>
  )
}
