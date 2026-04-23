import type { DesktopToolInvocation, DesktopToolPolicy, DesktopToolSpec } from '../../lib/api';
import { truncate } from '../../lib/format';
import { CAPABILITIES, type CapabilitySpec } from './constants';

export interface RunLogEntry {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  result: DesktopToolInvocation['result'];
  ts: number;
}

export function statusTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'success') return 'ok';
  if (status === 'denied') return 'warn';
  if (status === 'error') return 'err';
  return 'muted';
}

export function capabilityChecklist(policy: DesktopToolPolicy): {
  ready: boolean;
  messages: string[];
} {
  const messages: string[] = [];
  if (!policy.enabled) {
    messages.push('Master switch is OFF. Every desktop invocation will short-circuit.');
  }
  if (policy.allow_shell && policy.allowed_shell_commands.length === 0) {
    messages.push('Shell is enabled, but the allowlist is empty. Shell runs will still be denied.');
  }
  if (policy.allow_filesystem && !policy.filesystem_root) {
    messages.push(
      'Filesystem access is enabled without a root, so every path resolution will fail.'
    );
  }
  if (policy.require_approval_for.length > 0) {
    messages.push(
      `Operator approval is required for ${policy.require_approval_for.join(', ')} (${policy.approval_timeout_seconds}s timeout).`
    );
  }
  return { ready: messages.length === 0, messages };
}

export function capabilityForTool(toolName: string): CapabilitySpec | undefined {
  return CAPABILITIES.find((capability) => capability.toolNames.includes(toolName));
}

export function resultMimeType(result: DesktopToolInvocation['result']): string {
  const format = typeof result.format === 'string' ? result.format.toLowerCase() : 'png';
  if (format === 'jpeg' || format === 'jpg') return 'image/jpeg';
  if (format === 'webp') return 'image/webp';
  return 'image/png';
}

export function compactArgs(args: Record<string, unknown>): string {
  return truncate(JSON.stringify(args), 120);
}

export function exportedToolNames(tools: DesktopToolSpec[]): Set<string> {
  return new Set(tools.map((tool) => tool.name));
}

export function requiredFields(spec: DesktopToolSpec): string {
  const required = spec.input_schema.required ?? [];
  if (required.length === 0) return 'No required args';
  if (required.length === 1) return `Requires ${required[0]}`;
  return `Requires ${required.join(', ')}`;
}

export function summarizeResult(result: DesktopToolInvocation['result']): string {
  if (typeof result.error === 'string' && result.error.trim()) return result.error;
  if (typeof result.stdout === 'string' && result.stdout.trim()) return truncate(result.stdout, 96);
  if (typeof result.stderr === 'string' && result.stderr.trim()) return truncate(result.stderr, 96);
  if (typeof result.content === 'string' && result.content.trim())
    return truncate(result.content, 96);
  if (Array.isArray(result.entries)) return `${result.entries.length} filesystem entries`;
  if (typeof result.return_code === 'number') return `return code ${result.return_code}`;
  return truncate(JSON.stringify(result), 96);
}

export function screenshotless(
  result: DesktopToolInvocation['result']
): DesktopToolInvocation['result'] {
  if (!('screenshot_base64' in result)) return result;
  return {
    ...result,
    screenshot_base64: '[base64 omitted]',
  };
}
