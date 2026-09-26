const SAFE_LINK_PROTOCOLS = new Set(['http:', 'https:']);

/**
 * Return `raw` only when it is an absolute http(s) URL, otherwise `null`.
 *
 * Canvas widgets, run results and other agent/LLM output reach the DOM as
 * link targets. React does not block `javascript:`/`data:`/`vbscript:` hrefs
 * (it only warns in development), and the page CSP is the only thing standing
 * between such a link and script execution. Rendering untrusted URLs through
 * this guard keeps that a defence-in-depth layer instead of the only one.
 */
export function safeExternalUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    return SAFE_LINK_PROTOCOLS.has(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}
