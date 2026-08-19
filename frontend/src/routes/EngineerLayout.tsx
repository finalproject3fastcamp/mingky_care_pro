import { Outlet } from 'react-router-dom'

export function EngineerLayout() {
  return (
    <div className="engineer-shell">
      <main className="engineer-content">
        <Outlet />
      </main>
    </div>
  )
}
