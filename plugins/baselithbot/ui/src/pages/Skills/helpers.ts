import { ApiError, type Skill, type WorkspaceSkillReport } from '../../lib/api';

export const SCOPES = ['', 'bundled', 'managed', 'workspace'] as const;
export const SCOPE_ORDER = ['bundled', 'managed', 'workspace'] as const;

export type ScopeName = (typeof SCOPE_ORDER)[number];
export type SortKey = 'name' | 'scope' | 'version';
export type CatalogSkill = {
  name: string;
  version: string;
  description: string;
  entrypoint: string | null;
};

export function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

export function normalizeCatalogEntry(entry: Record<string, unknown>): CatalogSkill | null {
  const name = typeof entry.name === 'string' ? entry.name.trim() : '';
  if (!name) return null;
  return {
    name,
    version: typeof entry.version === 'string' && entry.version.trim() ? entry.version : '0.0.0',
    description: typeof entry.description === 'string' ? entry.description : '',
    entrypoint: typeof entry.entrypoint === 'string' ? entry.entrypoint : null,
  };
}

export function scopeTone(scope: ScopeName): 'ok' | 'warn' | 'muted' {
  if (scope === 'managed') return 'ok';
  if (scope === 'workspace') return 'warn';
  return 'muted';
}

export function scopeLabel(scope: ScopeName): string {
  return scope.charAt(0).toUpperCase() + scope.slice(1);
}

function readSourceCount(skill: Skill): number {
  const sources = skill.metadata?.sources;
  if (!sources || typeof sources !== 'object' || Array.isArray(sources)) return 0;
  return Object.keys(sources).length;
}

export function skillSummary(skill: Skill): string {
  const kind = typeof skill.metadata?.kind === 'string' ? skill.metadata.kind : '';
  if (skill.scope === 'bundled') return 'Native Baselithbot capability';
  if (skill.scope === 'managed') {
    return skill.metadata?.source === 'clawhub'
      ? 'Installed from ClawHub'
      : 'Managed registry skill';
  }
  if (kind === 'custom_skill') return 'Custom local skill bundle';
  const sourceCount = readSourceCount(skill);
  if (sourceCount > 0) {
    return `${sourceCount} prompt file${sourceCount === 1 ? '' : 's'} loaded from workspace`;
  }
  return 'Workspace prompt bundle';
}

export function skillMetaBadges(skill: Skill): string[] {
  const badges = [`v${skill.version}`];
  const validation = skill.metadata?.validation;
  const validationStatus =
    validation && typeof validation === 'object' && 'status' in validation
      ? String(validation.status)
      : '';
  if (skill.scope === 'managed' && skill.metadata?.source === 'clawhub') {
    badges.push('ClawHub');
  }
  if (skill.scope === 'workspace') {
    if (validationStatus) badges.push(validationStatus);
    const sourceCount = readSourceCount(skill);
    if (sourceCount > 0) badges.push(`${sourceCount} source${sourceCount === 1 ? '' : 's'}`);
  }
  return badges;
}

export function validationTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'verified') return 'ok';
  if (status === 'provisional') return 'warn';
  if (status === 'invalid') return 'err';
  return 'muted';
}

export function workspaceReportSummary(report: WorkspaceSkillReport): string {
  if (report.kind === 'prompt_bundle') return 'Legacy workspace prompt bundle';
  if (report.validation.status === 'invalid') return 'Rejected during registration';
  if (report.validation.status === 'provisional') return 'Registered with validation warnings';
  return 'Registered and structurally verified';
}

export function MetaTileValue(value: string | undefined | null): string {
  return value || '—';
}
