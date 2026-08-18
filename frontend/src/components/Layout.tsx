import { animate, createScope, stagger } from 'animejs'
import { useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { ActorField } from './ActorField'

export function Layout() {
  const location = useLocation()
  const routeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!routeRef.current) return
    const scope = createScope({
      root: routeRef,
      mediaQueries: {
        reduceMotion: '(prefers-reduced-motion: reduce)',
      },
    }).add((self) => {
      if (self?.matches.reduceMotion) return
      animate('.route-stage > *', {
        opacity: { from: 0 },
        y: { from: 10 },
        duration: 420,
        ease: 'out(4)',
      })
      animate('.route-stage > * > :is(header, section, .card, .waypoint-workspace)', {
        opacity: { from: 0 },
        y: { from: 12 },
        delay: stagger(45, { start: 60 }),
        duration: 460,
        ease: 'out(4)',
      })
    })
    return () => scope.revert()
  }, [location.pathname])

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
              <NavLink to="/engineer/fleet">
                <strong>Fleet</strong>
                <small>완주율 · 오차 예산 · 개입</small>
              </NavLink>
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
        <div className="route-stage" ref={routeRef} key={location.pathname}>
          <Outlet />
        </div>
      </main>
    </div>
  )
}
