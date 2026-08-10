import { NavLink, Outlet } from 'react-router-dom'

export function EngineerLayout() {
  return (
    <div className="engineer-shell">
      <aside className="engineer-nav" aria-label="엔지니어 도구">
        <nav>
          <NavLink to="events" className={({ isActive }) => `engineer-nav__link${isActive ? ' active' : ''}`}>
            <span>수집 이벤트</span>
            <small>로그와 상태 전이</small>
          </NavLink>
          <NavLink to="waypoints" className={({ isActive }) => `engineer-nav__link${isActive ? ' active' : ''}`}>
            <span>Waypoint 관리</span>
            <small>측정 · 검사 · 주행</small>
          </NavLink>
        </nav>
      </aside>
      <main className="engineer-content">
        <Outlet />
      </main>
    </div>
  )
}
