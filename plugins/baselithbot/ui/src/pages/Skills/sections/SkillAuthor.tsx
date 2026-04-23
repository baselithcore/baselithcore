import { useMemo, useState } from 'react';
import { Panel } from '../../../components/Panel';
import type {
  OpenClawFrontmatterPayload,
  WorkspaceInfo,
  WorkspaceSkillSpec,
} from '../../../lib/api';
import { Icon, paths } from '../../../lib/icons';

const SURFACES = ['chat', 'cli', 'ide'] as const;
type Surface = (typeof SURFACES)[number];

const OPENCLAW_OS = ['darwin', 'linux', 'win32'] as const;
type OpenClawOs = (typeof OPENCLAW_OS)[number];

const SLUG_RE = /^[a-z0-9][a-z0-9_-]{1,62}$/;

const DEFAULT_TEMPLATE = `# When to use

Describe a single precise trigger so the agent knows when to load this skill.

# Instructions

- Step 1: …
- Step 2: …
- Step 3: …

# Output contract

Describe the expected output shape, tone, or format.
`;

interface SkillAuthorProps {
  workspaces: WorkspaceInfo[];
  installedSlugs: Set<string>;
  pending: boolean;
  lastCreated: WorkspaceSkillSpec | null;
  onSubmit: (payload: {
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
  }) => void;
}

