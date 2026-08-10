import type { Edge, Node } from '@xyflow/react'

export interface PortDefinition {
  name: string
  label: string
  dataType: 'string' | 'number' | 'boolean' | 'object' | 'array' | 'any'
}

export interface NodeTypeDefinition {
  type: string
  version: string
  name: string
  description: string
  category: string
  color: string
  inputs: PortDefinition[]
  outputs: PortDefinition[]
  configSchema: {
    type: string
    properties: Record<string, {
      type?: string
      title?: string
      format?: string
      enum?: Array<string | number>
      minimum?: number
      maximum?: number
    }>
    required?: string[]
  }
  defaultConfig: Record<string, unknown>
  lifecycle: 'active' | 'deprecated'
  availableForNewFlows: boolean
}

export interface JsonSchemaProperty {
  type?: 'string' | 'number' | 'integer' | 'boolean'
  title?: string
  description?: string
  default?: unknown
  enum?: Array<string | number | boolean>
  minimum?: number
  maximum?: number
  minLength?: number
}

export interface JsonSchema {
  type: 'object'
  properties: Record<string, JsonSchemaProperty>
  required?: string[]
  additionalProperties?: boolean
}

export interface WorkflowNodeData extends Record<string, unknown> {
  label: string
  nodeType: string
  nodeVersion: string
  config: Record<string, unknown>
  definition?: NodeTypeDefinition
  runtimeStatus?: string
}

export type WorkflowNode = Node<WorkflowNodeData, 'workflow'>
export type WorkflowEdge = Edge

export interface FlowContent {
  schemaVersion: number
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface FlowSummary {
  id: string
  name: string
  description: string
  status: string
  current_version: number
  row_version: number
  created_at: string
  updated_at: string
}

export interface FlowDetail extends FlowSummary {
  draft_content: FlowContent
  input_schema: JsonSchema
  config_schema: JsonSchema
  default_config: Record<string, unknown>
}

export interface FlowVersion {
  id: string
  flow_id: string
  version_number: number
  content: FlowContent
  input_schema: JsonSchema
  config_schema: JsonSchema
  default_config: Record<string, unknown>
  created_at: string
}

export interface ValidationIssue {
  code: string
  message: string
  node_id?: string
}

export interface ValidationResult {
  valid: boolean
  issues: ValidationIssue[]
}

export interface NodeRun {
  id: string
  node_id: string
  node_type: string
  node_version: string
  status: string
  attempts: number
  max_attempts: number
  input_data?: Record<string, unknown>
  output_data?: Record<string, unknown>
  error_message?: string
  available_at: string
  started_at?: string
  finished_at?: string
  callback?: {
    id: string
    status: string
    callback_url: string
    auth_mode: 'CAPABILITY_URL' | 'BEARER' | 'API_KEY_HEADER' | 'HMAC_SHA256'
    credential_alias?: string
    expires_at: string
    received_at?: string
    created_at: string
  }
}

export interface RunSummary {
  id: string
  flow_id: string
  flow_name: string
  version_number: number
  status: string
  trigger_type: 'MANUAL' | 'SCHEDULE' | 'RERUN'
  trigger_id?: string
  parent_run_id?: string
  requested_at: string
  created_at: string
  started_at?: string
  finished_at?: string
}

export interface RunDetail extends RunSummary {
  input_data: Record<string, unknown>
  flow_config: Record<string, unknown>
  output_data?: Record<string, unknown>
  error_message?: string
  cancel_requested: boolean
  source_metadata: Record<string, unknown>
  flow_content: FlowContent
  node_runs: NodeRun[]
  variables: RunVariable[]
}

export interface RunVariable {
  id: string
  name: string
  value: unknown
  value_type: string
  updated_by_node_id: string
  revision: number
  created_at: string
  updated_at: string
}

export interface FlowSchedule {
  id: string
  flow_id: string
  name: string
  cron_expression: string
  timezone: string
  version_number: number
  input_data: Record<string, unknown>
  config_overrides: Record<string, unknown>
  enabled: boolean
  next_run_at: string
  last_triggered_at?: string
  created_at: string
  updated_at: string
}

export type CredentialType = 'BEARER' | 'BASIC' | 'API_KEY_HEADER'

export interface FlowCredential {
  id: string
  flow_id: string
  alias: string
  type: CredentialType
  allowed_origins: string[]
  enabled: boolean
  revision: number
  created_at: string
  updated_at: string
}
