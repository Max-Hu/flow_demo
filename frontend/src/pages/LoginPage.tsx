import { GitBranch, LogIn } from 'lucide-react'
import { type FormEvent, useState } from 'react'

import { api } from '../api'

export function LoginPage({ onLogin }: { onLogin: (username: string) => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await api.login(username, password)
      onLogin(result.username)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={(event) => void submit(event)}>
        <div className="login-brand"><span><GitBranch size={22} /></span><div><strong>FlowForge</strong><small>Automation control plane</small></div></div>
        <div><h1>Administrator login</h1><p>Sign in to manage flows, runs, schedules, and credentials.</p></div>
        {error && <div className="alert error">{error}</div>}
        <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <label>Password<input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus /></label>
        <button className="button primary full" disabled={busy || !username || !password}><LogIn size={16} /> {busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </main>
  )
}
