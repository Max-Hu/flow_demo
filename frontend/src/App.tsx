import { Activity, GitBranch, History, LogOut } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { DesignerPage } from './pages/DesignerPage'
import { api } from './api'
import { FlowListPage } from './pages/FlowListPage'
import { LoginPage } from './pages/LoginPage'
import { RunPage } from './pages/RunPage'
import { RunsPage } from './pages/RunsPage'

export function App() {
  const [username, setUsername] = useState<string | null>(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    void api.me().then((result) => setUsername(result.username)).catch(() => setUsername(null)).finally(() => setChecking(false))
    const unauthorized = () => setUsername(null)
    window.addEventListener('flowforge:unauthorized', unauthorized)
    return () => window.removeEventListener('flowforge:unauthorized', unauthorized)
  }, [])

  if (checking) return <div className="app-loading">Loading FlowForge…</div>
  if (!username) return <LoginPage onLogin={setUsername} />

  return (
    <div className="app-shell">
      <header className="app-header">
        <NavLink to="/flows" className="brand">
          <span className="brand-mark"><GitBranch size={20} /></span>
          <span>
            <strong>FlowForge</strong>
            <small>Automation MVP</small>
          </span>
        </NavLink>
        <nav className="main-nav">
          <NavLink to="/flows"><Activity size={17} /> Flows</NavLink>
          <NavLink to="/runs"><History size={17} /> Runs</NavLink>
        </nav>
        <div className="header-account"><div className="environment-pill"><span /> Local environment</div><small>{username}</small><button className="icon-button" title="Sign out" onClick={() => void api.logout().finally(() => setUsername(null))}><LogOut size={16} /></button></div>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/flows" element={<FlowListPage />} />
          <Route path="/flows/:flowId" element={<DesignerPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="*" element={<Navigate to="/flows" replace />} />
        </Routes>
      </main>
    </div>
  )
}
