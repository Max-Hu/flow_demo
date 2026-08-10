import dagre from '@dagrejs/dagre'

import type { WorkflowEdge, WorkflowNode } from '../types'

const NODE_WIDTH = 220
const NODE_HEIGHT = 92

export function layoutFlow(nodes: WorkflowNode[], edges: WorkflowEdge[]): WorkflowNode[] {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 60, marginx: 40, marginy: 40 })
  nodes.forEach((node) => graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }))
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target))
  dagre.layout(graph)
  return nodes.map((node) => {
    const position = graph.node(node.id)
    return {
      ...node,
      position: { x: position.x - NODE_WIDTH / 2, y: position.y - NODE_HEIGHT / 2 },
    }
  })
}

