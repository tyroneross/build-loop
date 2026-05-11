# UI Input/Output Contract

Use this contract for any build-loop task that adds, modifies, or validates a user-facing UI surface. It turns "build the component" into an explicit inventory of what the user can provide, what the system returns, and which UI pattern must handle each data shape.

## When It Applies

Apply this when `uiTarget != null` and the task changes any screen, component, form, table, chart, voice/audio affordance, file workflow, generated output, or streamed response. Skip only for copy-only changes that do not alter the available inputs, outputs, states, or operations.

## Required Plan Section

Every UI plan must include a `## UI Input/Output Contract` section before implementation. Use one row per affected screen or component, and keep each field concrete.

| Field | Required Answer |
|---|---|
| Surface | Screen/component name plus file path |
| User inputs | Every value the user can provide |
| System outputs | Every value the user receives or decides from |
| Data taxonomy | Structural type, content format, persistence intent |
| Operation model | CRUD operation plus any domain verb |
| Component mapping | Exact input control and output renderer |
| State matrix | Empty, populated, focused, disabled, loading, success, error, and streaming/abort when relevant |
| Modality | Text, voice, file, vision, chart, map, AI/generated, or streaming; include fallback |
| Validation/security | Presentation, application, and domain validation; sanitization; auth/authz display behavior |
| Traceability | Data schema, API endpoint/method, design-system component, and rationale |

## Data Taxonomy

Classify each input and output before choosing UI controls:

- **Structural type**: scalar, structured object/array, binary, stream.
- **Content format**: plain text, Markdown, rich text, HTML, JSON tree, chart data, audio, image, map/geospatial.
- **Persistence intent**: persisted CRUD data, transient session state, real-time stream, computed/derived output.

## Operation Model

Name the operation the UI performs:

- **Create**: POST, form, wizard, inline add.
- **Read**: GET, table, card, detail view, chart.
- **Update**: PUT/PATCH, edit form, inline edit, toggle.
- **Delete**: DELETE, destructive button, confirmation.
- **Domain verbs**: submit, approve, publish, escalate, cancel, refund, reorder, filter, sort, export, download.

Do not hide domain verbs inside generic "update" language when rules, side effects, or affordances differ.

## Component Mapping

Choose components from the data shape, not habit.

| Data Shape | Input Control | Output Renderer |
|---|---|---|
| Short string | Single-line input | Text with overflow/copy behavior |
| Long plain text | Textarea with sizing policy | Paragraph or preformatted text |
| Markdown | Split write/preview editor | Sanitized Markdown renderer |
| Rich text | Schema-backed editor | Paired rich-text viewer |
| JSON/object | Schema-aware JSON editor/tree | Collapsible tree or raw/parsed toggle |
| Number | Number input, slider, or stepper | Locale-aware number display |
| Boolean | Toggle, checkbox, or yes/no radio | Explicit state label |
| Date/time | Date/time/range picker | Locale/timezone-aware display |
| Enum | Select, radio group, or searchable select when options exceed seven | Label/chip/list value |
| File/binary | Upload/dropzone with progress | Preview/download with file metadata |
| Voice/audio | Mic button with waveform and text fallback | Audio player/TTS plus transcript |
| Tabular data | Filters/search feeding table | Sort/filter/paginated table |
| Chart data | Form/filter inputs feeding visualization | Named chart type plus table fallback |
| Streaming/AI output | Prompt or structured input | Token/partial renderer with abort and retry |
| Geospatial | Address/map controls | Map with markers/clusters and fallback text |

## State Matrix

Document the states each component must render:

- Default/empty.
- Populated/filled.
- Focused.
- Hover on pointer devices.
- Active/pressed.
- Disabled/permission-blocked.
- Loading.
- Success.
- Error/invalid.
- Empty result.
- Streaming/partial with abort and retry behavior when relevant.

## Modalities

When a modality exists, it needs its own UI and failure path:

- **Text**: text input, textarea, rich text, text/Markdown renderer.
- **Voice**: push-to-talk or wake-word trigger, ASR provider/threshold if known, transcript fallback, TTS controls.
- **File**: upload control, MIME/size rules, preview/download, copy-text fallback.
- **Vision/image**: image upload/camera, viewer/annotation, alt-text fallback.
- **Chart/graph**: chart type, data schema, axis labels, colorblind-safe palette, table fallback.
- **AI/generated output**: output type contract, response schema/template, streaming vs complete mode, abort/retry behavior.

## Validation And Security

Validation must be named by layer:

- **Presentation**: required fields, max length, pattern, type, inline field errors.
- **Application**: cross-field and business-rule errors, form-level/toast surface.
- **Domain**: invariant failures, system-error surface, and whether repeated domain failures indicate a spec gap.
- **Sanitization**: allowed subset for Markdown, rich text, HTML, JSON, and generated content.
- **Auth/authz**: permission required for create/update/delete/domain verbs, and UI behavior when denied: hidden, disabled, or 403/empty view.

## Traceability

Each UI element that accepts or returns data must trace to:

- Data model or schema version.
- API endpoint/method or local data source.
- Design-system component or explicit net-new rationale.
- Test or validation evidence.

If the trace cannot be named, the plan is incomplete.
