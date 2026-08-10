import { ArrowRight, GitBranch, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api'
import type { FlowContent, FlowSummary } from '../types'

const starterFlow: FlowContent = {
  schemaVersion: 1,
  nodes: [
    {
      id: 'start',
      type: 'workflow',
      position: { x: 100, y: 180 },
      data: { label: 'Start', nodeType: 'start', nodeVersion: '1.0', config: {} },
    },
    {
      id: 'end',
      type: 'workflow',
      position: { x: 420, y: 180 },
      data: { label: 'End', nodeType: 'end', nodeVersion: '1.0', config: {} },
    },
  ],
  edges: [
    {
      id: 'e-start-end',
      source: 'start',
      target: 'end',
      sourceHandle: 'output',
      targetHandle: 'input',
    },
  ],
}

export function FlowListPage() {
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const load = async () => {
    setLoading(true)
    try {
      setFlows(await api.flows())
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load flows')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const createFlow = async () => {
    const name = window.prompt('Flow name', `New Automation ${flows.length + 1}`)
    if (!name?.trim()) return
    try {
      const flow = await api.createFlow(name.trim(), starterFlow)
      navigate(`/flows/${flow.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create flow')
    }
  }

  return (
    <div className="page page-narrow">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Automation workspace</p>
          <h1>Flows</h1>
          <p>Create, publish, and monitor reusable automation workflows.</p>
        </div>
        <div className="heading-actions">
          <button className="button secondary" onClick={() => void load()}><RefreshCw size={16} /> Refresh</button>
          <button className="button primary" onClick={() => void createFlow()}><Plus size={17} /> New flow</button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {loading ? (
        <div className="empty-state">Loading flows…</div>
      ) : (
        <div className="flow-grid">
          {flows.map((flow) => (
            <Link className="flow-card" to={`/flows/${flow.id}`} key={flow.id}>
              <div className="flow-card-top">
                <span className="flow-card-icon"><GitBranch size={20} /></span>
                <span className={`badge ${flow.status.toLowerCase()}`}>{flow.status}</span>
              </div>
              <h2>{flow.name}</h2>
              <p>{flow.description || 'No description provided.'}</p>
              <div className="flow-card-footer">
                <span>Version {flow.current_version || 'Draft'}</span>
                <span>Edit flow <ArrowRight size={15} /></span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

