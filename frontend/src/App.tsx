import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { MedicalDashboard } from './routes/MedicalDashboard'
import { EngineerDashboard } from './routes/EngineerDashboard'
import { EngineerLayout } from './routes/EngineerLayout'
import { WaypointDashboard } from './routes/WaypointDashboard'
import { CameraDashboard } from './routes/CameraDashboard'
import { SystemDashboard } from './routes/SystemDashboard'
import { FleetDashboard } from './routes/FleetDashboard'
import { PharmacyDashboard } from './routes/PharmacyDashboard'
import './App.css'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/medical" replace />} />
          <Route path="medical" element={<MedicalDashboard />} />
          {/* 담당 로봇 화면은 별도 URL 을 갖는다. 새로고침·뒤로가기가 그대로
              동작하고, 로봇별로 탭을 따로 띄워둘 수 있다. */}
          <Route path="medical/:robotId" element={<MedicalDashboard />} />
          {/* 약국 조제 — 약사용. 의료진·엔지니어와 독립한 최상위 화면. */}
          <Route path="pharmacy" element={<PharmacyDashboard />} />
          <Route path="engineer" element={<EngineerLayout />}>
            <Route index element={<Navigate to="events" replace />} />
            {/* SLO 가 맨 위에 오는 화면이라 엔지니어 탭의 첫 항목이다. */}
            <Route path="fleet" element={<FleetDashboard />} />
            <Route path="events" element={<EngineerDashboard />} />
            <Route path="system" element={<SystemDashboard />} />
            <Route path="waypoints" element={<WaypointDashboard />} />
            <Route path="cameras" element={<CameraDashboard />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
