-- Confirms the ALTER TABLE ADD COLUMN path of schema_sql.py.

ALTER TABLE plan_info ADD COLUMN lob text;
ALTER TABLE plan_info ADD COLUMN deactivated boolean DEFAULT FALSE;
