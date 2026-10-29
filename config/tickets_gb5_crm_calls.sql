-- Read-only ticket query for GoodBooks CRM Service calls (table TCALL).
-- Use it if your support team logs customer tickets in GoodBooks CRM Service.
-- Column aliases map to PIE ticket fields: ticket_id, title, description, module,
-- status, priority, category, created_at, resolution.
-- Priority and nature are numeric IDs here; join their master tables if you want names.
-- Personal fields (reporter, contact, party) are deliberately not selected.
SELECT
    c.CALLNUMBER       AS ticket_id,
    c.REPORTEDPROBLEM  AS description,
    c.ACTIONTAKEN      AS resolution,
    c.CALLSTATUS       AS status,
    c.CALLPRIORITYID   AS priority,
    c.CALLNATUREID     AS category,
    c.CALLDATE         AS created_at
FROM dbo.TCALL AS c
