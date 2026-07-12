import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import { useAuth } from './auth/AuthContext'
import AppLayout from './components/Layout'
import LoginPage from './pages/Login'
import DashboardPage from './pages/Dashboard'
import InstancesPage from './pages/Instances'
import CreateInstancePage from './pages/CreateInstance'
import InstanceDetailPage from './pages/InstanceDetail'
import CredentialsPage from './pages/Credentials'
import TrafficPage from './pages/Traffic'
import UsersPage from './pages/Users'

function PrivateRoute({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { token, user, loading } = useAuth()
  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }
  if (!token) return <Navigate to="/login" replace />
  if (adminOnly && !user?.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <PrivateRoute>
              <AppLayout />
            </PrivateRoute>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="instances" element={<InstancesPage />} />
          <Route path="instances/:region/:name" element={<InstanceDetailPage />} />
          <Route path="create" element={<CreateInstancePage />} />
          <Route path="traffic" element={<TrafficPage />} />
          <Route path="credentials" element={<CredentialsPage />} />
          <Route
            path="users"
            element={
              <PrivateRoute adminOnly>
                <UsersPage />
              </PrivateRoute>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
