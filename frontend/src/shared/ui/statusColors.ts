export const STATUS_BG_MAP: Record<string, string> = {
  running: 'bg-info-tint text-info-on',
  waiting: 'bg-warning-tint text-warning-on',
  succeeded: 'bg-success-tint text-success-on',
  completed: 'bg-success-tint text-success-on',
  approved: 'bg-success-tint text-success-on',
  failed: 'bg-danger-tint text-danger-on',
  rejected: 'bg-danger-tint text-danger-on',
  error: 'bg-danger-tint text-danger-on',
  cancelled: 'bg-neutral-tint text-neutral-on',
  skipped: 'bg-neutral-tint text-neutral-on',
  pending: 'bg-neutral-tint text-muted',
  idle: 'bg-neutral-tint text-muted',
  timeout: 'bg-warning-tint text-warning-on',
  timeout_leader: 'bg-warning-tint text-warning-on',
}

export const STATUS_BG_DEFAULT = 'bg-neutral-tint text-neutral-on'
