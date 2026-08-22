import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from '@xyflow/react'
import {
  ArrowLeft,
  Braces,
  CalendarClock,
  CheckCircle2,
  History,
  KeyRound,
  LayoutDashboard,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  Trash2,
  Undo2,
  Unlink,
  Upload,
  X,
} from 'lucide-react'
import { type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '../api'
import { inputDefaults, SchemaInputFields } from '../components/SchemaInputFields'
import { WorkflowNode } from '../components/WorkflowNode'
import type {
  FlowContent,
  FlowCredential,
  FlowDetail,
  FlowSchedule,
  FlowVersion,
  JsonSchema,
  JsonSchemaProperty,
  NodeTypeDefinition,
  WorkflowEdge,
  WorkflowNodeData,
  WorkflowNode as WorkflowNodeType,
} from '../types'
import { layoutFlow } from '../utils/layout'

const nodeTypes = { workflow: WorkflowNode }
const emptySchema: JsonSchema = {
  type: 'object',
  properties: {},
  required: [],
  additionalProperties: false,
}

interface EditorSnapshot {
  content: FlowContent
  inputSchema: JsonSchema
  configSchema: JsonSchema
  defaultConfig: Record<string, unknown>
}

type PanelMode = 'properties' | 'input' | 'configuration' | 'credentials' | 'versions' | 'schedules'

function serialize(nodes: WorkflowNodeType[], edges: WorkflowEdge[]): FlowContent {
  return {
    schemaVersion: 1,
    nodes: nodes.map((node) => {
      const { definition: _definition, runtimeStatus: _runtimeStatus, ...data } = node.data
      return { id: node.id, type: 'workflow', position: node.position, data }
    }),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle ?? undefined,
      targetHandle: edge.targetHandle ?? undefined,
    })),
  }
}

function documentHash(snapshot: EditorSnapshot): string {
  return JSON.stringify(snapshot)
}

function versionDiff(before?: FlowVersion, after?: FlowVersion): string[] {
  if (!before || !after) return []
  const changes: string[] = []
  const compareItems = (
    label: string,
    left: Array<{ id: string }>,
    right: Array<{ id: string }>,
  ) => {
    const leftMap = new Map(left.map((item) => [item.id, JSON.stringify(item)]))
    const rightMap = new Map(right.map((item) => [item.id, JSON.stringify(item)]))
    const added = [...rightMap.keys()].filter((id) => !leftMap.has(id))
    const removed = [...leftMap.keys()].filter((id) => !rightMap.has(id))
    const changed = [...rightMap.keys()].filter(
      (id) => leftMap.has(id) && leftMap.get(id) !== rightMap.get(id),
    )
    if (added.length) changes.push(`${label} added: ${added.join(', ')}`)
    if (removed.length) changes.push(`${label} removed: ${removed.join(', ')}`)
    if (changed.length) changes.push(`${label} changed: ${changed.join(', ')}`)
  }
  compareItems('Nodes', before.content.nodes, after.content.nodes)
  compareItems('Connections', before.content.edges, after.content.edges)
  const beforeFields = before.input_schema.properties
  const afterFields = after.input_schema.properties
  const addedFields = Object.keys(afterFields).filter((name) => !(name in beforeFields))
  const removedFields = Object.keys(beforeFields).filter((name) => !(name in afterFields))
  const changedFields = Object.keys(afterFields).filter(
    (name) => name in beforeFields && JSON.stringify(beforeFields[name]) !== JSON.stringify(afterFields[name]),
  )
  if (addedFields.length) changes.push(`Input fields added: ${addedFields.join(', ')}`)
  if (removedFields.length) changes.push(`Input fields removed: ${removedFields.join(', ')}`)
  if (changedFields.length) changes.push(`Input fields changed: ${changedFields.join(', ')}`)
  return changes
}

