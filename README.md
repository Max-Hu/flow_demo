# FlowForge MVP

FlowForge is a local, end-to-end demonstration of a visual automation platform:

- React, TypeScript, and React Flow for workflow design and live run visualization.
- FastAPI for flow management, validation, publishing, runs, and SSE events.
- A separate Python worker for durable node execution.
- A separate Python scheduler for durable cron-triggered runs.
- PostgreSQL for flow versions, run state, node state, attempts, and events.
- A deterministic local Partner API that behaves like a third-party JSON service.

The seeded demo accepts a customer ID, calls the Partner API, evaluates the returned score, and returns either `APPROVED` or `MANUAL_REVIEW`.

## Quick start

Requirements:

- Docker Desktop with Docker Compose.
- Ports `3000`, `8000`, `8001`, and `5432` available.

Start the complete stack:

```powershell
Copy-Item .env.example .env
cd backend
pip install -r requirements.txt
python -m app.security.generate_setup_values
cd ..
# Paste the three generated WORKFLOW_* values into .env.
docker compose up --build
```

The password generator only prints an Argon2id hash and a random AES-256 key. The plaintext
administrator password is never written to the repository. Keep `.env` private; it is ignored by Git.

Open:

- UI: [http://localhost:3000](http://localhost:3000)
- FastAPI documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Demo Partner API documentation: [http://localhost:8001/docs](http://localhost:8001/docs)
- Adminer database browser: [http://localhost:8080](http://localhost:8080)

In Adminer choose PostgreSQL and use server `database`, database/user `workflow`, and the
password configured by `POSTGRES_PASSWORD`. The complete schema diagram, table dictionary,
and useful SQL are in [docs/database-erd.md](docs/database-erd.md).

The first API start runs Alembic migrations and creates the active `Customer Score Automation`, `Manual Approval Demo`, and `Run Variables Demo` flows.

## Demo walkthrough

1. Open **Flows** and select **Customer Score Automation**.
2. Drag nodes, change labels or properties, and use **Undo**, **Redo**, **Input schema**, or **Versions** from the toolbar.
3. Enter one of the generated demo IDs in the bottom-right run panel:
   - `CUST-1001` returns score 86 and follows the approved branch.
   - `CUST-1002` returns score 42 and follows the manual-review branch.
   - `CUST-1003` returns score 71 and follows the approved branch.
4. Click **Publish & run**.
5. The run page updates node states through Server-Sent Events. Select a node to inspect its input, output, attempts, duration, or error.

The live canvas uses directional runtime paths: completed edges are green, the edge entering the current node is blue with a moving particle, manual waits are pink, failures are red, and inactive branches are dimmed. The Current step overlay and legend remain visible while the graph updates through SSE.

To demonstrate human-in-the-loop execution, run **Manual Approval Demo**, select the pink waiting node, choose a decision, add an optional audit comment, and click **Continue execution**.

To demonstrate run-scoped variables, run **Run Variables Demo** with a customer ID. Its Set Variable node stores the numeric partner score as `customerScore`; the next node reads `{{ variables.customerScore }}`. Open **Variables** on the run page to inspect the value, type, revision, writer node, and update time.

To demonstrate encrypted third-party authentication, run **Credential Authentication Demo**
with `JOB-AUTH-1001`. The first HTTP node submits a protected job with the seeded
`demo_partner_api` Bearer credential, and HTTP Poll reads the latest credential revision on
every request until the protected job completes. Use `JOB-AUTH-DENY` to exercise the false
branch. The Run timeline records `CREDENTIAL_USED` with only alias, revision, node and origin.
The URL is built from `{{ flowConfig.partnerBaseUrl }}`, so this one demo covers both Flow
Configuration and Credentials.

To demonstrate an inbound asynchronous integration, run **HTTP Callback Demo**. When the purple
HTTP Callback node reaches `WAITING_CALLBACK`, select it on the Runs page and copy its unique
Callback URL. The worker lease has already been released. Send the callback with the seeded local
Bearer credential:

```powershell
$callbackUrl = "<URL shown on the waiting node>"
$headers = @{
  Authorization = "Bearer flowforge-local-demo-token"
  "Idempotency-Key" = "partner-event-001"
}
Invoke-RestMethod -Method Post -Uri $callbackUrl -Headers $headers `
  -ContentType "application/json" -Body '{"approved":true,"message":"Partner completed"}'
```

The run resumes without manual intervention and routes to `CALLBACK_APPROVED`. Send
`{"approved":false}` to exercise the denied branch. Repeating the same Idempotency-Key is safe;
a different key after consumption receives HTTP 409. Callback nodes also support API key header
and `X-FlowForge-Signature: sha256=<HMAC>` authentication using a selected Flow Credential.

Use **Schedules** in the designer to create a five-field cron trigger pinned to an immutable published version. Each schedule has its own timezone, validated input, enabled state, and next-run timestamp. The toolbar status selector controls whether a flow is `ACTIVE`, `PAUSED`, or `ARCHIVED`; paused and archived flows reject manual, scheduled, and rerun triggers while already-started runs continue.

The designer supports explicit node and connection deletion from the Properties panel, as well as the Delete and Backspace keys. The Runs page can manually start any published version from its generated input form. Completed runs can be rerun with the same pinned version and input from either the Runs table or the run detail page.

Flow input schemas are stored with both the mutable draft and every immutable version. They generate the manual-run form and are enforced again by FastAPI before a run is created. The Runs page can execute a selected historical version. The Versions panel compares nodes, connections, and input fields; rollback copies an old version into a new published version without rewriting history.

Flow Configuration stores non-sensitive versioned defaults. Manual runs and schedules can override
these values, and every run stores the validated merged snapshot. Nodes reference values with
`{{ flowConfig.partnerBaseUrl }}`; a full template preserves its JSON type. Reruns reuse the original
snapshot.

Use **Credentials** in the flow designer for Bearer, Basic, or API-key-header authentication. A node
stores only an immutable alias such as `partner_api`. Secrets are encrypted in PostgreSQL with
AES-256-GCM and are never returned by the API. HTTP Request and HTTP Poll load the newest revision
at node execution time, enforce exact allowed origins, and do not forward authentication across an
origin-changing redirect.

## Runtime architecture

```text
Browser / React Flow
        |
        | REST + SSE
        v
FastAPI control plane ------> PostgreSQL
                                  ^
                                  | durable queue + run state
                                  |
Python Worker --------------------+
Python Scheduler -----------------+
      |
      +------ HTTP ------> Demo Partner API
```

The API and worker use the same backend image and Python package but run as separate processes. FastAPI never executes a long-running node inside a request.

## Flow lifecycle

1. The editor saves a mutable draft.
2. Validation checks node implementations, JSON Schema configuration, ports, reachability, and cycles.
3. Publishing creates an immutable `FlowVersion` containing the graph and input schema.
4. Starting a run pins that version and creates a durable `NodeRun` for every node.
5. The worker claims ready nodes using `FOR UPDATE SKIP LOCKED`.
6. Node output activates downstream nodes. Condition outputs activate only their matching edge.
7. Worker failures are recovered after a lease expires; node failures use bounded retries.
8. Manual Approval nodes persist a `WAITING` state until an operator resumes or cancels the run.
9. Every run records its source (`MANUAL`, `SCHEDULE`, or `RERUN`), source ID, parent run, request time, and source metadata.
10. Set Variable nodes persist run-scoped JSON values with revision and writer audit data. Safe templates can reference `{{ input.path }}`, `{{ variables.name.path }}`, `{{ flowConfig.key }}`, and `{{ run.id }}` without evaluating arbitrary code.

## Services

| Service | Port | Purpose |
|---|---:|---|
| `frontend` | 3000 | React Flow designer and run monitor |
| `api` | 8000 | FastAPI control plane and SSE |
| `worker` | — | Durable Python node execution |
| `scheduler` | — | Cron evaluation and scheduled run creation |
| `partner-api` | 8001 | Local deterministic third-party API simulation |
| `database` | 5432 | PostgreSQL state store |
| `adminer` | 8080 | Local PostgreSQL schema/data browser |

## Development commands

Frontend:

Requires Node.js 22.12 or newer when running outside Docker.

```powershell
cd frontend
npm install
npm run dev
```

Backend tests through Docker:

```powershell
docker compose run --rm api sh -c "pip install -r requirements-dev.txt && pytest"
```

Frontend production build:

```powershell
cd frontend
npm run build
```

Rotate the credential encryption key after adding the new key to the Key Ring on API and Worker:

```powershell
docker compose exec api python -m app.security.rotate_credential_keys --to k2
```

The command locks and re-encrypts all revisions in one database transaction. Only remove the old key
after the command succeeds and API and Worker have restarted with the same Key Ring.

Reset all local data:

```powershell
docker compose down -v
```

The reset command permanently deletes the local PostgreSQL demo volume.

## Adding a Python node

Built-in nodes use one directory per versioned node type:

```text
backend/app/nodes/catalog/customer_lookup/
  __init__.py
  node.yaml
  handler.py
  test_handler.py
```

When two implementations of the same type must coexist, keep both directories, for example `customer_lookup_v1/` and `customer_lookup_v2/`, while their manifests declare `type: customer_lookup` and distinct versions. The directory name is only a package boundary; registry identity always comes from `(metadata.type, metadata.version)`.

`node.yaml` owns the UI metadata, ports, configuration schema, defaults, lifecycle, and the approved execution strategy:

```yaml
apiVersion: flowforge/v1
kind: NodeType
metadata:
  type: customer_lookup
  version: "1.0"
  name: Customer Lookup
  description: Loads customer information.
  lifecycle: active
spec:
  category: Business
  color: "#4f46e5"
  execution:
    kind: python
    handler: ".handler:CustomerLookupNode"
  inputs:
    - name: input
      label: Input
      dataType: object
  outputs:
    - name: output
      label: Customer
      dataType: object
  configSchema:
    type: object
    properties:
      includeHistory:
        type: boolean
        title: Include history
    required: [includeHistory]
    additionalProperties: false
  defaultConfig:
    includeHistory: false
```

The matching handler is ordinary trusted Python:

```python
class CustomerLookupNode:
    def execute(self, inputs, config, context):
        return {**inputs, "customer": load_customer(inputs["customerId"])}
```

Validate all built-in and installed node packages without starting a service:

```powershell
cd backend
python -m app.nodes.validate_registry
```

The loader uses safe YAML parsing, strict Pydantic models, JSON Schema validation, relative handler references, and a global `(type, version)` uniqueness check. API and Worker load the immutable registry once at startup and log the same SHA-256 fingerprint. Changing a manifest or handler requires restarting both processes. Published behavior must not be changed in place; introduce a new node version instead.

Set `metadata.lifecycle` to `deprecated` to hide a node from the designer palette while keeping it available to render and execute historical flows. The `/api/node-types` response returns both `lifecycle` and `availableForNewFlows`.

### Installing a trusted node package

An installed Python package can expose a package containing the same one-node-per-directory layout:

```toml
[project.entry-points."flowforge.node_providers"]
acme = "acme_flow_nodes.nodes"
```

Add the package to the backend image dependencies, rebuild API and Worker from the same image, run the registry validator, and restart both services. Package resources are read with `importlib.resources`, so standard wheels and zip-importable packages are supported.

Flow JSON cannot specify Python modules, file paths, URLs, or custom execution engines. Only code-approved strategies such as `python`, `manual_wait`, and `durable_poll` are accepted, and plugin packages must be treated as fully trusted application code. The UI intentionally cannot upload or install node code.

## Durable HTTP polling

The built-in `HTTP Poll` node repeatedly sends a GET request until a JSON response field matches the configured expectation. A poll performs exactly one request. If it does not match, the node enters `POLL_WAIT`, persists the last response and next execution time, releases its worker lease, and is promoted to `READY` when `available_at` is due. It never holds a worker with a sleep loop.

Example configuration:

```json
{
  "url": "http://partner-api:8001/customers/{customerId}/score",
  "responsePath": "status",
  "operator": "equals",
  "expectedValue": "COMPLETED",
  "intervalSeconds": 10,
  "maxPolls": 60,
  "requestTimeoutSeconds": 10
}
```

`responsePath` accepts nested dot paths such as `job.status.code`. Supported operators are `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal`, and `contains`. The UI stores `expectedValue` as text; the handler converts it to a number or boolean when the observed response value has that type.

The successful response is passed directly downstream with an additional `_poll` object containing the match decision, poll count, observed value, and expectation. A node fails clearly after `maxPolls`, while transport or response errors retry within the same total attempt budget. Runs can be cancelled while waiting. The run page displays the poll count, last response, and next poll timestamp.

## Run variables and templates

Run variables belong to one execution and never leak into another run. The Set Variable node supports `REPLACE`, `MERGE`, `APPEND`, and `INCREMENT`. Writes are serialized at the run row to prevent lost updates from parallel branches and emit a `VARIABLE_SET` audit event.

A full template preserves its JSON type, so `{{ input.score }}` stores a number rather than the string `"86"`. Embedded templates are converted to text. Secrets must not be stored as run variables; use a dedicated encrypted secrets store for credentials.

## Oracle path

The MVP intentionally uses PostgreSQL so the demo starts quickly and without Oracle image constraints. The backend uses SQLAlchemy and includes `python-oracledb` for a later Oracle adapter. Before production Oracle deployment, add an Oracle-specific Alembic migration, verify JSON/CLOB mappings, and load-test `SKIP LOCKED` task claiming against the target Oracle version.

For business transactions, keep all strongly consistent Oracle operations inside one Python node. Do not share a database transaction across workflow nodes.

## MVP boundaries

Included:

- DAG workflows, branches, joins, retries, cancellation, immutable versions, SSE updates.
- Registered Python nodes only.
- Durable HTTP polling that releases the worker between requests.
- Database-backed durable task claiming and short worker leases.
- Editor undo/redo, unsaved-change protection, schema-driven input forms, version comparison, rollback, and version-specific runs.
- Cron schedules, flow activation/pause/archive controls, manual waiting and continuation, and run provenance.
- Run-scoped variables, concurrency-safe writes, Set Variable nodes, safe templates, and a live Variables inspector.
- Single-administrator session authentication with HttpOnly cookies, CSRF protection, and login throttling.
- Versioned Flow Configuration plus encrypted, revisioned, flow-scoped HTTP Credentials.
- Adminer database inspection, a Mermaid ERD/table dictionary, and an authenticated Credential demo Flow.
- Durable HTTP Callback waits with unique URLs, Bearer/API-key/HMAC authentication, idempotency,
  cancellation, timeout handling, live UI guidance, and a seeded callback demo Flow.

Not included:

- Multi-tenancy, fine-grained roles, OAuth2 credentials, Vault, or cloud KMS integration.
- Arbitrary loops or long-running in-worker timers.
- Arbitrary Python or SQL entered in the UI.
- Large artifact storage or distributed transactions.
