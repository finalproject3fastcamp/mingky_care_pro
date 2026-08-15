import { NavLink, Outlet } from 'react-router-dom'

import { ActorField } from './ActorField'

export function Layout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <img src="/favicon.svg" alt="" className="app-logo" />
          <h1>Mingky Care Pro</h1>
        </div>
        <nav className="app-primary-nav" aria-label="주요 화면">
          <NavLink to="/medical">의료진</NavLink>
          <div className="app-nav-menu">
            <NavLink to="/engineer" className="app-nav-menu__trigger">
              엔지니어
              <span aria-hidden="true">⌄</span>
            </NavLink>
            <div className="app-nav-menu__panel" aria-label="엔지니어 도구">
              <NavLink to="/engineer/events">
                <strong>수집 이벤트</strong>
                <small>로그와 상태 전이</small>
              </NavLink>
              <NavLink to="/engineer/system">
                <strong>시스템 관리</strong>
                <small>가동 · 재시작 · 화재 경보</small>
              </NavLink>
              <NavLink to="/engineer/waypoints">
                <strong>Waypoint 관리</strong>
                <small>측정 · 검사 · 주행</small>
              </NavLink>
              <NavLink to="/engineer/cameras">
                <strong>카메라 모니터링</strong>
                <small>전방 · 후방 저FPS 영상</small>
              </NavLink>
            </div>
          </div>
          <ActorField />
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
