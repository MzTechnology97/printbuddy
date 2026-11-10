// Fork (printfarm): OrcaSlicer's preset combos in the workspace: the switches made, their preview (the server's
// settings.switch_preset, the same the saved copy gets), the switch waiting on the unsaved-changes dialog, and
// committing one onto the page's latest draft.
import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { type PresetRef, type SettingValue, type WorkspaceSetting, workspaceApi } from './api';
import type { Draft } from './draft';
import { draftAfterSwitch, unsavedKeys, withSwitched } from './presetTools';

export interface Switched {
  refs: PresetRef[];
  values: Record<string, SettingValue>;
}
export interface PendingSwitch extends Switched {
  ref: PresetRef;
  /** what the switch leaves behind: the dialog's list */
  keys: string[];
}
/** The page as it is now: read when a preview comes back, not when it was asked for. */
export interface LiveState {
  settings: WorkspaceSetting[];
  draft: Draft;
  replace: (draft: Draft) => void;
}
const NONE: Switched = { refs: [], values: {} };

/** ``source``: the project's own settings; ``live``: the page's settings (switched) and draft now. */
export function usePresetSwitch(source: WorkspaceSetting[], live: () => LiveState, onError: (message: string) => void) {
  const [switched, setSwitched] = useState<Switched>(NONE);
  const [pending, setPending] = useState<PendingSwitch | null>(null);
  // which switches a preview builds on: a commit or a reset makes an answer asked before it stale
  const generation = useRef(0);
  const preview = useMutation({
    mutationFn: (refs: PresetRef[]) => workspaceApi.previewSwitch(Object.fromEntries(source.map((s) => [s.key, s.value])), refs),
  });
  const commit = (next: PendingSwitch, transfer: boolean) => {
    const { settings, draft, replace } = live();
    replace(draftAfterSwitch(settings, withSwitched(source, next.values), draft, next.keys, next.ref.section, next.ref.index, transfer));
    generation.current += 1;
    setSwitched({ refs: next.refs, values: next.values });
    setPending(null);
  };
  const pick = (ref: PresetRef) => {
    if (preview.isPending) return; // one switch at a time: the next builds on it
    const asked = generation.current;
    const refs = [...switched.refs.filter((r) => r.section !== ref.section || r.index !== ref.index), ref];
    preview.mutate(refs, {
      onSuccess: ({ values }) => {
        if (generation.current !== asked) return; // the switches changed meanwhile (Undo all): this answer is stale
        const { settings, draft } = live(); // edits typed meanwhile are kept: they apply over the switch
        const next = { ref, refs, values, keys: unsavedKeys(settings, draft, ref.section, ref.index) };
        if (next.keys.length) setPending(next);
        else commit(next, false);
      },
      onError: (e) => onError(e instanceof Error ? e.message : String(e)),
    });
  };
  return {
    switched,
    pending,
    /** a preview is on its way: no other pick until it's back */
    busy: preview.isPending,
    pick,
    commit,
    cancel: () => setPending(null),
    reset: () => {
      generation.current += 1;
      setSwitched(NONE);
      setPending(null);
    },
  };
}
