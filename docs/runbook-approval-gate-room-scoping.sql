-- Approval-gate room-scope detection report
--
-- Run with: psql "$DATABASE_URL" -f docs/runbook-approval-gate-room-scoping.sql
-- This file contains SELECT statements only. It does not modify customer data.

-- Legacy approval-gate node configuration carrying chatroom_id. Current schema
-- rejects this key; these rows can only predate that constraint or bypass it.
WITH configured_rooms AS (
    SELECT
        w.id AS workflow_id,
        owner_workspace.project_id AS workflow_project_id,
        node ->> 'id' AS node_id,
        node -> 'config' ->> 'chatroom_id' AS requested_chatroom_id
    FROM workflows AS w
    JOIN workspaces AS owner_workspace ON owner_workspace.id = w.workspace_id
    CROSS JOIN LATERAL jsonb_array_elements(w.definition -> 'nodes') AS node
    WHERE w.deleted_at IS NULL
      AND owner_workspace.deleted_at IS NULL
      AND node ->> 'type' = 'approval_gate'
      AND node -> 'config' ? 'chatroom_id'
)
SELECT
    configured_rooms.*,
    CASE
        WHEN requested_chatroom_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN 'malformed'
        WHEN room.id IS NULL OR room.deleted_at IS NOT NULL OR room_workspace.deleted_at IS NOT NULL
            THEN 'unknown_or_deleted'
        WHEN room_workspace.project_id <> workflow_project_id
            THEN 'outside_workflow_project'
        ELSE 'in_scope'
    END AS scope_status
FROM configured_rooms
LEFT JOIN chatrooms AS room
    ON room.id = CASE
        WHEN requested_chatroom_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN requested_chatroom_id::uuid
    END
LEFT JOIN workspaces AS room_workspace ON room_workspace.id = room.workspace_id
ORDER BY workflow_id, node_id;

-- Persisted trigger payloads can be retried or resumed after deployment. These
-- rows quantify historical exposure and identify executions that will now fail
-- closed when they reach an approval gate.
WITH triggered_rooms AS (
    SELECT
        run.id AS workflow_run_id,
        run.workflow_id,
        run.project_id AS workflow_project_id,
        run.context -> 'trigger_payload' ->> 'chatroom_id' AS requested_chatroom_id,
        run.state,
        run.started_at
    FROM workflow_runs AS run
    WHERE run.context -> 'trigger_payload' ? 'chatroom_id'
)
SELECT
    triggered_rooms.*,
    CASE
        WHEN requested_chatroom_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN 'malformed'
        WHEN room.id IS NULL OR room.deleted_at IS NOT NULL OR room_workspace.deleted_at IS NOT NULL
            THEN 'unknown_or_deleted'
        WHEN room_workspace.project_id <> workflow_project_id
            THEN 'outside_run_project'
        ELSE 'in_scope'
    END AS scope_status
FROM triggered_rooms
LEFT JOIN chatrooms AS room
    ON room.id = CASE
        WHEN requested_chatroom_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN requested_chatroom_id::uuid
    END
LEFT JOIN workspaces AS room_workspace ON room_workspace.id = room.workspace_id
ORDER BY started_at DESC, workflow_run_id;
