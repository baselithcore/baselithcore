import { useMemo, useState } from 'react';
import type { OpenClawFrontmatterPayload } from '../../../../lib/api';
import { DEFAULT_TEMPLATE, SLUG_RE } from './types';
import type { OpenClawOs, SkillAuthorProps, Surface } from './types';

const splitList = (raw: string): string[] =>
  raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

export function useOpenClawFields() {
  const [enabled, setEnabled] = useState(false);
  const [homepage, setHomepage] = useState('');
  const [userInvocable, setUserInvocable] = useState(true);
  const [disableModel, setDisableModel] = useState(false);
  const [dispatch, setDispatch] = useState<'' | 'tool'>('');
  const [commandTool, setCommandTool] = useState('');
  const [commandArgMode, setCommandArgMode] = useState<'' | 'raw'>('');
  const [always, setAlways] = useState(false);
  const [emoji, setEmoji] = useState('');
  const [os, setOs] = useState<OpenClawOs[]>([]);
  const [primaryEnv, setPrimaryEnv] = useState('');
  const [skillKey, setSkillKey] = useState('');
  const [reqBins, setReqBins] = useState('');
  const [reqAnyBins, setReqAnyBins] = useState('');
  const [reqEnv, setReqEnv] = useState('');
  const [reqConfig, setReqConfig] = useState('');

  const payload = useMemo<OpenClawFrontmatterPayload | null>(() => {
    if (!enabled) return null;
    const bins = splitList(reqBins);
    const anyBins = splitList(reqAnyBins);
    const env = splitList(reqEnv);
    const config = splitList(reqConfig);
    return {
      homepage: homepage.trim() || null,
      user_invocable: userInvocable,
      disable_model_invocation: disableModel,
      command_dispatch: dispatch || null,
      command_tool: commandTool.trim() || null,
      command_arg_mode: commandArgMode || null,
      always,
      emoji: emoji.trim() || null,
      os: [...os],
      primary_env: primaryEnv.trim() || null,
      skill_key: skillKey.trim() || null,
      requires: { bins, any_bins: anyBins, env, config },
      install: [],
    };
  }, [
    enabled,
    homepage,
    userInvocable,
    disableModel,
    dispatch,
    commandTool,
    commandArgMode,
    always,
    emoji,
    os,
    primaryEnv,
    skillKey,
    reqBins,
    reqAnyBins,
    reqEnv,
    reqConfig,
  ]);

  const dispatchConsistent = !enabled || dispatch !== 'tool' || commandTool.trim().length > 0;

  const toggleOs = (value: OpenClawOs) => {
    setOs((prev) =>
      prev.includes(value) ? prev.filter((item) => item !== value) : [...prev, value]
    );
  };

  const reset = () => {
    setEnabled(false);
    setHomepage('');
    setUserInvocable(true);
    setDisableModel(false);
    setDispatch('');
    setCommandTool('');
    setCommandArgMode('');
    setAlways(false);
    setEmoji('');
    setOs([]);
    setPrimaryEnv('');
    setSkillKey('');
    setReqBins('');
    setReqAnyBins('');
    setReqEnv('');
    setReqConfig('');
  };

  return {
    enabled,
    setEnabled,
    homepage,
    setHomepage,
    userInvocable,
    setUserInvocable,
    disableModel,
    setDisableModel,
    dispatch,
    setDispatch,
    commandTool,
    setCommandTool,
    commandArgMode,
    setCommandArgMode,
    always,
    setAlways,
    emoji,
    setEmoji,
    os,
    primaryEnv,
    setPrimaryEnv,
    skillKey,
    setSkillKey,
    reqBins,
    setReqBins,
    reqAnyBins,
    setReqAnyBins,
    reqEnv,
    setReqEnv,
    reqConfig,
    setReqConfig,
    payload,
    dispatchConsistent,
    toggleOs,
    reset,
  };
}

export type OpenClawFieldsState = ReturnType<typeof useOpenClawFields>;

export function useSkillAuthorForm({
  installedSlugs,
  pending,
  onSubmit,
}: Pick<SkillAuthorProps, 'installedSlugs' | 'pending' | 'onSubmit'>) {
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

  const openclaw = useOpenClawFields();

  const tags = useMemo(
    () =>
      tagsInput
        .split(',')
        .map((tag) => tag.trim().toLowerCase())
        .filter((tag) => tag.length > 0 && tag.length <= 48),
    [tagsInput]
  );

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
    openclaw.dispatchConsistent &&
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
    openclaw.reset();
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
      openclaw: openclaw.payload,
    });
  };

  return {
    expanded,
    setExpanded,
    slug,
    setSlug,
    name,
    setName,
    description,
    setDescription,
    version,
    setVersion,
    instructions,
    setInstructions,
    surfaces,
    tagsInput,
    setTagsInput,
    workspace,
    setWorkspace,
    overwrite,
    setOverwrite,
    openclaw,
    slugValid,
    slugCollides,
    canSubmit,
    reset,
    toggleSurface,
    handleSubmit,
  };
}
