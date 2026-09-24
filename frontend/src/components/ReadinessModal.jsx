import { useEffect, useRef, useState } from 'react'
import { submitReadiness } from '../services/backendService'
import { formatMessage } from '../data/i18n'

// Brief PVT-style test (HLD §7.4): 10 stimuli, random 1–3 s gaps. A reaction slower than
// 500 ms is a lapse (rules.yaml readiness.lapse_ms_gt). The server computes score + rating.
const TRIALS = 10
const LAPSE_MS = 500
const SLEEP_24 = [[8, '7–8 h+'], [6, '6 h'], [5, '5 h'], [4, '< 5 h']]
const SLEEP_48 = [[15, '14 h+'], [12, '12–13 h'], [10, '< 12 h']]
const FEEL = [[5, 'Great'], [4, 'Good'], [3, 'OK'], [2, 'Tired'], [1, 'Unwell']]

const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length
const sd = (xs) => Math.sqrt(mean(xs.map((x) => (x - mean(xs)) ** 2)))

function Options({ options, value, onChange }) {
  return (
    <div className="readiness-option-grid">
      {options.map(([v, label]) => (
        <button key={v} type="button" className={`readiness-option ${value === v ? 'selected' : ''}`} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  )
}

export default function ReadinessModal({ machineId, onClose, onSaved }) {
  const [sleep24, setSleep24] = useState(8)
  const [sleep48, setSleep48] = useState(15)
  const [feel, setFeel] = useState(4)
  const [times, setTimes] = useState([])
  const [phase, setPhase] = useState('idle') // idle | waiting | ready | done
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const readyAt = useRef(0)
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  const arm = () => {
    setPhase('waiting')
    timer.current = setTimeout(() => {
      readyAt.current = performance.now()
      setPhase('ready')
    }, 1000 + Math.random() * 2000)
  }

  const tap = () => {
    if (phase === 'idle' || phase === 'done') {
      setTimes([])
      arm()
    } else if (phase === 'waiting') {
      clearTimeout(timer.current) // false start: re-arm this trial
      arm()
    } else {
      const next = [...times, performance.now() - readyAt.current]
      setTimes(next)
      if (next.length >= TRIALS) setPhase('done')
      else arm()
    }
  }

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await submitReadiness({
        machine_id: machineId,
        sleep_last_24h_h: sleep24,
        sleep_last_48h_h: Math.max(sleep48, sleep24),
        feel_score: feel,
        rt_mean_ms: Math.round(mean(times)),
        rt_sd_ms: Math.round(sd(times)),
        rt_lapses: times.filter((t) => t > LAPSE_MS).length,
        camera_used: false,
      })
      setResult(res)
      onSaved?.(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const label = {
    idle: 'Start reaction test',
    waiting: `Wait for green… (${times.length + 1}/${TRIALS})`,
    ready: 'Tap now!',
    done: `${Math.round(mean(times))} ms avg — tap to redo`,
  }[phase]

  return (
    <div className="modal-backdrop soft-backdrop">
      <div className="readiness-result-modal compact-readiness-modal" style={{ maxHeight: '92vh', overflowY: 'auto' }}>
        <div className="readiness-topbar">
          <button type="button" className="circle-button back-button" aria-label="Back" onClick={onClose}>‹</button>
          <div className="readiness-header-copy">
            <h2>Operator readiness</h2>
            <p>Check before you start</p>
          </div>
        </div>

        {result ? (
          <div className="readiness-summary-box">
            <div className="summary-header">
              <span className="summary-label">Status</span>
              <span className={`summary-badge ${result.rating.toLowerCase()}`}>{result.rating}</span>
            </div>
            <div className="summary-score">{result.score}</div>
            <ul className="summary-copy" style={{ paddingLeft: '1.1rem' }}>
              {result.reasons.length === 0 && <li>No concerns found.</li>}
              {result.reasons.map((r) => <li key={r}>{formatMessage(r)}</li>)}
            </ul>
          </div>
        ) : (
          <div className="readiness-form-section">
            <div className="readiness-input-block">
              <p className="readiness-check-label">Sleep in the last 24 h</p>
              <Options options={SLEEP_24} value={sleep24} onChange={setSleep24} />
            </div>
            <div className="readiness-input-block">
              <p className="readiness-check-label">Sleep in the last 48 h</p>
              <Options options={SLEEP_48} value={sleep48} onChange={setSleep48} />
            </div>
            <div className="readiness-input-block">
              <p className="readiness-check-label">How do you feel?</p>
              <Options options={FEEL} value={feel} onChange={setFeel} />
            </div>
            <div className="readiness-input-block reaction-block">
              <p className="readiness-check-label">Reaction check ({TRIALS} taps)</p>
              <button
                type="button"
                className={`reaction-button ${phase === 'ready' ? 'ready' : phase === 'done' ? 'done' : ''}`}
                onPointerDown={tap}
              >
                {label}
              </button>
            </div>
          </div>
        )}

        {error && <p style={{ color: '#dc2626', fontSize: '0.85rem' }}>{error}</p>}

        <div className="readiness-footer-note">
          <span className="info-dot">i</span>
          <p>This is advice only. It never stops your shift.</p>
        </div>

        {result ? (
          <button type="button" className="readiness-next-button" onClick={onClose}>Continue</button>
        ) : (
          <button type="button" className="readiness-next-button" disabled={phase !== 'done' || busy} onClick={save}>
            {busy ? 'Saving…' : phase === 'done' ? 'Save readiness' : 'Finish the reaction test first'}
          </button>
        )}
      </div>
    </div>
  )
}
