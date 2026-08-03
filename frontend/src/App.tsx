import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { MedicalDashboard } from './routes/MedicalDashboard'
import { EngineerDashboard } from './routes/EngineerDashboard'
import './App.css'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/medical" replace />} />
          <Route path="medical" element={<MedicalDashboard />} />
          <Route path="engineer" element={<EngineerDashboard />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