function DesignerCanvas() {
  const { flowId = '' } = useParams()
  const navigate = useNavigate()
  const [flow, setFlow] = useState<FlowDetail | null>(null)
  const [definitions, setDefinitions] = useState<NodeTypeDefinition[]>([])
  const [versions, setVersions] = useState<FlowVersion[]>([])
  const [schedules, setSchedules] = useState<FlowSchedule[]>([])
  const [nodes, setNodes] = useState<WorkflowNodeType[]>([])
  const [edges, setEdges] = useState<WorkflowEdge[]>([])
  const [inputSchema, setInputSchema] = useState<JsonSchema>(emptySchema)
  const [configSchema, setConfigSchema] = useState<JsonSchema>(emptySchema)
  const [defaultConfig, setDefaultConfig] = useState<Record<string, unknown>>({})
  const [credentials, setCredentials] = useState<FlowCredential[]>([])
  const [runInput, setRunInput] = useState<Record<string, unknown>>({})
  const [past, setPast] = useState<EditorSnapshot[]>([])
  const [future, setFuture] = useState<EditorSnapshot[]>([])
  const [savedHash, setSavedHash] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [panelMode, setPanelMode] = useState<PanelMode>('properties')
  const [compareFrom, setCompareFrom] = useState<number | null>(null)
  const [compareTo, setCompareTo] = useState<number | null>(null)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [scheduleName, setScheduleName] = useState('Daily automation')
  const [scheduleCron, setScheduleCron] = useState('0 9 * * *')
  const [scheduleTimezone, setScheduleTimezone] = useState('UTC')
  const [scheduleVersion, setScheduleVersion] = useState<number | null>(null)
  const [scheduleInput, setScheduleInput] = useState<Record<string, unknown>>({})
  const [credentialAlias, setCredentialAlias] = useState('partner_api')
  const [credentialType, setCredentialType] = useState<'BEARER' | 'BASIC' | 'API_KEY_HEADER'>('BEARER')
  const [credentialOrigins, setCredentialOrigins] = useState('https://partner.example.com')
  const [credentialSecret, setCredentialSecret] = useState<Record<string, string>>({})
  const historyReady = useRef(false)
  const { screenToFlowPosition, fitView } = useReactFlow()

  const definitionsByKey = useMemo(
    () => new Map(definitions.map((item) => [`${item.type}@${item.version}`, item])),
    [definitions],
  )

  const enrichNodes = useCallback((content: FlowContent, byKey = definitionsByKey) => (
    content.nodes.map((node) => ({
      ...node,
      type: 'workflow' as const,
      data: {
        ...node.data,
        definition: byKey.get(`${node.data.nodeType}@${node.data.nodeVersion}`),
      },
    }))
  ), [definitionsByKey])

  const currentSnapshot = useMemo<EditorSnapshot>(() => ({
    content: serialize(nodes, edges),
    inputSchema,
    configSchema,
    defaultConfig,
  }), [configSchema, defaultConfig, edges, inputSchema, nodes])
  const currentHash = useMemo(() => documentHash(currentSnapshot), [currentSnapshot])
  const isDirty = Boolean(flow && savedHash && currentHash !== savedHash)

  const capture = useCallback((): EditorSnapshot => ({
    content: serialize(nodes, edges),
    inputSchema: structuredClone(inputSchema),
    configSchema: structuredClone(configSchema),
    defaultConfig: structuredClone(defaultConfig),
  }), [configSchema, defaultConfig, edges, inputSchema, nodes])

  const commitHistory = useCallback(() => {
    if (!historyReady.current) return
    const snapshot = capture()
    setPast((items) => [...items, snapshot].slice(-50))
    setFuture([])
  }, [capture])

  const applySnapshot = useCallback((snapshot: EditorSnapshot) => {
    setNodes(enrichNodes(snapshot.content))
    setEdges(snapshot.content.edges)
    setInputSchema(structuredClone(snapshot.inputSchema))
    setConfigSchema(structuredClone(snapshot.configSchema))
    setDefaultConfig(structuredClone(snapshot.defaultConfig))
    setRunInput(inputDefaults(snapshot.inputSchema))
    setSelectedId(null)
    setSelectedEdgeId(null)
  }, [enrichNodes])

  const undo = useCallback(() => {
    if (!past.length) return
    const target = past[past.length - 1]
    setPast((items) => items.slice(0, -1))
    setFuture((items) => [capture(), ...items].slice(0, 50))
    applySnapshot(target)
  }, [applySnapshot, capture, past])

  const redo = useCallback(() => {
    if (!future.length) return
    const [target, ...remaining] = future
    setFuture(remaining)
    setPast((items) => [...items, capture()].slice(-50))
    applySnapshot(target)
  }, [applySnapshot, capture, future])

  const loadEditor = useCallback((loadedFlow: FlowDetail, loadedVersions: FlowVersion[], loadedDefinitions: NodeTypeDefinition[]) => {
    const byKey = new Map(
      loadedDefinitions.map((item) => [`${item.type}@${item.version}`, item]),
    )
    const schema = loadedFlow.input_schema ?? structuredClone(emptySchema)
    const loadedConfigSchema = loadedFlow.config_schema ?? structuredClone(emptySchema)
    const loadedDefaultConfig = loadedFlow.default_config ?? {}
    setFlow(loadedFlow)
    setVersions(loadedVersions)
    setNodes(loadedFlow.draft_content.nodes.map((node) => ({
      ...node,
      type: 'workflow' as const,
      data: {
        ...node.data,
        definition: byKey.get(`${node.data.nodeType}@${node.data.nodeVersion}`),
      },
    })))
    setEdges(loadedFlow.draft_content.edges)
    setInputSchema(schema)
    setConfigSchema(loadedConfigSchema)
    setDefaultConfig(loadedDefaultConfig)
    setRunInput(inputDefaults(schema))
    setSavedHash(documentHash({ content: loadedFlow.draft_content, inputSchema: schema, configSchema: loadedConfigSchema, defaultConfig: loadedDefaultConfig }))
    setPast([])
    setFuture([])
    const latest = loadedVersions[0]?.version_number ?? null
    const previous = loadedVersions[1]?.version_number ?? latest
    setCompareFrom(previous)
    setCompareTo(latest)
    historyReady.current = true
    setTimeout(() => fitView({ padding: 0.18 }), 30)
  }, [fitView])

  useEffect(() => {
    historyReady.current = false
    Promise.all([api.flow(flowId), api.nodeTypes(), api.versions(flowId), api.schedules(flowId), api.credentials(flowId)])
      .then(([loadedFlow, loadedDefinitions, loadedVersions, loadedSchedules, loadedCredentials]) => {
        setDefinitions(loadedDefinitions)
        setSchedules(loadedSchedules)
        setCredentials(loadedCredentials)
        setScheduleVersion(loadedVersions[0]?.version_number ?? null)
        setScheduleInput(loadedVersions[0] ? inputDefaults(loadedVersions[0].input_schema) : {})
        loadEditor(loadedFlow, loadedVersions, loadedDefinitions)
      })
      .catch((err: Error) => setNotice({ kind: 'error', text: err.message }))
  }, [flowId, loadEditor])

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    const protectLinks = (event: MouseEvent) => {
      if (!isDirty) return
      const target = event.target as Element | null
      const anchor = target?.closest('a')
      if (!anchor || anchor.target === '_blank') return
      if (!window.confirm('You have unsaved changes. Leave this page and discard them?')) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('click', protectLinks, true)
    return () => {
      window.removeEventListener('beforeunload', beforeUnload)
      document.removeEventListener('click', protectLinks, true)
    }
  }, [isDirty])

  useEffect(() => {
    const shortcuts = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return
      if (event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) redo()
        else undo()
      } else if (event.key.toLowerCase() === 'y') {
        event.preventDefault()
        redo()
      } else if (event.key.toLowerCase() === 's') {
        event.preventDefault()
        void runAction('save')
      }
    }
    window.addEventListener('keydown', shortcuts)
    return () => window.removeEventListener('keydown', shortcuts)
  })

  const handleNodesChange = (changes: NodeChange<WorkflowNodeType>[]) => {
    if (changes.some((change) => change.type === 'remove')) commitHistory()
    setNodes((current) => applyNodeChanges(changes, current))
  }

  const handleEdgesChange = (changes: EdgeChange<WorkflowEdge>[]) => {
    if (changes.some((change) => change.type === 'remove')) commitHistory()
    setEdges((current) => applyEdgeChanges(changes, current))
  }

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || connection.source === connection.target) return
    commitHistory()
    setEdges((current) => addEdge({ ...connection, id: crypto.randomUUID(), animated: true }, current))
  }, [commitHistory])

  const onDrop = useCallback((event: DragEvent) => {
    event.preventDefault()
    const raw = event.dataTransfer.getData('application/flowforge-node')
    if (!raw) return
    const { type, version } = JSON.parse(raw) as { type: string; version: string }
    const definition = definitionsByKey.get(`${type}@${version}`)
    if (!definition) return
    commitHistory()
    const id = `${type}-${crypto.randomUUID().slice(0, 8)}`
    const node: WorkflowNodeType = {
      id,
      type: 'workflow',
      position: screenToFlowPosition({ x: event.clientX, y: event.clientY }),
      data: {
        label: definition.name,
        nodeType: type,
        nodeVersion: version,
        config: structuredClone(definition.defaultConfig),
        definition,
      },
    }
    setNodes((current) => [...current, node])
    setSelectedId(id)
    setSelectedEdgeId(null)
    setPanelMode('properties')
  }, [commitHistory, definitionsByKey, screenToFlowPosition])

  const selectedNode = nodes.find((node) => node.id === selectedId)
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId)

  const deleteNode = () => {
    if (!selectedNode || !window.confirm(`Delete "${selectedNode.data.label}" and its connections?`)) return
    commitHistory()
    setNodes((current) => current.filter((node) => node.id !== selectedNode.id))
    setEdges((current) => current.filter(
      (edge) => edge.source !== selectedNode.id && edge.target !== selectedNode.id,
    ))
    setSelectedId(null)
    setNotice({ kind: 'success', text: 'Node removed. Undo is available until the draft is reloaded.' })
  }

  const deleteEdge = () => {
    if (!selectedEdge) return
    commitHistory()
    setEdges((current) => current.filter((edge) => edge.id !== selectedEdge.id))
    setSelectedEdgeId(null)
    setNotice({ kind: 'success', text: 'Connection removed. Undo is available.' })
  }

  const updateSelected = (patch: Partial<WorkflowNodeType['data']>) => {
    if (!selectedId) return
    commitHistory()
    setNodes((current) => current.map((node) => (
      node.id === selectedId ? { ...node, data: { ...node.data, ...patch } } : node
    )))
  }

  const updateConfig = (key: string, value: unknown) => {
    if (!selectedNode) return
    updateSelected({ config: { ...selectedNode.data.config, [key]: value } })
  }

  const updateFlowConfigPatch = (raw: string) => {
    if (!selectedNode) return
    const trimmed = raw.trim()
    if (!trimmed) {
      updateSelected({ flowConfigPatch: undefined })
      return
    }
    try {
      const parsed = JSON.parse(trimmed) as unknown
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error('Flow configuration patch must be a JSON object.')
      }
      const patch = parsed as Record<string, unknown>
      updateSelected({ flowConfigPatch: Object.keys(patch).length ? patch : undefined })
      setNotice({ kind: 'success', text: 'Flow configuration patch updated.' })
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Invalid JSON patch.' })
    }
  }

  const updateSchema = (schema: JsonSchema) => {
    commitHistory()
    setInputSchema(schema)
    const defaults = inputDefaults(schema)
    setRunInput((current) => Object.fromEntries(
      Object.keys(schema.properties).map((name) => [name, current[name] ?? defaults[name]]),
    ))
  }

  const addInputField = () => {
    let index = Object.keys(inputSchema.properties).length + 1
    let name = `field${index}`
    while (name in inputSchema.properties) name = `field${++index}`
    updateSchema({
      ...inputSchema,
      properties: {
        ...inputSchema.properties,
        [name]: { type: 'string', title: `Field ${index}`, default: '' },
      },
    })
  }

  const renameInputField = (oldName: string, nextName: string) => {
    const clean = nextName.trim().replace(/[^A-Za-z0-9_]/g, '')
    if (!clean || clean === oldName || clean in inputSchema.properties) return
    const entries = Object.entries(inputSchema.properties).map(([name, property]) => (
      name === oldName ? [clean, property] : [name, property]
    ))
    updateSchema({
      ...inputSchema,
      properties: Object.fromEntries(entries),
      required: (inputSchema.required ?? []).map((name) => (name === oldName ? clean : name)),
    })
  }

  const updateInputField = (name: string, patch: Partial<JsonSchemaProperty>) => {
    updateSchema({
      ...inputSchema,
      properties: {
        ...inputSchema.properties,
        [name]: { ...inputSchema.properties[name], ...patch },
      },
    })
  }

  const removeInputField = (name: string) => {
    const properties = { ...inputSchema.properties }
    delete properties[name]
    updateSchema({
      ...inputSchema,
      properties,
      required: (inputSchema.required ?? []).filter((item) => item !== name),
    })
  }

  const toggleRequired = (name: string, required: boolean) => {
    const current = new Set(inputSchema.required ?? [])
    if (required) current.add(name)
    else current.delete(name)
    updateSchema({ ...inputSchema, required: [...current] })
  }

  const updateFlowConfiguration = (
    schema: JsonSchema,
    defaults: Record<string, unknown>,
  ) => {
    commitHistory()
    setConfigSchema(schema)
    setDefaultConfig(defaults)
  }

  const addConfigField = () => {
    let index = Object.keys(configSchema.properties).length + 1
    let name = `config${index}`
    while (name in configSchema.properties) name = `config${++index}`
    updateFlowConfiguration(
      {
        ...configSchema,
        properties: {
          ...configSchema.properties,
          [name]: { type: 'string', title: `Configuration ${index}`, default: '' },
        },
      },
      { ...defaultConfig, [name]: '' },
    )
  }

  const updateConfigField = (
    name: string,
    property: Partial<JsonSchemaProperty>,
    value: unknown = defaultConfig[name],
  ) => updateFlowConfiguration(
    {
      ...configSchema,
      properties: {
        ...configSchema.properties,
        [name]: { ...configSchema.properties[name], ...property },
      },
    },
    { ...defaultConfig, [name]: value },
  )

  const removeConfigField = (name: string) => {
    const properties = { ...configSchema.properties }
    const defaults = { ...defaultConfig }
    delete properties[name]
    delete defaults[name]
    updateFlowConfiguration(
      { ...configSchema, properties, required: (configSchema.required ?? []).filter((item) => item !== name) },
      defaults,
    )
  }

  const toggleConfigRequired = (name: string, required: boolean) => {
    const current = new Set(configSchema.required ?? [])
    if (required) current.add(name)
    else current.delete(name)
    updateFlowConfiguration({ ...configSchema, required: [...current] }, defaultConfig)
  }

  const createCredential = async () => {
    if (!flow) return
    setBusy(true)
    try {
      await api.createCredential(flow.id, {
        alias: credentialAlias,
        type: credentialType,
        allowedOrigins: credentialOrigins.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
        secret: credentialSecret,
      })
      setCredentials(await api.credentials(flow.id))
      setCredentialSecret({})
      setNotice({ kind: 'success', text: `Credential ${credentialAlias} created. Its secret is no longer visible.` })
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Could not create credential' })
    } finally { setBusy(false) }
  }

  const promptCredentialSecret = (credential: FlowCredential): Record<string, unknown> | null => {
    if (credential.type === 'BEARER') {
      const token = window.prompt(`New token for ${credential.alias}`)
      return token ? { token } : null
    }
    if (credential.type === 'BASIC') {
      const username = window.prompt(`New username for ${credential.alias}`)
      const password = window.prompt(`New password for ${credential.alias}`)
      return username && password ? { username, password } : null
    }
    const headerName = window.prompt('Header name', 'X-API-Key')
    const value = window.prompt(`New API key for ${credential.alias}`)
    const prefix = window.prompt('Optional prefix', '') ?? ''
    return headerName && value ? { headerName, value, prefix } : null
  }

  const rotateCredential = async (credential: FlowCredential) => {
    if (!flow) return
    const secret = promptCredentialSecret(credential)
    if (!secret) return
    try {
      await api.rotateCredential(flow.id, credential.id, secret)
      setCredentials(await api.credentials(flow.id))
      setNotice({ kind: 'success', text: `${credential.alias} rotated. New nodes use the latest revision.` })
    } catch (err) { setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Rotation failed' }) }
  }

  const save = async (): Promise<FlowDetail> => {
    if (!flow) throw new Error('Flow is still loading')
    const snapshot = capture()
    const saved = await api.saveDraft(
      flow.id,
      snapshot.content,
      snapshot.inputSchema,
      snapshot.configSchema,
      snapshot.defaultConfig,
      flow.row_version,
    )
    setFlow(saved)
    setSavedHash(documentHash(snapshot))
    setNotice({ kind: 'success', text: 'Draft saved.' })
    return saved
  }

  const runAction = async (action: 'save' | 'validate' | 'publish' | 'run') => {
    setBusy(true)
    try {
      const saved = await save()
      if (action === 'validate') {
        const result = await api.validate(saved.id)
        setNotice(result.valid
          ? { kind: 'success', text: 'Flow validation passed.' }
          : { kind: 'error', text: result.issues.map((issue) => issue.message).join(' · ') })
      }
      if (action === 'publish' || action === 'run') {
        const version = await api.publish(saved.id)
        setFlow((current) => current ? { ...current, current_version: version.version_number, status: 'ACTIVE' } : current)
        const loadedVersions = await api.versions(saved.id)
        setVersions(loadedVersions)
        setScheduleVersion(version.version_number)
        setScheduleInput(inputDefaults(loadedVersions[0].input_schema))
        setNotice({ kind: 'success', text: `Published version ${version.version_number}.` })
      }
      if (action === 'run') {
        const run = await api.createRun(saved.id, runInput)
        navigate(`/runs/${run.id}`)
      }
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Action failed' })
    } finally {
      setBusy(false)
    }
  }

  const rollback = async (versionNumber: number) => {
    if (!flow || !window.confirm(`Rollback version ${versionNumber} as a new published version?`)) return
    setBusy(true)
    try {
      const restored = await api.rollbackVersion(flow.id, versionNumber)
      const [loadedFlow, loadedVersions] = await Promise.all([api.flow(flow.id), api.versions(flow.id)])
      loadEditor(loadedFlow, loadedVersions, definitions)
      setNotice({ kind: 'success', text: `Version ${versionNumber} restored as version ${restored.version_number}.` })
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Rollback failed' })
    } finally {
      setBusy(false)
    }
  }

  const changeFlowStatus = async (status: 'ACTIVE' | 'PAUSED' | 'ARCHIVED') => {
    if (!flow) return
    setBusy(true)
    try {
      const updated = await api.updateFlowStatus(flow.id, status)
      setFlow(updated)
      setNotice({ kind: 'success', text: `Flow is now ${status.toLowerCase()}.` })
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Status update failed' })
    } finally { setBusy(false) }
  }

  const selectScheduleVersion = (versionNumber: number) => {
    const version = versions.find((item) => item.version_number === versionNumber)
    setScheduleVersion(versionNumber)
    setScheduleInput(version ? inputDefaults(version.input_schema) : {})
  }

  const createSchedule = async () => {
    if (!flow || !scheduleVersion) return
    setBusy(true)
    try {
      await api.createSchedule(flow.id, {
        name: scheduleName,
        cronExpression: scheduleCron,
        timezone: scheduleTimezone,
        versionNumber: scheduleVersion,
        inputData: scheduleInput,
        enabled: true,
      })
      setSchedules(await api.schedules(flow.id))
      setNotice({ kind: 'success', text: 'Schedule created.' })
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Could not create schedule' })
    } finally { setBusy(false) }
  }

  const toggleSchedule = async (schedule: FlowSchedule) => {
    if (!flow) return
    try {
      await api.updateSchedule(flow.id, schedule.id, { enabled: !schedule.enabled })
      setSchedules(await api.schedules(flow.id))
    } catch (err) { setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Could not update schedule' }) }
  }

  const removeSchedule = async (schedule: FlowSchedule) => {
    if (!flow || !window.confirm(`Delete schedule "${schedule.name}"?`)) return
    try {
      await api.deleteSchedule(flow.id, schedule.id)
      setSchedules(await api.schedules(flow.id))
    } catch (err) { setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'Could not delete schedule' }) }
  }

  const categories = useMemo(() => {
    const result = new Map<string, NodeTypeDefinition[]>()
    definitions.forEach((definition) => {
      if (!definition.availableForNewFlows) return
      result.set(definition.category, [...(result.get(definition.category) ?? []), definition])
    })
    return result
  }, [definitions])
  const fromVersion = versions.find((item) => item.version_number === compareFrom)
  const toVersion = versions.find((item) => item.version_number === compareTo)
  const differences = versionDiff(fromVersion, toVersion)

  return (
    <div className="designer-page">
      <div className="designer-toolbar">
        <div className="toolbar-title">
          <Link to="/flows" className="icon-button" title="Back to flows"><ArrowLeft size={18} /></Link>
          <div><strong>{flow?.name ?? 'Loading…'}</strong><small>Draft · Published v{flow?.current_version ?? 0}{isDirty ? ' · Unsaved changes' : ''}</small></div>
        </div>
        {notice && <div className={`toolbar-notice ${notice.kind}`}>{notice.text}</div>}
        <div className="toolbar-actions">
          <button className="icon-button" disabled={!past.length || busy} onClick={undo} title="Undo (Ctrl+Z)"><Undo2 size={17} /></button>
          <button className="icon-button" disabled={!future.length || busy} onClick={redo} title="Redo (Ctrl+Y)"><Redo2 size={17} /></button>
          <button className="button ghost" disabled={busy} onClick={() => {
            commitHistory()
            setNodes((current) => layoutFlow(current, edges))
            setTimeout(() => fitView({ padding: 0.18, duration: 300 }), 20)
          }} title="Auto layout"><LayoutDashboard size={16} /> <span>Auto layout</span></button>
          <button className="button ghost" title="Input schema" onClick={() => setPanelMode('input')}><Braces size={16} /> <span>Input schema</span></button>
          <button className="button ghost" title="Flow configuration" onClick={() => setPanelMode('configuration')}><Braces size={16} /> <span>Flow configuration</span></button>
          <button className="button ghost" title="Credentials" onClick={() => setPanelMode('credentials')}><KeyRound size={16} /> <span>Credentials</span></button>
          <button className="button ghost" title="Versions" onClick={() => setPanelMode('versions')}><History size={16} /> <span>Versions</span></button>
          <button className="button ghost" title="Schedules" onClick={() => setPanelMode('schedules')}><CalendarClock size={16} /> <span>Schedules</span></button>
          <select className={`status-select ${flow?.status.toLowerCase() ?? ''}`} disabled={busy || !flow?.current_version} value={flow?.status ?? 'DRAFT'} onChange={(event) => void changeFlowStatus(event.target.value as 'ACTIVE' | 'PAUSED' | 'ARCHIVED')} title="Flow operational status">
            {!flow?.current_version && <option value="DRAFT">DRAFT</option>}
            <option value="ACTIVE">ACTIVE</option><option value="PAUSED">PAUSED</option><option value="ARCHIVED">ARCHIVED</option>
          </select>
          <button className="button secondary" title="Validate" disabled={busy} onClick={() => void runAction('validate')}><CheckCircle2 size={16} /> <span>Validate</span></button>
          <button className="button secondary" title="Save" disabled={busy || !isDirty} onClick={() => void runAction('save')}><Save size={16} /> <span>Save</span></button>
          <button className="button primary" title="Publish" disabled={busy} onClick={() => void runAction('publish')}><Upload size={16} /> <span>Publish</span></button>
        </div>
      </div>
      <div className="designer-layout">
        <aside className="node-palette">
          <div className="panel-title"><strong>Node library</strong><span>Drag onto the canvas</span></div>
          {[...categories.entries()].map(([category, items]) => (
            <section key={category}>
              <h3>{category}</h3>
              {items.map((item) => (
                <div
                  className="palette-node"
                  draggable
                  key={`${item.type}@${item.version}`}
                  onDragStart={(event) => {
                    event.dataTransfer.setData('application/flowforge-node', JSON.stringify({
                      type: item.type,
                      version: item.version,
                    }))
                    event.dataTransfer.effectAllowed = 'move'
                  }}
                >
                  <span style={{ background: item.color }} />
                  <div><strong>{item.name}</strong><small>{item.description}</small></div>
                </div>
              ))}
            </section>
          ))}
        </aside>

        <div className="flow-canvas" onDrop={onDrop} onDragOver={(event) => event.preventDefault()}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={onConnect}
            onNodeDragStart={commitHistory}
            onNodeClick={(_, node) => {
              setSelectedId(node.id)
              setSelectedEdgeId(null)
              setPanelMode('properties')
            }}
            onEdgeClick={(_, edge) => {
              setSelectedEdgeId(edge.id)
              setSelectedId(null)
              setPanelMode('properties')
            }}
            onNodesDelete={(deleted) => {
              const deletedIds = new Set(deleted.map((node) => node.id))
              setEdges((current) => current.filter(
                (edge) => !deletedIds.has(edge.source) && !deletedIds.has(edge.target),
              ))
              setSelectedId(null)
            }}
            onEdgesDelete={() => setSelectedEdgeId(null)}
            onPaneClick={() => {
              setSelectedId(null)
              setSelectedEdgeId(null)
            }}
            fitView
            deleteKeyCode={['Backspace', 'Delete']}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.3} color="#cbd5e1" />
            <Controls position="bottom-left" />
            <MiniMap pannable zoomable nodeColor={(node) => (node.data as WorkflowNodeData | undefined)?.definition?.color ?? '#94a3b8'} />
          </ReactFlow>
          <div className="quick-run schema-run">
            <div><strong>Test input</strong><small>Generated from the draft schema</small></div>
            <SchemaInputFields schema={inputSchema} value={runInput} onChange={setRunInput} compact />
            <button className="button run" disabled={busy} onClick={() => void runAction('run')}><Play size={16} /> Publish & run</button>
          </div>
        </div>

        <aside className="properties-panel">
          {panelMode === 'input' ? (
            <>
              <div className="panel-title with-action"><div><strong>Input schema</strong><span>Define the run form and validation</span></div><button className="icon-button" onClick={() => setPanelMode('properties')} title="Close"><X size={15} /></button></div>
              <div className="schema-builder">
                {Object.entries(inputSchema.properties).map(([name, property]) => (
                  <div className="schema-field-card" key={name}>
                    <div className="schema-field-heading"><strong>{property.title || name}</strong><button onClick={() => removeInputField(name)} title="Remove field"><Trash2 size={14} /></button></div>
                    <label>Field name<input defaultValue={name} onBlur={(event) => renameInputField(name, event.target.value)} /></label>
                    <label>Label<input value={property.title ?? ''} onChange={(event) => updateInputField(name, { title: event.target.value })} /></label>
                    <label>Type<select value={property.type ?? 'string'} onChange={(event) => updateInputField(name, { type: event.target.value as JsonSchemaProperty['type'], default: event.target.value === 'boolean' ? false : event.target.value === 'number' || event.target.value === 'integer' ? 0 : '' })}><option value="string">String</option><option value="number">Number</option><option value="integer">Integer</option><option value="boolean">Boolean</option></select></label>
                    <label>Description<input value={property.description ?? ''} onChange={(event) => updateInputField(name, { description: event.target.value })} /></label>
                    <label>Default value{property.type === 'boolean' ? <input type="checkbox" checked={Boolean(property.default)} onChange={(event) => updateInputField(name, { default: event.target.checked })} /> : <input type={property.type === 'number' || property.type === 'integer' ? 'number' : 'text'} value={String(property.default ?? '')} onChange={(event) => updateInputField(name, { default: property.type === 'number' || property.type === 'integer' ? Number(event.target.value) : event.target.value })} />}</label>
                    <label className="checkbox-label"><input type="checkbox" checked={(inputSchema.required ?? []).includes(name)} onChange={(event) => toggleRequired(name, event.target.checked)} /> Required</label>
                  </div>
                ))}
                <button className="button secondary full" onClick={addInputField}><Plus size={15} /> Add input field</button>
              </div>
            </>
          ) : panelMode === 'configuration' ? (
            <>
              <div className="panel-title with-action"><div><strong>Flow configuration</strong><span>Versioned, non-sensitive defaults</span></div><button className="icon-button" onClick={() => setPanelMode('properties')} title="Close"><X size={15} /></button></div>
              <div className="schema-builder">
                {Object.entries(configSchema.properties).map(([name, property]) => (
                  <div className="schema-field-card" key={name}>
                    <div className="schema-field-heading"><strong>{property.title || name}</strong><button onClick={() => removeConfigField(name)} title="Remove configuration"><Trash2 size={14} /></button></div>
                    <label>Key<input value={name} disabled title="Remove and recreate a key to rename it" /></label>
                    <label>Label<input value={property.title ?? ''} onChange={(event) => updateConfigField(name, { title: event.target.value })} /></label>
                    <label>Type<select value={property.type ?? 'string'} onChange={(event) => {
                      const type = event.target.value as JsonSchemaProperty['type']
                      const nextValue = type === 'boolean' ? false : type === 'number' || type === 'integer' ? 0 : ''
                      updateConfigField(name, { type, default: nextValue }, nextValue)
                    }}><option value="string">String</option><option value="number">Number</option><option value="integer">Integer</option><option value="boolean">Boolean</option></select></label>
                    <label>Default value{property.type === 'boolean' ? <input type="checkbox" checked={Boolean(defaultConfig[name])} onChange={(event) => updateConfigField(name, { default: event.target.checked }, event.target.checked)} /> : <input type={property.type === 'number' || property.type === 'integer' ? 'number' : 'text'} value={String(defaultConfig[name] ?? '')} onChange={(event) => { const value = property.type === 'number' || property.type === 'integer' ? Number(event.target.value) : event.target.value; updateConfigField(name, { default: value }, value) }} />}</label>
                    <label className="checkbox-label"><input type="checkbox" checked={(configSchema.required ?? []).includes(name)} onChange={(event) => toggleConfigRequired(name, event.target.checked)} /> Required</label>
                    <small className="property-help">Template: <code>{`{{ flowConfig.${name} }}`}</code></small>
                  </div>
                ))}
                <button className="button secondary full" onClick={addConfigField}><Plus size={15} /> Add configuration</button>
                <p className="property-help">Do not store passwords or API keys here. Use Flow Credentials for sensitive values.</p>
              </div>
            </>
          ) : panelMode === 'credentials' ? (
            <>
              <div className="panel-title with-action"><div><strong>Credentials</strong><span>Flow-scoped authentication</span></div><button className="icon-button" onClick={() => setPanelMode('properties')} title="Close"><X size={15} /></button></div>
              <div className="credential-panel">
                <div className="credential-form">
                  <label>Alias<input value={credentialAlias} onChange={(event) => setCredentialAlias(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))} placeholder="partner_api" /></label>
                  <label>Type<select value={credentialType} onChange={(event) => { setCredentialType(event.target.value as typeof credentialType); setCredentialSecret({}) }}><option value="BEARER">Bearer token</option><option value="BASIC">Basic authentication</option><option value="API_KEY_HEADER">API key header</option></select></label>
                  <label>Allowed origins<textarea value={credentialOrigins} onChange={(event) => setCredentialOrigins(event.target.value)} placeholder="https://partner.example.com" /></label>
                  {credentialType === 'BEARER' && <label>Token<input type="password" value={credentialSecret.token ?? ''} onChange={(event) => setCredentialSecret({ token: event.target.value })} /></label>}
                  {credentialType === 'BASIC' && <><label>Username<input value={credentialSecret.username ?? ''} onChange={(event) => setCredentialSecret((current) => ({ ...current, username: event.target.value }))} /></label><label>Password<input type="password" value={credentialSecret.password ?? ''} onChange={(event) => setCredentialSecret((current) => ({ ...current, password: event.target.value }))} /></label></>}
                  {credentialType === 'API_KEY_HEADER' && <><label>Header name<input value={credentialSecret.headerName ?? ''} onChange={(event) => setCredentialSecret((current) => ({ ...current, headerName: event.target.value }))} placeholder="X-API-Key" /></label><label>Value<input type="password" value={credentialSecret.value ?? ''} onChange={(event) => setCredentialSecret((current) => ({ ...current, value: event.target.value }))} /></label><label>Optional prefix<input value={credentialSecret.prefix ?? ''} onChange={(event) => setCredentialSecret((current) => ({ ...current, prefix: event.target.value }))} placeholder="Token " /></label></>}
                  <button className="button primary full" disabled={busy || !credentialAlias || !credentialOrigins.trim()} onClick={() => void createCredential()}><Plus size={15} /> Create credential</button>
                  <p className="property-help">Secrets are encrypted and never displayed again after creation.</p>
                </div>
                <div className="credential-list">{credentials.map((credential) => <div className="credential-card" key={credential.id}><div><strong>{credential.alias}</strong><small>{credential.type} · revision {credential.revision}</small><small>{credential.allowed_origins.join(', ')}</small></div><span className={`badge ${credential.enabled ? 'active' : 'archived'}`}>{credential.enabled ? 'ENABLED' : 'DISABLED'}</span><div><button className="row-action" onClick={() => void rotateCredential(credential)}>Rotate</button><button className="row-action" onClick={() => flow && void api.updateCredential(flow.id, credential.id, { enabled: !credential.enabled }).then(() => api.credentials(flow.id)).then(setCredentials).catch((err: Error) => setNotice({ kind: 'error', text: err.message }))}>{credential.enabled ? 'Disable' : 'Enable'}</button></div></div>)}</div>
                {!credentials.length && <div className="panel-placeholder">No credentials configured.</div>}
              </div>
            </>
          ) : panelMode === 'versions' ? (
            <>
              <div className="panel-title with-action"><div><strong>Version history</strong><span>Compare or restore published versions</span></div><button className="icon-button" onClick={() => setPanelMode('properties')} title="Close"><X size={15} /></button></div>
              {!versions.length ? <div className="panel-placeholder">Publish the flow to create its first version.</div> : <div className="version-panel">
                <div className="version-compare-selects">
                  <label>From<select value={compareFrom ?? ''} onChange={(event) => setCompareFrom(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.version_number}>Version {version.version_number}</option>)}</select></label>
                  <label>To<select value={compareTo ?? ''} onChange={(event) => setCompareTo(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.version_number}>Version {version.version_number}</option>)}</select></label>
                </div>
                <div className="version-diff"><strong>Changes</strong>{differences.length ? <ul>{differences.map((change) => <li key={change}>{change}</li>)}</ul> : <p>No structural differences.</p>}</div>
                <div className="version-list">{versions.map((version) => <div key={version.id}><div><strong>Version {version.version_number}</strong><small>{new Date(version.created_at).toLocaleString()}</small></div>{version.version_number !== flow?.current_version && <button className="row-action" disabled={busy} onClick={() => void rollback(version.version_number)}><RotateCcw size={13} /> Rollback</button>}</div>)}</div>
              </div>}
            </>
          ) : panelMode === 'schedules' ? (
            <>
              <div className="panel-title with-action"><div><strong>Schedules</strong><span>Trigger a pinned version with cron</span></div><button className="icon-button" onClick={() => setPanelMode('properties')} title="Close"><X size={15} /></button></div>
              {!versions.length ? <div className="panel-placeholder">Publish the flow before creating a schedule.</div> : <div className="schedule-panel">
                <div className="schedule-form">
                  <label>Name<input value={scheduleName} onChange={(event) => setScheduleName(event.target.value)} /></label>
                  <label>Cron expression<input value={scheduleCron} onChange={(event) => setScheduleCron(event.target.value)} placeholder="0 9 * * *" /></label>
                  <label>Timezone<input value={scheduleTimezone} onChange={(event) => setScheduleTimezone(event.target.value)} placeholder="UTC" /></label>
                  <label>Published version<select value={scheduleVersion ?? ''} onChange={(event) => selectScheduleVersion(Number(event.target.value))}>{versions.map((version) => <option key={version.id} value={version.version_number}>Version {version.version_number}</option>)}</select></label>
                  {scheduleVersion && <SchemaInputFields schema={versions.find((item) => item.version_number === scheduleVersion)?.input_schema ?? emptySchema} value={scheduleInput} onChange={setScheduleInput} />}
                  <button className="button primary full" disabled={busy || !scheduleName.trim()} onClick={() => void createSchedule()}><Plus size={15} /> Create schedule</button>
                  <p className="property-help">Examples: <code>*/5 * * * *</code> every five minutes, <code>0 9 * * 1-5</code> weekdays at 09:00.</p>
                </div>
                <div className="schedule-list">{schedules.map((schedule) => <div className="schedule-card" key={schedule.id}><div><strong>{schedule.name}</strong><small>{schedule.cron_expression} · {schedule.timezone} · v{schedule.version_number}</small><small>Next: {new Date(schedule.next_run_at).toLocaleString()}</small></div><div><button className="row-action" onClick={() => void toggleSchedule(schedule)}>{schedule.enabled ? 'Pause' : 'Enable'}</button><button className="icon-button danger-text" onClick={() => void removeSchedule(schedule)} title="Delete schedule"><Trash2 size={14} /></button></div></div>)}</div>
                {!schedules.length && <div className="panel-placeholder">No schedules yet.</div>}
              </div>}
            </>
          ) : (
            <>
              <div className="panel-title"><strong>Properties</strong><span>Configure the selected node</span></div>
              {!selectedNode && !selectedEdge ? (
                <div className="panel-placeholder">Select a node to edit or delete it. Use Input schema or Versions in the toolbar for flow-level settings.</div>
              ) : selectedEdge ? (
                <div className="property-form">
                  <div className="property-meta"><span>Connection</span><code>{selectedEdge.source} → {selectedEdge.target}</code></div>
                  <button className="button danger full" onClick={deleteEdge}><Unlink size={16} /> Delete connection</button>
                </div>
              ) : selectedNode ? (
                <div className="property-form">
                  <label>Node label<input value={selectedNode.data.label} onChange={(event) => updateSelected({ label: event.target.value })} /></label>
                  <div className="property-meta"><span>Type</span><code>{selectedNode.data.nodeType}@{selectedNode.data.nodeVersion}</code></div>
                  {Object.entries(selectedNode.data.definition?.configSchema.properties ?? {}).map(([key, property]) => {
                    const value = selectedNode.data.config[key]
                    const inputType = property.type === 'number' || property.type === 'integer' || typeof value === 'number' ? 'number' : 'text'
                    return <label key={key}>{property.title ?? key}{property.format === 'flow-credential' ? <select value={String(value ?? '')} onChange={(event) => updateConfig(key, event.target.value)}><option value="">No authentication</option>{credentials.filter((item) => item.enabled).map((item) => <option value={item.alias} key={item.id}>{item.alias} · {item.type}</option>)}</select> : property.enum ? <select value={String(value ?? '')} onChange={(event) => updateConfig(key, event.target.value)}>{property.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select> : <input type={inputType} value={String(value ?? '')} min={property.minimum} max={property.maximum} onChange={(event) => updateConfig(key, inputType === 'number' ? Number(event.target.value) : event.target.value)} />}</label>
                  })}
                  <label>Flow configuration patch
                    <textarea
                      key={`${selectedNode.id}:${JSON.stringify(selectedNode.data.flowConfigPatch ?? {})}`}
                      defaultValue={JSON.stringify(selectedNode.data.flowConfigPatch ?? {}, null, 2)}
                      onBlur={(event) => updateFlowConfigPatch(event.target.value)}
                    />
                  </label>
                  <p className="property-help">Optional JSON object merged into <code>flowConfig</code> after this node succeeds. Template values such as <code>{'{{ input.field }}'}</code> are resolved at runtime.</p>
                  {selectedNode.data.nodeType === 'set_variable' && <p className="property-help">Use <code>{'{{ input.field }}'}</code>, <code>{'{{ variables.name }}'}</code>, or <code>{'{{ run.id }}'}</code>. A full template preserves numbers, booleans, objects, and arrays.</p>}
                  <p className="property-help">Changes are stored in the draft when you click Save.</p>
                  <button className="button danger full" onClick={deleteNode}><Trash2 size={16} /> Delete node</button>
                </div>
              ) : null}
            </>
          )}
        </aside>
      </div>
    </div>
  )
}

export function DesignerPage() {
  return <ReactFlowProvider><DesignerCanvas /></ReactFlowProvider>
}
