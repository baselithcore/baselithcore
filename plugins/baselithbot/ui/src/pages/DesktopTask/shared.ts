import type { DesktopToolPolicy, DesktopToolSpec } from '../../lib/api';
import type { CapabilityKey } from './constants';

export interface DesktopShared {
  policy: DesktopToolPolicy;
  tools: DesktopToolSpec[];
  toolMap: Map<string, DesktopToolSpec>;
  canUse: (toolName: string, capability: CapabilityKey) => boolean;
  invoke: (tool: string, args: Record<string, unknown>) => void;
  invokePending: boolean;
  launcherBinary: string | null;
}