export function SkillAuthor({
  workspaces,
  installedSlugs,
  pending,
  lastCreated,
  onSubmit,
}: SkillAuthorProps) {
  const [expanded, setExpanded] = useState(false);
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [version, setVersion] = useState('0.1.0');
  const [instructions, setInstructions] = useState(DEFAULT_TEMPLATE);
  const [surfaces, setSurfaces] = useState<Surface[]>(['chat']);
  const [tagsInput, setTagsInput] = useState('');
  const [workspace, setWorkspace] = useState<string>('');
  const [overwrite, setOverwrite] = useState(false);

  const [openclawEnabled, setOpenclawEnabled] = useState(false);
  const [ocHomepage, setOcHomepage] = useState('');
  const [ocUserInvocable, setOcUserInvocable] = useState(true);
  const [ocDisableModel, setOcDisableModel] = useState(false);
  const [ocDispatch, setOcDispatch] = useState<'' | 'tool'>('');
  const [ocCommandTool, setOcCommandTool] = useState('');
  const [ocCommandArgMode, setOcCommandArgMode] = useState<'' | 'raw'>('');
  const [ocAlways, setOcAlways] = useState(false);
  const [ocEmoji, setOcEmoji] = useState('');
  const [ocOs, setOcOs] = useState<OpenClawOs[]>([]);
  const [ocPrimaryEnv, setOcPrimaryEnv] = useState('');
  const [ocSkillKey, setOcSkillKey] = useState('');
  const [ocReqBins, setOcReqBins] = useState('');
  const [ocReqAnyBins, setOcReqAnyBins] = useState('');
  const [ocReqEnv, setOcReqEnv] = useState('');
  const [ocReqConfig, setOcReqConfig] = useState('');

  const tags = useMemo(
    () =>
      tagsInput
        .split(',')
        .map((tag) => tag.trim().toLowerCase())
        .filter((tag) => tag.length > 0 && tag.length <= 48),
    [tagsInput]
  );

  const splitList = (raw: string): string[] =>
    raw
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0);

  const openclawPayload = useMemo<OpenClawFrontmatterPayload | null>(() => {
    if (!openclawEnabled) return null;
    const bins = splitList(ocReqBins);
    const anyBins = splitList(ocReqAnyBins);
    const env = splitList(ocReqEnv);
    const config = splitList(ocReqConfig);
    return {
      homepage: ocHomepage.trim() || null,
      user_invocable: ocUserInvocable,
      disable_model_invocation: ocDisableModel,
      command_dispatch: ocDispatch || null,
      command_tool: ocCommandTool.trim() || null,
      command_arg_mode: ocCommandArgMode || null,
      always: ocAlways,
      emoji: ocEmoji.trim() || null,
      os: [...ocOs],
      primary_env: ocPrimaryEnv.trim() || null,
      skill_key: ocSkillKey.trim() || null,
      requires: { bins, any_bins: anyBins, env, config },
      install: [],
    };
  }, [
    openclawEnabled,
    ocHomepage,
    ocUserInvocable,
    ocDisableModel,
    ocDispatch,
    ocCommandTool,
    ocCommandArgMode,
    ocAlways,
    ocEmoji,
    ocOs,
    ocPrimaryEnv,
    ocSkillKey,
    ocReqBins,
    ocReqAnyBins,
    ocReqEnv,
    ocReqConfig,
  ]);

  const dispatchConsistent =
    !openclawEnabled || ocDispatch !== 'tool' || ocCommandTool.trim().length > 0;

  const slugClean = slug.trim().toLowerCase();
  const slugValid = slugClean === '' || SLUG_RE.test(slugClean);
  const slugCollides = slugClean !== '' && installedSlugs.has(slugClean);

  const canSubmit =
    !pending &&
    slugClean !== '' &&
    slugValid &&
    name.trim() !== '' &&
    description.trim() !== '' &&
    instructions.trim() !== '' &&
    surfaces.length > 0 &&
    dispatchConsistent &&
    (!slugCollides || overwrite);

  const reset = () => {
    setSlug('');
    setName('');
    setDescription('');
    setVersion('0.1.0');
    setInstructions(DEFAULT_TEMPLATE);
    setSurfaces(['chat']);
    setTagsInput('');
    setWorkspace('');
    setOverwrite(false);
    setOpenclawEnabled(false);
    setOcHomepage('');
    setOcUserInvocable(true);
    setOcDisableModel(false);
    setOcDispatch('');
    setOcCommandTool('');
    setOcCommandArgMode('');
    setOcAlways(false);
    setOcEmoji('');
    setOcOs([]);
    setOcPrimaryEnv('');
    setOcSkillKey('');
    setOcReqBins('');
    setOcReqAnyBins('');
    setOcReqEnv('');
    setOcReqConfig('');
  };

  const toggleOcOs = (value: OpenClawOs) => {
    setOcOs((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]
    );
  };

  const toggleSurface = (surface: Surface) => {
    setSurfaces((prev) =>
      prev.includes(surface) ? prev.filter((item) => item !== surface) : [...prev, surface]
    );
  };

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit({
      slug: slugClean,
      name: name.trim(),
      description: description.trim(),
      version: version.trim() || '0.1.0',
      instructions,
      surfaces,
      tags,
      workspace: workspace || null,
      overwrite,
      openclaw: openclawPayload,
    });
  };

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
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
            {!slugValid && (
              <div className="info-block" style={{ color: 'var(--danger, #d14)' }}>
                Slug must be 2-63 chars: lowercase letters, digits, <code>-</code> or
                <code> _</code>, starting with a letter or digit.
              </div>
            )}
            {slugCollides && (
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
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-description">Short description</label>
            <input
              id="skill-author-description"
              className="input"
              placeholder="One-sentence trigger describing when the agent should load this skill."
              value={description}
              onChange={(event) => setDescription(event.target.value)}
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
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              maxLength={32}
            />
          </div>

          <div className="form-row">
            <label>Surfaces</label>
            <div className="skills-chip-row">
              {SURFACES.map((surface) => {
                const active = surfaces.includes(surface);
                return (
                  <button
                    key={surface}
                    type="button"
                    className={`badge ${active ? 'ok' : 'muted'}`}
                    onClick={() => toggleSurface(surface)}
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
              value={tagsInput}
              onChange={(event) => setTagsInput(event.target.value)}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-workspace">Workspace</label>
            <select
              id="skill-author-workspace"
              className="input"
              value={workspace}
              onChange={(event) => setWorkspace(event.target.value)}
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
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              style={{ fontFamily: 'var(--font-mono, ui-monospace, monospace)', minHeight: 240 }}
              maxLength={32_000}
            />
            <div className="info-block">
              Markdown is injected into the agent prompt when the skill activates. Lead with "when
              to use", then steps, then an output contract.
            </div>
          </div>

          <div
            className="skills-callout"
            style={{ borderTop: '1px dashed var(--border, #334)', paddingTop: 12 }}
          >
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={openclawEnabled}
                onChange={(event) => setOpenclawEnabled(event.target.checked)}
              />
              <strong>OpenClaw compatibility</strong>
            </label>
            <div className="skills-callout-body">
              Emit <code>homepage</code>, <code>user-invocable</code>, dispatch controls, and a{' '}
              <code>metadata.openclaw</code> block so the bundle loads under OpenClaw Gateway
              alongside baselithbot.
            </div>
          </div>

          {openclawEnabled && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="form-row">
                <label htmlFor="skill-author-oc-homepage">Homepage</label>
                <input
                  id="skill-author-oc-homepage"
                  className="input"
                  placeholder="https://example.com/skill"
                  value={ocHomepage}
                  onChange={(event) => setOcHomepage(event.target.value)}
                  maxLength={512}
                />
              </div>

              <div className="form-row">
                <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={ocUserInvocable}
                    onChange={(event) => setOcUserInvocable(event.target.checked)}
                  />
                  User invocable (slash command exposed)
                </label>
                <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={ocDisableModel}
                    onChange={(event) => setOcDisableModel(event.target.checked)}
                  />
                  Disable model invocation (skip auto-selection by agent)
                </label>
                <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={ocAlways}
                    onChange={(event) => setOcAlways(event.target.checked)}
                  />
                  Always active (bypass gating)
                </label>
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-dispatch">Command dispatch</label>
                <select
                  id="skill-author-oc-dispatch"
                  className="input"
                  value={ocDispatch}
                  onChange={(event) => setOcDispatch(event.target.value as '' | 'tool')}
                >
                  <option value="">(none — default LLM dispatch)</option>
                  <option value="tool">tool (forward invocation to a tool)</option>
                </select>
              </div>

              {ocDispatch === 'tool' && (
                <>
                  <div className="form-row">
                    <label htmlFor="skill-author-oc-tool">Command tool</label>
                    <input
                      id="skill-author-oc-tool"
                      className="input"
                      placeholder="baselith.search"
                      value={ocCommandTool}
                      onChange={(event) => setOcCommandTool(event.target.value)}
                      maxLength={128}
                    />
                    {!dispatchConsistent && (
                      <div className="info-block" style={{ color: 'var(--danger, #d14)' }}>
                        command-dispatch=tool requires a command-tool.
                      </div>
                    )}
                  </div>
                  <div className="form-row">
                    <label htmlFor="skill-author-oc-arg-mode">Command arg mode</label>
                    <select
                      id="skill-author-oc-arg-mode"
                      className="input"
                      value={ocCommandArgMode}
                      onChange={(event) => setOcCommandArgMode(event.target.value as '' | 'raw')}
                    >
                      <option value="">(default)</option>
                      <option value="raw">raw (forward unparsed args)</option>
                    </select>
                  </div>
                </>
              )}

              <div className="form-row">
                <label htmlFor="skill-author-oc-emoji">Emoji</label>
                <input
                  id="skill-author-oc-emoji"
                  className="input"
                  placeholder="🧠"
                  value={ocEmoji}
                  onChange={(event) => setOcEmoji(event.target.value)}
                  maxLength={16}
                />
              </div>

              <div className="form-row">
                <label>OS restriction</label>
                <div className="skills-chip-row">
                  {OPENCLAW_OS.map((value) => {
                    const active = ocOs.includes(value);
                    return (
                      <button
                        key={value}
                        type="button"
                        className={`badge ${active ? 'ok' : 'muted'}`}
                        onClick={() => toggleOcOs(value)}
                        style={{ cursor: 'pointer' }}
                      >
                        {value}
                      </button>
                    );
                  })}
                </div>
                <div className="info-block">
                  Empty = every OS. Selecting any restricts loading to those platforms.
                </div>
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-primary-env">Primary env var</label>
                <input
                  id="skill-author-oc-primary-env"
                  className="input"
                  placeholder="OPENAI_API_KEY"
                  value={ocPrimaryEnv}
                  onChange={(event) => setOcPrimaryEnv(event.target.value)}
                  maxLength={128}
                />
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-skill-key">Skill key (config override)</label>
                <input
                  id="skill-author-oc-skill-key"
                  className="input"
                  placeholder="my_skill_key"
                  value={ocSkillKey}
                  onChange={(event) => setOcSkillKey(event.target.value)}
                  maxLength={128}
                />
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-bins">requires.bins (comma separated)</label>
                <input
                  id="skill-author-oc-bins"
                  className="input"
                  placeholder="ffmpeg, yt-dlp"
                  value={ocReqBins}
                  onChange={(event) => setOcReqBins(event.target.value)}
                />
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-anybins">requires.anyBins (comma separated)</label>
                <input
                  id="skill-author-oc-anybins"
                  className="input"
                  placeholder="python, python3"
                  value={ocReqAnyBins}
                  onChange={(event) => setOcReqAnyBins(event.target.value)}
                />
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-env">requires.env (comma separated)</label>
                <input
                  id="skill-author-oc-env"
                  className="input"
                  placeholder="OPENAI_API_KEY, SERPAPI_KEY"
                  value={ocReqEnv}
                  onChange={(event) => setOcReqEnv(event.target.value)}
                />
              </div>

              <div className="form-row">
                <label htmlFor="skill-author-oc-config">
                  requires.config paths (comma separated)
                </label>
                <input
                  id="skill-author-oc-config"
                  className="input"
                  placeholder="agent.apiKey, providers.openai.model"
                  value={ocReqConfig}
                  onChange={(event) => setOcReqConfig(event.target.value)}
                />
              </div>
            </div>
          )}

          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(event) => setOverwrite(event.target.checked)}
            />
            <span>Overwrite existing bundle with the same slug</span>
          </label>

          <div className="inline" style={{ gap: 8 }}>
            <button
              type="button"
              className="btn primary"
              disabled={!canSubmit}
              onClick={handleSubmit}
            >
              <Icon path={paths.plus} size={12} />
              {pending ? 'Creating…' : 'Create skill'}
            </button>
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => {
                reset();
                setExpanded(false);
              }}
              disabled={pending}
            >
              Cancel
            </button>
            <button type="button" className="btn ghost sm" onClick={reset} disabled={pending}>
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
