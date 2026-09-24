export default function Tasks({ tasks = [] }) {
  const currentTasks = tasks.length ? tasks : []

  return (
    <div className="stack gap-4">
      <div className="panel panel-dark">
        <p className="eyebrow">Shift 07:00–17:00</p>
        <p className="display-title large">Tasks</p>
      </div>

      <div className="task-list">
        {currentTasks.map((task) => (
          <div key={task.id} className="panel panel-light task-card">
            <div className="task-row-top">
              <div className="task-copy">
                <span className={`task-badge ${task.status}`}>
                  {task.status === 'in-progress' ? 'In Progress' : task.status === 'done' ? 'Complete' : 'Pending'}
                </span>
                <p className="panel-title">{task.title}</p>
                <p className="muted-text">{task.location}</p>
              </div>

              {task.eta !== '—' && (
                <div className="eta-box">
                  <p className="eyebrow">ETA</p>
                  <p className="eta-value">{task.eta}</p>
                </div>
              )}
            </div>

            {task.status !== 'pending' && (
              <div className="progress-block">
                <div className="progress-header-row">
                  <span>Progress</span>
                  <span className="progress-badge">{task.progress}% complete</span>
                </div>

                <div className="progress-hint" aria-label={`Progress ${task.progress}%`}>
                  <div className="progress-value">{task.progress}%</div>
                  <div className="progress-copy">done</div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
