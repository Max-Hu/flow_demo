import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
} from '@xyflow/react'
import { ArrowLeft, Ban, CirclePlay, Database, RefreshCw, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { API_URL, api } from '../api'
import { WorkflowNode } from '../components/WorkflowNode'
import { RuntimeEdge, type RuntimeEdgeState, type RuntimeEdgeType } from '../components/RuntimeEdge'
import type { NodeRun, NodeTypeDefinition, RunDetail, WorkflowNode as WorkflowNodeType } from '../types'

const nodeTypes = { workflow: WorkflowNode }
const edgeTypes = { runtime: RuntimeEdge }
const terminalStatuses = new Set(['SUCCESS', 'FAILED', 'CANCELLED'])
const currentNodeStatuses = new Set([
  'READY', 'RUNNING', 'RETRY_WAIT', 'POLL_WAIT', 'WAITING', 'WAITING_CALLBACK',
])

function duration(node?: NodeRun): string {
  if (!node?.started_at) return '—'
  const end = node.finished_at ? new Date(node.finished_at).getTime() : Date.now()
  return `${Math.max(0, (end - new Date(node.started_at).getTime()) / 1000).toFixed(1)}s`
}

export function RunPage() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const [run, setRun] = useState<RunDetail | null>(null)
  const [definitions, setDefinitions] = useState<NodeTypeDefinition[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [rerunning, setRerunning] = useState(false)
  const [decision, setDecision] = useState('APPROVED')
  const [comment, setComment] = useState('')
  const [detailView, setDetailView] = useState<'overview' | 'variables'>('overview')

  const refresh = useCallback(async () => {
    try {
      setRun(await api.run(runId))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the run')
    }
  }, [runId])

  useEffect(() => {
    void Promise.all([api.run(runId), api.nodeTypes()]).then(([loadedRun, loadedDefinitions]) => {
      setRun(loadedRun)
      setDefinitions(loadedDefinitions)
    }).catch((err: Error) => setError(err.message))
  }, [runId])

  useEffect(() => {
    if (!run || terminalStatuses.has(run.status)) return
    const source = new EventSource(`${API_URL}/runs/${runId}/events`, { withCredentials: true })
    source.addEventListener('workflow', () => { void refresh() })
    source.onerror = () => {
      source.close()
      setTimeout(() => void refresh(), 1200)
    }
    return () => source.close()
  }, [refresh, run?.status, runId])

  const definitionMap = useMemo(
    () => new Map(definitions.map((item) => [`${item.type}@${item.version}`, item])),
    [definitions],
  )
  const runMap = useMemo(
    () => new Map(run?.node_runs.map((item) => [item.node_id, item]) ?? []),
    [run],
  )

  const nodes = useMemo<WorkflowNodeType[]>(() => run?.flow_content.nodes.map((node) => ({
    ...node,
    type: 'workflow',
    draggable: false,
    selectable: true,
    data: {
      ...node.data,
      definition: definitionMap.get(`${node.data.nodeType}@${node.data.nodeVersion}`),
      runtimeStatus: runMap.get(node.id)?.status ?? 'PENDING',
    },
  })) ?? [], [definitionMap, run, runMap])

  const edges = useMemo<RuntimeEdgeType[]>(() => run?.flow_content.edges.map((edge) => {
    const sourceRun = runMap.get(edge.source)
    const targetRun = runMap.get(edge.target)
    let branchActive = true
    if (sourceRun?.node_type === 'condition' && sourceRun.status === 'SUCCESS') {
      branchActive = sourceRun.output_data?.branch === edge.sourceHandle
    }
    let runtimeState: RuntimeEdgeState = 'pending'
    if (!branchActive || sourceRun?.status === 'SKIPPED' || targetRun?.status === 'SKIPPED') {
      runtimeState = 'skipped'
    } else if (targetRun?.status === 'FAILED') {
      runtimeState = 'failed'
    } else if (targetRun?.status === 'WAITING' || targetRun?.status === 'WAITING_CALLBACK') {
      runtimeState = 'waiting'
    } else if (targetRun?.status === 'POLL_WAIT') {
      runtimeState = 'polling'
    } else if (targetRun && currentNodeStatuses.has(targetRun.status)) {
      runtimeState = 'active'
    } else if (sourceRun?.status === 'SUCCESS' && targetRun?.status === 'SUCCESS') {
      runtimeState = 'completed'
    }
    const color = runtimeState === 'completed' ? '#10b981'
      : runtimeState === 'active' ? '#2563eb'
        : runtimeState === 'polling' ? '#d97706'
        : runtimeState === 'waiting' ? '#db2777'
          : runtimeState === 'failed' ? '#ef4444' : '#a8b2c1'
    return {
      ...edge,
      type: 'runtime',
      animated: false,
      data: { runtimeState },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
    }
  }) ?? [], [run, runMap])

  const currentSteps = useMemo(() => nodes.filter((node) => (
    currentNodeStatuses.has(String(node.data.runtimeStatus ?? ''))
  )), [nodes])
  const currentStepText = currentSteps.length
    ? currentSteps.map((node) => {
      const nodeRun = runMap.get(node.id)
      return nodeRun?.status === 'POLL_WAIT'
        ? `${node.data.label} · poll ${nodeRun.attempts}/${nodeRun.max_attempts}`
        : nodeRun?.status === 'WAITING_CALLBACK'
          ? `${node.data.label} · waiting for callback`
        : node.data.label
    }).join(', ')
    : run?.status === 'SUCCESS' ? 'Run completed'
      : run?.status === 'FAILED' ? 'Run failed'
        : run?.status === 'CANCELLED' ? 'Run cancelled' : 'Preparing execution'

  const selectedRun = selectedId ? runMap.get(selectedId) : undefined
  const selectedNode = nodes.find((item) => item.id === selectedId)

  const cancel = async () => {
    if (!run) return
    try { setRun(await api.cancelRun(run.id)) } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not cancel run')
    }
  }

  const retry = async () => {
    if (!run || !selectedRun) return
    try { setRun(await api.retryNode(run.id, selectedRun.node_id)) } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not retry node')
    }
  }

  const rerun = async () => {
    if (!run) return
    setRerunning(true)
    try {
      const newRun = await api.rerunRun(run.id)
      navigate(`/runs/${newRun.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not rerun workflow')
      setRerunning(false)
    }
  }

  const resume = async () => {
    if (!run || !selectedRun) return
    try {
      setRun(await api.resumeNode(run.id, selectedRun.node_id, decision, comment))
      setComment('')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not continue the run') }
  }

  if (!run) return <div className="empty-state full-page">{error || 'Loading run…'}</div>

  return (
    <div className="run-page">
      <div className="run-toolbar">
        <div className="toolbar-title">
          <Link to="/runs" className="icon-button"><ArrowLeft size={18} /></Link>
          <div><strong>{run.flow_name}</strong><small>Run {run.id.slice(0, 8)} · Version {run.version_number} · {run.trigger_type}</small></div>
        </div>
        <span className={`run-status ${run.status.toLowerCase()}`}><span />{run.status}</span>
        <div className="toolbar-actions">
          <button className="button secondary" onClick={() => void refresh()}><RefreshCw size={16} /> Refresh</button>
          {terminalStatuses.has(run.status) && <button className="button primary" disabled={rerunning} onClick={() => void rerun()} title="Use the same published version and input"><RotateCcw size={16} /> {rerunning ? 'Starting…' : 'Run again'}</button>}
          {!terminalStatuses.has(run.status) && <button className="button danger" onClick={() => void cancel()}><Ban size={16} /> Cancel run</button>}
        </div>
      </div>
      {error && <div className="floating-error">{error}</div>}
      <div className="run-layout">
        <div className="run-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodeClick={(_, node) => { setSelectedId(node.id); setDetailView('overview') }}
            onPaneClick={() => setSelectedId(null)}
            nodesConnectable={false}
            elementsSelectable
            fitView
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.3} color="#cbd5e1" />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(node) => {
              const status = String(node.data?.runtimeStatus ?? '')
              return status === 'SUCCESS' ? '#10b981' : status === 'FAILED' ? '#ef4444' : '#94a3b8'
            }} />
          </ReactFlow>
          <div className={`execution-progress-overlay ${run.status.toLowerCase()}`}>
            <span className="execution-progress-pulse" />
            <div><small>Current step</small><strong>{currentStepText}</strong></div>
          </div>
          <div className="execution-legend">
            <span><i className="completed" /> Completed</span>
            <span><i className="active" /> Active</span>
            <span><i className="waiting" /> Waiting</span>
            <span><i className="polling" /> Polling</span>
            <span><i className="pending" /> Pending</span>
          </div>
        </div>
        <aside className="run-details">
          <div className="panel-title"><strong>Run details</strong><span>Live execution state</span></div>
          <div className="run-detail-tabs">
            <button className={detailView === 'overview' ? 'active' : ''} onClick={() => setDetailView('overview')}>Overview</button>
            <button className={detailView === 'variables' ? 'active' : ''} onClick={() => setDetailView('variables')}><Database size={13} /> Variables <span>{run.variables.length}</span></button>
          </div>
          {detailView === 'variables' ? (
            <div className="variable-list">
              {run.variables.map((variable) => <article className="variable-card" key={variable.id}>
                <div className="variable-heading"><div><strong>{variable.name}</strong><small>{variable.value_type} · revision {variable.revision}</small></div><span>by {variable.updated_by_node_id}</span></div>
                <pre>{JSON.stringify(variable.value, null, 2)}</pre>
                <small>Updated {new Date(variable.updated_at).toLocaleString()}</small>
              </article>)}
              {!run.variables.length && <div className="panel-placeholder">No run variables have been written yet. Add a Set Variable node to this flow.</div>}
            </div>
          ) : !selectedRun ? (
            <>
              <div className="summary-grid">
                <div><span>Run source</span><p className="source-summary"><strong>{run.trigger_type}</strong>{run.trigger_id && <> · {run.trigger_id.slice(0, 8)}</>}{run.parent_run_id && <> · parent {run.parent_run_id.slice(0, 8)}</>}</p><pre>{JSON.stringify(run.source_metadata, null, 2)}</pre></div>
                <div><span>Input</span><pre>{JSON.stringify(run.input_data, null, 2)}</pre></div>
                <div><span>Flow configuration snapshot</span><pre>{JSON.stringify(run.flow_config, null, 2)}</pre></div>
                <div><span>Output</span><pre>{JSON.stringify(run.output_data ?? {}, null, 2)}</pre></div>
              </div>
              {run.error_message && <div className="alert error">{run.error_message}</div>}
              <p className="panel-placeholder">Select a node to inspect its input, output, and attempts.</p>
            </>
          ) : (
            <div className="node-inspector">
              <div className="inspector-heading"><div><strong>{selectedNode?.data.label}</strong><small>{selectedRun.node_type}@{selectedRun.node_version}</small></div><span className={`badge ${selectedRun.status.toLowerCase()}`}>{selectedRun.status}</span></div>
              <dl>
                <div><dt>Attempts</dt><dd>{selectedRun.attempts} / {selectedRun.max_attempts}</dd></div>
                <div><dt>Duration</dt><dd>{duration(selectedRun)}</dd></div>
              </dl>
              {selectedRun.status === 'FAILED' && <button className="button secondary full" onClick={() => void retry()}><RotateCcw size={16} /> Retry node</button>}
              {selectedRun.status === 'WAITING' && <div className="manual-resume-form">
                <div className="alert waiting">This node is waiting for an operator.</div>
                <label>Decision<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="APPROVED">Approved</option><option value="REJECTED">Rejected</option><option value="CONTINUE">Continue</option></select></label>
                <label>Comment<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Optional audit comment" /></label>
                <button className="button primary full" onClick={() => void resume()}><CirclePlay size={16} /> Continue execution</button>
              </div>}
              {selectedRun.status === 'WAITING_CALLBACK' && selectedRun.callback && <div className="callback-wait-panel">
                <div className="alert callback-waiting">
                  Waiting for an external JSON callback. The worker has been released.
                </div>
                <label>Callback URL</label>
                <pre>{selectedRun.callback.callback_url}</pre>
                <dl>
                  <div><dt>Authentication</dt><dd>{selectedRun.callback.auth_mode}</dd></div>
                  <div><dt>Credential</dt><dd>{selectedRun.callback.credential_alias ?? 'Capability URL only'}</dd></div>
                  <div><dt>Expires</dt><dd>{new Date(selectedRun.callback.expires_at).toLocaleString()}</dd></div>
                </dl>
                <label>Example request</label>
                <pre>{`curl -X POST "${selectedRun.callback.callback_url}" \\\n  -H "Content-Type: application/json" \\\n  -H "Idempotency-Key: callback-001"${selectedRun.callback.auth_mode === 'BEARER' ? ' \\\n  -H "Authorization: Bearer <credential token>"' : selectedRun.callback.auth_mode === 'API_KEY_HEADER' ? ' \\\n  -H "<configured API key header>: <credential value>"' : selectedRun.callback.auth_mode === 'HMAC_SHA256' ? ' \\\n  -H "X-FlowForge-Signature: sha256=<HMAC of raw body>"' : ''} \\\n  -d '{"approved":true,"message":"Callback received"}'`}</pre>
              </div>}
              {selectedRun.status === 'POLL_WAIT' && <div className="alert polling">
                Waiting for the next HTTP poll at {new Date(selectedRun.available_at).toLocaleString()}.
                The worker has been released.
              </div>}
              {selectedRun.error_message && <div className="alert error">{selectedRun.error_message}</div>}
              <label>Input</label><pre>{JSON.stringify(selectedRun.input_data ?? {}, null, 2)}</pre>
              <label>Output</label><pre>{JSON.stringify(selectedRun.output_data ?? {}, null, 2)}</pre>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
