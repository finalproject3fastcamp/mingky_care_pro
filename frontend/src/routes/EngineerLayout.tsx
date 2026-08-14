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
          <NavLink to="system" className={({ isActive }) => `engineer-nav__link${isActive ? ' active' : ''}`}>
            <span>시스템 관리</span>
            <small>가동 · 재시작 · 화재 경보</small>
          </NavLink>
          <NavLink to="waypoints" className={({ isActive }) => `engineer-nav__link${isActive ? ' active' : ''}`}>
            <span>Waypoint 관리</span>
            <small>측정 · 검사 · 주행</small>
          </NavLink>
          <NavLink to="cameras" className={({ isActive }) => `engineer-nav__link${isActive ? ' active' : ''}`}>
            <span>카메라 모니터링</span>
            <small>전방 · 후방 저FPS 영상</small>
          </NavLink>
        </nav>
      </aside>
      <main className="engineer-content">
        <Outlet />
      </main>
    </div>
  )
}
