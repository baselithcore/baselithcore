import type { CustomAgentPayload } from '../../lib/api';

export interface CustomAgentDraft {
  name: string;
  description: string;
  keywordsRaw: string;
  priority: string;
  actionType: string;
  slashCommand: string;
  webhookUrl: string;
  webhookHeaders: string;
  webhookTimeout: string;
  staticPayload: string;
}

export type CustomAgentBuildResult =
  | { ok: true; payload: CustomAgentPayload }
  | { ok: false; error: string };

export function buildCustomAgentPayload(draft: CustomAgentDraft): CustomAgentBuildResult {
  const trimmedName = draft.name.trim();
  if (!trimmedName) {
    return { ok: false, error: 'Name is required.' };
  }
  const pri = Number(draft.priority);
  if (!Number.isFinite(pri) || pri < 0 || pri > 10_000) {
    return { ok: false, error: 'Priority must be between 0 and 10000.' };
  }
  const keywords = draft.keywordsRaw
    .split(',')
    .map((kw) => kw.trim())
    .filter(Boolean);

  let params: Record<string, unknown>;
  try {
    if (draft.actionType === 'chat_command') {
      if (!draft.slashCommand.startsWith('/')) {
        throw new Error("Command must start with '/'.");
      }
      params = { command: draft.slashCommand };
    } else if (draft.actionType === 'http_webhook') {
      if (!draft.webhookUrl.startsWith('http')) {
        throw new Error('URL must start with http:// or https://');
      }
      const headers = draft.webhookHeaders.trim() ? JSON.parse(draft.webhookHeaders) : {};
      const timeoutN = Number(draft.webhookTimeout);
      if (!Number.isFinite(timeoutN) || timeoutN < 1 || timeoutN > 60) {
        throw new Error('Timeout must be between 1 and 60 seconds.');
      }
      params = { url: draft.webhookUrl, headers, timeout_seconds: timeoutN };
    } else if (draft.actionType === 'static_response') {
      const payload = draft.staticPayload.trim() ? JSON.parse(draft.staticPayload) : {};
      if (typeof payload !== 'object' || Array.isArray(payload) || payload === null) {
        throw new Error('Static payload must be a JSON object.');
      }
      params = { payload };
    } else {
      throw new Error(`Unsupported action '${draft.actionType}'.`);
    }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }

  return {
    ok: true,
    payload: {
      name: trimmedName,
      description: draft.description.trim(),
      keywords,
      priority: pri,
      metadata: {},
      action: { type: draft.actionType, params },
    },
  };
}

export function dispatchResultStatus(result: unknown): string {
  if (result && typeof result === 'object') {
    const status = (result as { status?: unknown }).status;
    if (typeof status === 'string') {
      return status;
    }
  }
  return 'ok';
}
