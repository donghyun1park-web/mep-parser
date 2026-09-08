# Vite review workspace implementation

Accepted scope: Vite frontend, linked original DXF and 3D selection, horizontal section control, actionable review list. Existing project autosave, offline export, edits, geometry contract, and IFC evidence gates remain authoritative.

1. Extract the current frontend to vanilla JavaScript/CSS/HTML. Vite builds a pinned, self-contained bundle using the existing Three.js r160. Python embeds built assets and runtime data; end users need neither Node nor internet.
2. Read actual DXF primitives for a source backdrop, disclose unsupported/truncated content, preserve source signature references, and transform each floor into the same world millimetres as geometry. Source background and derived element overlays are visually distinct. Selection is by EID overlay; ambiguous source matches never silently choose an EID.
3. Add linked source/3D mode, floor selector, section height, and review queue. Viewing never changes geometry or acknowledges issues. Edits refresh review state and preserve existing save/recovery behavior.
4. Test source transforms/omissions, bundle/data isolation, review/section selection logic, server security and consistency. Build production bundle, exercise the real browser and representative drawings, run full regression, then apply only this turn's delta with a recoverable backup.

Deferred: full 3D MEP editing, drawing revision diff, actual IFC mesh rendering, cloud collaboration.
