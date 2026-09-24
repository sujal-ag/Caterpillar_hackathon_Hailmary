import { useEffect, useState } from 'react'
import { completeLesson, fetchAssignedLessons, fetchLesson } from '../services/backendService'
import { formatMessage } from '../data/i18n'

// Lessons assigned by the edge (HLD §4.11). Content is only served while the machine is
// known OFF — the server answers 409 otherwise (I5), so "deliverable" gates the Open button.
const titleOf = (lesson) => {
  const text = formatMessage(lesson.title_key)
  return text !== lesson.title_key ? text : lesson.lesson_id.replace(/^L-/, '').replaceAll('-', ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}

function LessonBody({ assignmentId, onCompleted }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [choice, setChoice] = useState(null)

  useEffect(() => {
    fetchLesson(assignmentId).then(setData).catch((e) => setError(e.message))
  }, [assignmentId])

  if (error) return <p className="muted-text" style={{ color: '#d97706' }}>{error}</p>
  if (!data) return <p className="muted-text">Loading…</p>

  const replay = data.replay
  const body = data.lesson.content_json?.body
  const answered = choice != null
  const score = replay ? (choice === replay.correct_option ? 100 : 0) : 100

  const finish = async () => {
    try {
      await completeLesson(assignmentId, score, replay ? [choice] : [])
      onCompleted()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="lesson-panel">
      {body && <p className="lesson-content">{body}</p>}
      {replay && (
        <>
          <p className="lesson-content"><strong>Replay:</strong> {replay.prompt}</p>
          <div className="btn-row" style={{ flexDirection: 'column' }}>
            {(replay.options || []).map((opt, i) => (
              <button
                key={opt}
                type="button"
                disabled={answered}
                className={`btn-small ${answered && i === replay.correct_option ? 'primary' : ''}`}
                style={{ textAlign: 'left', outline: choice === i ? '2px solid #2563eb' : 'none' }}
                onClick={() => setChoice(i)}
              >
                {opt}
              </button>
            ))}
          </div>
          {answered && <p className="lesson-content">{replay.explanation}</p>}
        </>
      )}
      <button type="button" className="primary-lesson-button" disabled={replay && !answered} onClick={finish}>
        Complete lesson
      </button>
    </div>
  )
}

export default function Training({ refreshKey }) {
  const [assignments, setAssignments] = useState(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    fetchAssignedLessons()
      .then((d) => setAssignments(d.assignments))
      .catch((e) => setError(e.message))
  }, [refreshKey, reload])

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Micro-lessons</p>
        <p className="display-title large">Training</p>
      </div>

      {error && <p className="muted-text" style={{ color: '#dc2626' }}>Lessons unavailable: {error}</p>}
      {assignments?.length === 0 && <p className="muted-text">No lessons assigned. Lessons appear after a safety event.</p>}

      <div className="lesson-list">
        {(assignments || []).map((a) => {
          const done = Boolean(a.completed_at)
          return (
            <div key={a.assignment_id} className="panel panel-light lesson-card">
              <button type="button" className="lesson-toggle" onClick={() => setOpen(open === a.assignment_id ? null : a.assignment_id)}>
                <div className="lesson-main">
                  <div className="lesson-title-row">
                    <p className="panel-title">{titleOf(a.lesson)}</p>
                    {done && <span className="done-pill">Done · {a.score}</span>}
                  </div>
                  <p className="muted-text">
                    {a.reason || 'Assigned'} · {Math.max(1, Math.round(a.lesson.duration_s / 60))} min
                    {!done && !a.deliverable && ' · available when the engine is off'}
                  </p>
                </div>
                <span className="lesson-arrow">{open === a.assignment_id ? '▲' : '▼'}</span>
              </button>

              {open === a.assignment_id && (
                done ? (
                  <p className="muted-text" style={{ padding: '0.5rem 0' }}>Completed {new Date(a.completed_at).toLocaleString()}.</p>
                ) : a.deliverable ? (
                  <LessonBody
                    assignmentId={a.assignment_id}
                    onCompleted={() => {
                      setOpen(null)
                      setReload((n) => n + 1)
                    }}
                  />
                ) : (
                  <p className="muted-text" style={{ padding: '0.5rem 0', color: '#d97706' }}>
                    Not while the machine is running. Park, shut down, then open this lesson.
                  </p>
                )
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
