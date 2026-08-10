import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Activity, Braces, CheckCircle2, CircleStop, Clock3, Database, GitBranch, Globe2, Hand, RefreshCw } from 'lucide-react'

import type { WorkflowNode } from '../types'

const icons: Record<string, typeof Activity> = {
  start: Activity,
  http_request: Globe2,
  http_poll: RefreshCw,
  condition: GitBranch,
  result: Braces,
  set_variable: Database,
  manual_approval: Hand,
  delay: Clock3,
  end: CircleStop,
}

export function WorkflowNode({ data, selected, isConnectable }: NodeProps<WorkflowNode>) {
  const definition = data.definition
  const Icon = icons[data.nodeType] ?? CheckCircle2
  const status = data.runtimeStatus?.toLowerCase()

  return (
    <div
      className={`workflow-node ${selected ? 'selected' : ''} ${status ? `status-${status}` : ''}`}
      style={{ '--node-color': definition?.color ?? '#64748b' } as React.CSSProperties}
    >
      {definition?.inputs.map((input, index) => (
        <Handle
          key={input.name}
          id={input.name}
          type="target"
          position={Position.Left}
          isConnectable={isConnectable}
          style={{ top: `${((index + 1) / (definition.inputs.length + 1)) * 100}%` }}
          title={`${input.label} (${input.dataType})`}
        />
      ))}
      <div className="node-accent" />
      <div className="node-heading">
        <span className="node-icon"><Icon size={16} /></span>
        <span className="node-kind">{definition?.name ?? data.nodeType}</span>
        {data.runtimeStatus && <span className={`status-dot ${status}`} />}
      </div>
      <div className="node-label">{data.label}</div>
      {data.runtimeStatus && <div className="node-runtime-status">{data.runtimeStatus}</div>}
      {definition?.outputs.map((output, index) => (
        <Handle
          key={output.name}
          id={output.name}
          type="source"
          position={Position.Right}
          isConnectable={isConnectable}
          style={{ top: `${((index + 1) / (definition.outputs.length + 1)) * 100}%` }}
          title={`${output.label} (${output.dataType})`}
        >
          {definition.outputs.length > 1 && <span className="handle-label">{output.label}</span>}
        </Handle>
      ))}
    </div>
  )
}
