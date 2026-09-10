---
type: feature
status: draft
created: 2026-09-10
requirements: []
depends_on: [2026-09-10-collaborative-canvas]
---

# Canvas Templates

## 1. Summary

Provide pre-built canvas templates (layouts with placeholder objects) that users can
apply when starting a new canvas or resetting an existing one. Templates reduce the
blank-canvas barrier and offer structured starting points for common collaboration
scenarios (brainstorming, retrospective, SWOT analysis, mind map).

## 2. Goals and Non-goals

**Goals**

- A set of platform-provided canvas templates selectable at canvas creation.
- Templates define a layout of pre-positioned objects (notes, text, shapes, connectors).
- Users can apply a template to an empty canvas or choose to start blank.
- Templates are stored as JSON definitions, not as full canvas snapshots.

**Non-goals**

- User-created custom templates (platform-provided only in this phase).
- Template marketplace or sharing between organizations.
- Template versioning or migration.

## 3. Acceptance Criteria

- [ ] AC-1: When opening an empty canvas, the user sees a template picker with at least
  3 built-in templates.
- [ ] AC-2: Selecting a template populates the canvas with the template's objects.
- [ ] AC-3: The user can dismiss the picker and start with a blank canvas.
- [ ] AC-4: Template objects are fully editable after placement (they become regular
  canvas objects).

## 4. Key Technical Decisions

- Templates stored as static JSON in the frontend bundle (no backend storage needed).
- Template application creates real canvas objects via the batch endpoint.

## 5. Detailed Changes

_To be filled during `/spec` analysis._

## 6. Test Plan

_To be filled during `/spec` analysis._
