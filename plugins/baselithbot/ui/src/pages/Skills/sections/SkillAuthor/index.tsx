import { Panel } from '../../../../components/Panel';
import { Icon, paths } from '../../../../lib/icons';
import { useSkillAuthorForm } from './hooks';
import { OpenClawFields } from './OpenClawFields';
import { SURFACES } from './types';
import type { SkillAuthorProps } from './types';

export function SkillAuthor({
  workspaces,
  installedSlugs,
  pending,
  lastCreated,
  onSubmit,
}: SkillAuthorProps) {
  const form = useSkillAuthorForm({ installedSlugs, pending, onSubmit });
  const { expanded, setExpanded, openclaw } = form;

  return (
    <Panel title="Author custom skill" tag={expanded ? 'composer' : 'closed'}>
      <div className="skills-callout">
        <div className="skills-callout-title">Create a workspace skill from the UI</div>
        <div className="skills-callout-body">
          The composer writes a validated <code>SKILL.md</code> (YAML frontmatter + instructions)
          and <code>MANIFEST.yaml</code> (bundle version, supported surfaces, passing{' '}
          <code>tested_on</code> entry) bundle, then rescans the workspace so the registry reflects
          the new skill immediately.
        </div>
      </div>

      {!expanded ? (
        <button
          type="button"
          className="btn primary"
          onClick={() => setExpanded(true)}
          style={{ marginTop: 12 }}
        >
          <Icon path={paths.plus} size={12} />
          New custom skill
        </button>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12 }}>
          <div className="form-row">
            <label htmlFor="skill-author-slug">Slug</label>
            <input
              id="skill-author-slug"
              className="input"
              placeholder="kebab-or-snake-case, e.g. lead-qualifier"
              value={form.slug}
              onChange={(event) => form.setSlug(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            {!form.slugValid && (
              <div className="info-block" style={{ color: 'var(--danger, #d14)' }}>
                Slug must be 2-63 chars: lowercase letters, digits, <code>-</code> or
                <code> _</code>, starting with a letter or digit.
              </div>
            )}
            {form.slugCollides && (
              <div className="info-block" style={{ color: 'var(--warn, #c90)' }}>
                A skill with this slug already exists. Toggle "overwrite" to replace it.
              </div>
            )}
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-name">Display name</label>
            <input
              id="skill-author-name"
              className="input"
              placeholder="Lead Qualifier"
              value={form.name}
              onChange={(event) => form.setName(event.target.value)}
              maxLength={120}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-description">Short description</label>
            <input
              id="skill-author-description"
              className="input"
              placeholder="One-sentence trigger describing when the agent should load this skill."
              value={form.description}
              onChange={(event) => form.setDescription(event.target.value)}
              maxLength={500}
            />
            <div className="info-block">
              Tight triggers help the orchestrator decide when to activate the skill.
            </div>
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-version">Version</label>
            <input
              id="skill-author-version"
              className="input"
              placeholder="0.1.0"
              value={form.version}
              onChange={(event) => form.setVersion(event.target.value)}
              maxLength={32}
            />
          </div>

          <div className="form-row">
            <label>Surfaces</label>
            <div className="skills-chip-row">
              {SURFACES.map((surface) => {
                const active = form.surfaces.includes(surface);
                return (
                  <button
                    key={surface}
                    type="button"
                    className={`badge ${active ? 'ok' : 'muted'}`}
                    onClick={() => form.toggleSurface(surface)}
                    style={{ cursor: 'pointer' }}
                  >
                    {surface}
                  </button>
                );
              })}
            </div>
            <div className="info-block">
              Select every surface the skill was authored for. At least one is required.
            </div>
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-tags">Tags (comma separated)</label>
            <input
              id="skill-author-tags"
              className="input"
              placeholder="sales, crm, qualification"
              value={form.tagsInput}
              onChange={(event) => form.setTagsInput(event.target.value)}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-workspace">Workspace</label>
            <select
              id="skill-author-workspace"
              className="input"
              value={form.workspace}
              onChange={(event) => form.setWorkspace(event.target.value)}
            >
              <option value="">Global (state root)</option>
              {workspaces.map((ws) => (
                <option key={ws.name} value={ws.name}>
                  {ws.name}
                  {ws.primary ? ' · primary' : ''}
                </option>
              ))}
            </select>
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-instructions">Instructions (SKILL.md body)</label>
            <textarea
              id="skill-author-instructions"
              className="input"
              rows={14}
              value={form.instructions}
              onChange={(event) => form.setInstructions(event.target.value)}
              style={{ fontFamily: 'var(--font-mono, ui-monospace, monospace)', minHeight: 240 }}
              maxLength={32_000}
            />
            <div className="info-block">
              Markdown is injected into the agent prompt when the skill activates. Lead with "when
              to use", then steps, then an output contract.
            </div>
          </div>

          <OpenClawFields oc={openclaw} />

          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={form.overwrite}
              onChange={(event) => form.setOverwrite(event.target.checked)}
            />
            <span>Overwrite existing bundle with the same slug</span>
          </label>

          <div className="inline" style={{ gap: 8 }}>
            <button
              type="button"
              className="btn primary"
              disabled={!form.canSubmit}
              onClick={form.handleSubmit}
            >
              <Icon path={paths.plus} size={12} />
              {pending ? 'Creating…' : 'Create skill'}
            </button>
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => {
                form.reset();
                setExpanded(false);
              }}
              disabled={pending}
            >
              Cancel
            </button>
            <button type="button" className="btn ghost sm" onClick={form.reset} disabled={pending}>
              Reset form
            </button>
          </div>

          {lastCreated && (
            <div className="info-block" style={{ color: 'var(--ok, #2a7)' }}>
              Last created: <strong>{lastCreated.name}</strong> (slug{' '}
              <code>{lastCreated.slug}</code>, validation{' '}
              <code>{lastCreated.validation.status}</code>).
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
