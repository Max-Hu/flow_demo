import { ArrowRight, Play, RefreshCw, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api'
import { inputDefaults, SchemaInputFields } from '../components/SchemaInputFields'
import type { FlowSummary, FlowVersion, RunSummary } from '../types'

function formatDate(value?: string) {
  return value ? new Date(value).toLocaleString() : '—'
}

export function RunsPage() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [versions, setVersions] = useState<FlowVersion[]>([])
  const [selectedFlowId, setSelectedFlowId] = useState('')
  const [selectedVersionNumber, setSelectedVersionNumber] = useState<number | null>(null)
  const [inputData, setInputData] = useState<Record<string, unknown>>({})
  const [configOverrides, setConfigOverrides] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const [rerunningId, setRerunningId] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      const [loadedRuns, loadedFlows] = await Promise.all([api.runs(), api.flows()])
      const runnableFlows = loadedFlows.filter((flow) => flow.current_version > 0 && flow.status === 'ACTIVE')
      setRuns(loadedRuns)
      setFlows(runnableFlows)
      setSelectedFlowId((current) => (
        runnableFlows.some((flow) => flow.id === current) ? current : runnableFlows[0]?.id ?? ''
      ))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load runs')
    }
  }

  useEffect(() => { void load() }, [])

  useEffect(() => {
    if (!selectedFlowId) {
      setVersions([])
      setSelectedVersionNumber(null)
      setInputData({})
      setConfigOverrides({})
      return
    }
    void api.versions(selectedFlowId).then((items) => {
      setVersions(items)
      const latest = items[0]
      setSelectedVersionNumber(latest?.version_number ?? null)
      setInputData(latest ? inputDefaults(latest.input_schema) : {})
      setConfigOverrides(structuredClone(latest?.default_config ?? {}))
    }).catch((err: Error) => setError(err.message))
  }, [selectedFlowId])

  const selectedVersion = useMemo(
    () => versions.find((version) => version.version_number === selectedVersionNumber),
    [selectedVersionNumber, versions],
  )

  const selectVersion = (versionNumber: number) => {
    const version = versions.find((item) => item.version_number === versionNumber)
    setSelectedVersionNumber(versionNumber)
    setInputData(version ? inputDefaults(version.input_schema) : {})
    setConfigOverrides(structuredClone(version?.default_config ?? {}))
  }

  const startRun = async () => {
    if (!selectedVersion) return
    setBusy(true)
    try {
      const missing = (selectedVersion.input_schema.required ?? []).filter((name) => {
        const value = inputData[name]
        return value === undefined || value === null || value === ''
      })
      if (missing.length) throw new Error(`Complete the required fields: ${missing.join(', ')}`)
      const run = await api.createRun(selectedFlowId, inputData, selectedVersion.version_number, configOverrides)
      navigate(`/runs/${run.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start run')
    } finally {
      setBusy(false)
    }
  }

  const rerun = async (runId: string) => {
    setRerunningId(runId)
    try {
      const newRun = await api.rerunRun(runId)
      navigate(`/runs/${newRun.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not rerun workflow')
      setRerunningId(null)
    }
  }

  return (
    <div className="page page-narrow">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Execution history</p>
          <h1>Runs</h1>
          <p>Start a published version or inspect workflow results and failures.</p>
        </div>
        <button className="button secondary" onClick={() => void load()}><RefreshCw size={16} /> Refresh</button>
      </div>
      {error && <div className="alert error">{error}</div>}
      <section className="run-launch-card schema-driven">
        <div className="run-launch-copy">
          <p className="eyebrow">Manual trigger</p>
          <h2>Start a run</h2>
          <p>The selected version controls both the workflow and its generated input form.</p>
        </div>
        <label>Published flow
          <select value={selectedFlowId} onChange={(event) => setSelectedFlowId(event.target.value)}>
            {flows.map((flow) => <option key={flow.id} value={flow.id}>{flow.name}</option>)}
          </select>
        </label>
        <label>Version
          <select value={selectedVersionNumber ?? ''} onChange={(event) => selectVersion(Number(event.target.value))}>
            {versions.map((version) => <option key={version.id} value={version.version_number}>Version {version.version_number}</option>)}
          </select>
        </label>
        <div className="run-schema-form">
          {selectedVersion && <SchemaInputFields schema={selectedVersion.input_schema} value={inputData} onChange={setInputData} />}
          {selectedVersion && Object.keys(selectedVersion.config_schema.properties).length > 0 && <><p className="form-section-label">Flow configuration overrides</p><SchemaInputFields schema={selectedVersion.config_schema} value={configOverrides} onChange={setConfigOverrides} /></>}
        </div>
        <button className="button primary" disabled={busy || !selectedVersion} onClick={() => void startRun()}>
          <Play size={16} /> {busy ? 'Starting…' : 'Run now'}
        </button>
      </section>
      <div className="table-card">
        <table>
          <thead><tr><th>Flow</th><th>Version</th><th>Source</th><th>Status</th><th>Requested</th><th>Finished</th><th /></tr></thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td><strong>{run.flow_name}</strong><small>{run.id.slice(0, 8)}</small></td>
                <td>v{run.version_number}</td>
                <td><span className={`source-pill ${run.trigger_type.toLowerCase()}`}>{run.trigger_type}</span>{run.parent_run_id && <small>from {run.parent_run_id.slice(0, 8)}</small>}</td>
                <td><span className={`badge ${run.status.toLowerCase()}`}>{run.status}</span></td>
                <td>{formatDate(run.requested_at)}</td>
                <td>{formatDate(run.finished_at)}</td>
                <td><div className="run-row-actions">
                  <button className="row-action" disabled={rerunningId === run.id} onClick={() => void rerun(run.id)} title="Rerun the same version with the same input"><RotateCcw size={14} /> Rerun</button>
                  <Link className="row-link" to={`/runs/${run.id}`}>Open <ArrowRight size={15} /></Link>
                </div></td>
              </tr>
            ))}
            {!runs.length && <tr><td colSpan={7} className="empty-cell">No runs yet. Start an active flow above.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
