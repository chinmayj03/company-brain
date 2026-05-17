-- Seed data for default workspace
INSERT INTO workspaces (id, name, slug)
VALUES ('00000000-0000-0000-0000-000000000001', 'Demo Workspace', 'demo');

INSERT INTO workspace_sources (id, workspace_id, kind, display_name, sync_status)
VALUES
    ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'github', 'company-brain', 'ok'),
    ('10000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'jira', 'BRAIN project', 'ok');
