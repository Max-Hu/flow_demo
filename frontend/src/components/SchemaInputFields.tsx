import type { JsonSchema } from '../types'

export function inputDefaults(schema: JsonSchema): Record<string, unknown> {
  return Object.fromEntries(Object.entries(schema.properties).map(([name, property]) => {
    if (property.default !== undefined) return [name, property.default]
    if (property.type === 'boolean') return [name, false]
    if (property.type === 'number' || property.type === 'integer') return [name, 0]
    return [name, '']
  }))
}

interface SchemaInputFieldsProps {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  compact?: boolean
}

export function SchemaInputFields({ schema, value, onChange, compact = false }: SchemaInputFieldsProps) {
  const required = new Set(schema.required ?? [])
  const fields = Object.entries(schema.properties)

  if (!fields.length) {
    return <p className="schema-empty">This flow does not define any input fields.</p>
  }

  const update = (name: string, next: unknown) => onChange({ ...value, [name]: next })

  return (
    <div className={`schema-input-fields ${compact ? 'compact' : ''}`}>
      {fields.map(([name, property]) => (
        <label key={name}>
          <span>{property.title || name}{required.has(name) && <b> *</b>}</span>
          {property.enum ? (
            <select value={String(value[name] ?? '')} onChange={(event) => update(name, event.target.value)}>
              {property.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
            </select>
          ) : property.type === 'boolean' ? (
            <input type="checkbox" checked={Boolean(value[name])} onChange={(event) => update(name, event.target.checked)} />
          ) : (
            <input
              type={property.type === 'number' || property.type === 'integer' ? 'number' : 'text'}
              value={String(value[name] ?? '')}
              min={property.minimum}
              max={property.maximum}
              required={required.has(name)}
              onChange={(event) => update(
                name,
                property.type === 'number' || property.type === 'integer'
                  ? Number(event.target.value)
                  : event.target.value,
              )}
            />
          )}
          {!compact && property.description && <small>{property.description}</small>}
        </label>
      ))}
    </div>
  )
}
