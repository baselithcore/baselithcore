import type { OpenClawFieldsState } from './hooks';
import { OPENCLAW_OS } from './types';

interface OpenClawFieldsProps {
  oc: OpenClawFieldsState;
}

export function OpenClawFields({ oc }: OpenClawFieldsProps) {
  return (
    <>
      <div
        className="skills-callout"
        style={{ borderTop: '1px dashed var(--border, #334)', paddingTop: 12 }}
      >
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={oc.enabled}
            onChange={(event) => oc.setEnabled(event.target.checked)}
          />
          <strong>OpenClaw compatibility</strong>
        </label>
        <div className="skills-callout-body">
          Emit <code>homepage</code>, <code>user-invocable</code>, dispatch controls, and a{' '}
          <code>metadata.openclaw</code> block so the bundle loads under OpenClaw Gateway
          alongside baselithbot.
        </div>
      </div>

      {oc.enabled && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="form-row">
            <label htmlFor="skill-author-oc-homepage">Homepage</label>
            <input
              id="skill-author-oc-homepage"
              className="input"
              placeholder="https://example.com/skill"
              value={oc.homepage}
              onChange={(event) => oc.setHomepage(event.target.value)}
              maxLength={512}
            />
          </div>

          <div className="form-row">
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={oc.userInvocable}
                onChange={(event) => oc.setUserInvocable(event.target.checked)}
              />
              User invocable (slash command exposed)
            </label>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={oc.disableModel}
                onChange={(event) => oc.setDisableModel(event.target.checked)}
              />
              Disable model invocation (skip auto-selection by agent)
            </label>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="checkbox"
                checked={oc.always}
                onChange={(event) => oc.setAlways(event.target.checked)}
              />
              Always active (bypass gating)
            </label>
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-dispatch">Command dispatch</label>
            <select
              id="skill-author-oc-dispatch"
              className="input"
              value={oc.dispatch}
              onChange={(event) => oc.setDispatch(event.target.value as '' | 'tool')}
            >
              <option value="">(none — default LLM dispatch)</option>
              <option value="tool">tool (forward invocation to a tool)</option>
            </select>
          </div>

          {oc.dispatch === 'tool' && (
            <>
              <div className="form-row">
                <label htmlFor="skill-author-oc-tool">Command tool</label>
                <input
                  id="skill-author-oc-tool"
                  className="input"
                  placeholder="baselith.search"
                  value={oc.commandTool}
                  onChange={(event) => oc.setCommandTool(event.target.value)}
                  maxLength={128}
                />
                {!oc.dispatchConsistent && (
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
                  value={oc.commandArgMode}
                  onChange={(event) => oc.setCommandArgMode(event.target.value as '' | 'raw')}
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
              value={oc.emoji}
              onChange={(event) => oc.setEmoji(event.target.value)}
              maxLength={16}
            />
          </div>

          <div className="form-row">
            <label>OS restriction</label>
            <div className="skills-chip-row">
              {OPENCLAW_OS.map((value) => {
                const active = oc.os.includes(value);
                return (
                  <button
                    key={value}
                    type="button"
                    className={`badge ${active ? 'ok' : 'muted'}`}
                    onClick={() => oc.toggleOs(value)}
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
              value={oc.primaryEnv}
              onChange={(event) => oc.setPrimaryEnv(event.target.value)}
              maxLength={128}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-skill-key">Skill key (config override)</label>
            <input
              id="skill-author-oc-skill-key"
              className="input"
              placeholder="my_skill_key"
              value={oc.skillKey}
              onChange={(event) => oc.setSkillKey(event.target.value)}
              maxLength={128}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-bins">requires.bins (comma separated)</label>
            <input
              id="skill-author-oc-bins"
              className="input"
              placeholder="ffmpeg, yt-dlp"
              value={oc.reqBins}
              onChange={(event) => oc.setReqBins(event.target.value)}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-anybins">requires.anyBins (comma separated)</label>
            <input
              id="skill-author-oc-anybins"
              className="input"
              placeholder="python, python3"
              value={oc.reqAnyBins}
              onChange={(event) => oc.setReqAnyBins(event.target.value)}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-env">requires.env (comma separated)</label>
            <input
              id="skill-author-oc-env"
              className="input"
              placeholder="OPENAI_API_KEY, SERPAPI_KEY"
              value={oc.reqEnv}
              onChange={(event) => oc.setReqEnv(event.target.value)}
            />
          </div>

          <div className="form-row">
            <label htmlFor="skill-author-oc-config">requires.config paths (comma separated)</label>
            <input
              id="skill-author-oc-config"
              className="input"
              placeholder="agent.apiKey, providers.openai.model"
              value={oc.reqConfig}
              onChange={(event) => oc.setReqConfig(event.target.value)}
            />
          </div>
        </div>
      )}
    </>
  );
}
