import { NavLink, Outlet } from 'react-router-dom'

export function Layout() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Mingky Care Pro</h1>
        <nav>
          <NavLink to="/medical">의료진</NavLink>
          <NavLink to="/engineer">엔지니어</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
