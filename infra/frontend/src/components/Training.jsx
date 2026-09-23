import { useEffect, useState } from 'react'
import { LESSONS } from '../data/mockData'
import { completeLesson, fetchAssignedLessons } from '../services/backendService'

const normalizeLesson = (lesson) => {
  const title = lesson?.title || lesson?.title_key || 'Micro-lesson'
  const key = lesson?.lesson_id || lesson?.id || title

  return {
    id: key,
    title: title.replace(/^lesson\./, '').replace(/\._/g, ' ').replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase()),
    category: lesson?.tags?.[0] || 'Safety',
    duration: lesson?.duration_s ? `${Math.max(1, Math.round(lesson.duration_s / 60))} min` : lesson?.duration || '1 min',
    done: Boolean(lesson?.done || lesson?.completed),
    lessonId: lesson?.lesson_id || lesson?.id,
  }
}

export default function Training() {
  const [active, setActive] = useState(null)
  const [lessons, setLessons] = useState(LESSONS)

  useEffect(() => {
    let isMounted = true

    const load = async () => {
      const next = await fetchAssignedLessons()
      if (!isMounted) return
      if (next && next.length) {
        setLessons(next.map(normalizeLesson))
      } else {
        setLessons(LESSONS)
      }
    }

    load()
    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Micro-lessons</p>
        <p className="display-title large">Training</p>
      </div>

      <div className="lesson-list">
        {lessons.map((lesson) => (
          <div key={lesson.id} className="panel panel-light lesson-card">
            <button
              type="button"
              onClick={() => setActive(active === lesson.id ? null : lesson.id)}
              className="lesson-toggle"
            >
              <div className="lesson-main">
                <div className="lesson-title-row">
                  <p className="panel-title">{lesson.title}</p>
                  {lesson.done && <span className="done-pill">Done</span>}
                </div>
                <p className="muted-text">{lesson.category} · {lesson.duration}</p>
              </div>
              <span className="lesson-arrow">{active === lesson.id ? '▲' : '▼'}</span>
            </button>

            {active === lesson.id && (
              <div className="lesson-panel">
                <p className="lesson-content">
                  Essential steps and safety checks for <strong>{lesson.title.toLowerCase()}</strong>. Follow each step before continuing operations.
                </p>
                <button
                  type="button"
                  className="primary-lesson-button"
                  onClick={async () => {
                    await completeLesson(lesson.lessonId || lesson.id, { score: 100, answers: [] })
                    setLessons((current) => current.map((item) => item.id === lesson.id ? { ...item, done: true } : item))
                  }}
                >
                  {lesson.done ? 'Replay Lesson' : 'Start Lesson'}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
