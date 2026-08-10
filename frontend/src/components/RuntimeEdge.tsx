import { BaseEdge, getBezierPath, type Edge, type EdgeProps } from '@xyflow/react'

export type RuntimeEdgeState = 'pending' | 'active' | 'waiting' | 'polling' | 'completed' | 'failed' | 'skipped'

export interface RuntimeEdgeData extends Record<string, unknown> {
  runtimeState: RuntimeEdgeState
}

export type RuntimeEdgeType = Edge<RuntimeEdgeData, 'runtime'>

const colors: Record<RuntimeEdgeState, string> = {
  pending: '#a8b2c1',
  active: '#2563eb',
  waiting: '#db2777',
  polling: '#d97706',
  completed: '#10b981',
  failed: '#ef4444',
  skipped: '#cbd5e1',
}

export function RuntimeEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps<RuntimeEdgeType>) {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.35,
  })
  const state = data?.runtimeState ?? 'pending'
  const color = colors[state]
  const moving = state === 'active' || state === 'waiting' || state === 'polling'

  return (
    <g className={`runtime-edge runtime-edge-${state}`} data-edge-state={state}>
      {moving && <path className="runtime-edge-glow" d={edgePath} stroke={color} />}
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: moving ? 2.3 : state === 'completed' ? 2 : 1.5,
          strokeDasharray: state === 'skipped' ? '5 7' : undefined,
          opacity: state === 'skipped' ? 0.42 : state === 'pending' ? 0.66 : 1,
        }}
      />
      {moving && (
        <circle r="3.5" fill={color} stroke="white" strokeWidth="1.5" className="runtime-edge-particle">
          <animateMotion dur={state === 'polling' ? '2.1s' : state === 'waiting' ? '1.7s' : '1.15s'} repeatCount="indefinite" path={edgePath} />
        </circle>
      )}
    </g>
  )
}
