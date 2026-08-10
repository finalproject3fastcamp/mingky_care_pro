import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { MedicalDashboard } from './routes/MedicalDashboard'
import { EngineerDashboard } from './routes/EngineerDashboard'
import { EngineerLayout } from './routes/EngineerLayout'
import { WaypointDashboard } from './routes/WaypointDashboard'
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
          <Route path="engineer" element={<EngineerLayout />}>
            <Route index element={<Navigate to="events" replace />} />
            <Route path="events" element={<EngineerDashboard />} />
            <Route path="waypoints" element={<WaypointDashboard />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
