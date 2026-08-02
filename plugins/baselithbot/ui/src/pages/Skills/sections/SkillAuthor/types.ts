import type {
  OpenClawFrontmatterPayload,
  WorkspaceInfo,
  WorkspaceSkillSpec,
} from '../../../../lib/api';

export const SURFACES = ['chat', 'cli', 'ide'] as const;
export type Surface = (typeof SURFACES)[number];

export const OPENCLAW_OS = ['darwin', 'linux', 'win32'] as const;
export type OpenClawOs = (typeof OPENCLAW_OS)[number];

export const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,62}$/;

export const DEFAULT_TEMPLATE = `# When to use

Describe a single precise trigger so the agent knows when to load this skill.

# Instructions

- Step 1: …
- Step 2: …
- Step 3: …

# Output contract

Describe the expected output shape, tone, or format.
`;

export interface SkillAuthorSubmitPayload {
  slug: string;
  name: string;
  description: string;
  version: string;
  instructions: string;
  surfaces: string[];
  tags: string[];
  workspace: string | null;
  overwrite: boolean;
  openclaw: OpenClawFrontmatterPayload | null;
}

export interface SkillAuthorProps {
  workspaces: WorkspaceInfo[];
  installedSlugs: Set<string>;
  pending: boolean;
  lastCreated: WorkspaceSkillSpec | null;
  onSubmit: (payload: SkillAuthorSubmitPayload) => void;
}
