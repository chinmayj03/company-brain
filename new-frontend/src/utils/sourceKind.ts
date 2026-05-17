export const KIND_LABEL: Record<string, string> = {
  git_local:     'GIT',
  git_remote:    'GIT',
  openapi:       'API',
  confluence:    'CF ',
  db_migrations: 'SQL',
  github_prs:    'PR ',
  slack_channel: 'SLK',
  notion:        'NOT',
  jira:          'JRA',
  datadog:       'DDG',
};

export function sourceKindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.slice(0, 3).toUpperCase();
}

export const KIND_AVAILABLE: Record<string, boolean> = {
  git_local:     true,
  git_remote:    true,
  openapi:       true,
  confluence:    true,
  db_migrations: true,
  github_prs:    true,
  slack_channel: false,
  notion:        false,
  jira:          false,
  datadog:       false,
};
