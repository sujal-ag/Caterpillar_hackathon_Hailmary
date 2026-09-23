import { useEffect, useMemo, useRef, useState } from 'react'

export default function ReadinessModal({ onClose, onSubmit }) {
  const [responses, setResponses] = useState({
    sleep: 'good',
    heat: 'normal',
  })
  const [reactionMs, setReactionMs] = useState(null)
  const [reactionState, setReactionState] = useState('idle')
  const readyAtRef = useRef(null)
  const timeoutRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const startReactionTest = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)

    const delay = 1200 + Math.random() * 2200
    setReactionState('waiting')
    setReactionMs(null)
    readyAtRef.current = null

    timeoutRef.current = setTimeout(() => {
      readyAtRef.current = Date.now()
      setReactionState('ready')
    }, delay)
  }

  const handleReactionTap = () => {
    if (reactionState === 'waiting') {
      setReactionState('too-soon')
      setReactionMs(0)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      return
    }

    if (reactionState === 'ready' && readyAtRef.current) {
      const measured = Math.max(180, Date.now() - readyAtRef.current)
      setReactionMs(measured)
      setReactionState('done')
      return
    }

    if (reactionState === 'done' || reactionState === 'idle' || reactionState === 'too-soon') {
      startReactionTest()
    }
  }

  const result = useMemo(() => {
    let score = 100

    if (responses.sleep === 'low') score -= 15
    if (responses.sleep === 'very-low') score -= 30

    if (responses.heat === 'warm') score -= 10
    if (responses.heat === 'hot') score -= 20

    if (reactionMs == null) {
      score -= 10
    } else if (reactionMs > 600) score -= 20
    else if (reactionMs > 400) score -= 10

    const rating = score >= 75 ? 'GREEN' : score >= 50 ? 'YELLOW' : 'RED'
    const reasons = []

    if (responses.sleep !== 'good') reasons.push('Sleep recovery')
    if (responses.heat !== 'normal') reasons.push('Heat stress')
    if (reactionMs != null && reactionMs > 600) reasons.push('Reaction time')

    return { score: Math.max(0, Math.min(100, score)), rating, reasons }
  }, [responses, reactionMs])

  const updateResponse = (key, value) => {
    setResponses((prev) => ({ ...prev, [key]: value }))
  }

  const reactionButtonLabel =
    reactionState === 'idle'
      ? 'Start reaction test'
      : reactionState === 'waiting'
        ? 'Wait for green…'
        : reactionState === 'ready'
          ? 'Tap now!'
          : reactionState === 'too-soon'
            ? 'Too soon — retry'
            : reactionMs
              ? `${Math.round(reactionMs)} ms`
              : 'Retry test'

  const reactionButtonClass =
    reactionState === 'ready' ? 'reaction-button ready' :
      reactionState === 'done' ? 'reaction-button done' :
        'reaction-button'

  return (
    <div className="modal-backdrop soft-backdrop">
      <div className="readiness-result-modal compact-readiness-modal">
        <div className="readiness-topbar">
          <button type="button" className="circle-button back-button" aria-label="Back" onClick={onClose}>
            ‹
          </button>

          <div className="readiness-header-copy">
            <h2>Operator readiness</h2>
            <p>Check before you start</p>
          </div>
        </div>

        <div className="readiness-form-section">
          <div className="readiness-input-block">
            <p className="readiness-check-label">Sleep quality</p>
            <div className="readiness-option-grid">
              {['good', 'low', 'very-low'].map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`readiness-option ${responses.sleep === value ? 'selected' : ''}`}
                  onClick={() => updateResponse('sleep', value)}
                >
                  {value === 'good' ? 'Good (7–8h)' : value === 'low' ? 'Low (<6h)' : 'Very low (<5h)'}
                </button>
              ))}
            </div>
          </div>

          <div className="readiness-input-block">
            <p className="readiness-check-label">Heat / comfort</p>
            <div className="readiness-option-grid">
              {['normal', 'warm', 'hot'].map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`readiness-option ${responses.heat === value ? 'selected' : ''}`}
                  onClick={() => updateResponse('heat', value)}
                >
                  {value === 'normal' ? 'Normal' : value === 'warm' ? 'Warm' : 'High heat'}
                </button>
              ))}
            </div>
          </div>

          <div className="readiness-input-block reaction-block">
            <p className="readiness-check-label">Reaction check</p>
            <button type="button" className={reactionButtonClass} onClick={handleReactionTap}>
              {reactionButtonLabel}
            </button>
            {reactionMs != null && <p className="reaction-time-value">Reaction time: {Math.round(reactionMs)} ms</p>}
          </div>
        </div>

        <div className="readiness-summary-box">
          <div className="summary-header">
            <span className="summary-label">Status</span>
            <span className={`summary-badge ${result.rating.toLowerCase()}`}>{result.rating}</span>
          </div>
          <div className="summary-score">{result.score}</div>
          <p className="summary-copy">
            {result.rating === 'GREEN'
              ? 'Fit for duty and ready to work.'
              : result.rating === 'YELLOW'
                ? 'Continue with caution and monitor closely.'
                : 'Take a break and retest before continuing.'}
          </p>
        </div>

        <div className="readiness-footer-note">
          <span className="info-dot">i</span>
          <p>This is advice only. It never stops your shift. Camera images are never saved.</p>
        </div>

        <button
          type="button"
          className="readiness-next-button"
          onClick={async () => {
            await onSubmit?.({ score: result.score, rating: result.rating, reasons: result.reasons })
            onClose()
          }}
        >
          Save readiness
        </button>
      </div>
    </div>
  )
}
