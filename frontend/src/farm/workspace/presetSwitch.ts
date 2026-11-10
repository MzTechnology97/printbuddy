// Fork (printfarm): OrcaSlicer's preset combos in the workspace: their preview (the server's settings.switch_preset,
// the same the saved copy gets), the switch waiting on the unsaved-changes dialog, and committing one onto the page's
// latest draft. The switches made live in the draft's history (draft.ts): a switch is one undo step.
import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { type PresetRef, type WorkspaceSetting, workspaceApi } from './api';
import type { Draft, Switched } from './draft';
import { draftAfterSwitch, unsavedKeys, withSwitched } from './presetTools';

export interface PendingSwitch extends Switched {
  ref: PresetRef;
  /** what the switch leaves behind: the dialog's list */
  keys: string[];
  /** the switches it was built on: if they changed meanwhile (Undo, Undo all), it is stale */
  base: Switched;
}
/** The page as it is now: read when a preview comes back, not when it was asked for. */
export interface LiveState {
  settings: WorkspaceSetting[];
  draft: Draft;
  switched: Switched;
  /** the switches and the draft together: one undo step */
  switchTo: (switched: Switched, draft: Draft) => void;
}

/** ``source``: the project's own settings; ``live``: the page's settings (switched), draft and switches now. */
export function usePresetSwitch(source: WorkspaceSetting[], live: () => LiveState, onError: (message: string) => void) {
  const [pending, setPending] = useState<PendingSwitch | null>(null);
  // a reset (Undo all) makes an answer asked before it stale, even when the switches look the same
  const generation = useRef(0);
  const preview = useMutation({
    mutationFn: (refs: PresetRef[]) => workspaceApi.previewSwitch(Object.fromEntries(source.map((s) => [s.key, s.value])), refs),
  });
  const commit = (next: PendingSwitch, transfer: boolean) => {
    setPending(null);
    const { settings, draft, switched, switchTo } = live();
    if (switched !== next.base) return; // undone meanwhile: the switch no longer fits
    const after = draftAfterSwitch(settings, withSwitched(source, next.values), draft, next.keys, next.ref.section, next.ref.index, transfer);
    switchTo({ refs: next.refs, values: next.values }, after);
  };
  const pick = (ref: PresetRef) => {
    if (preview.isPending) return; // one switch at a time: the next builds on it
    const asked = generation.current;
    const base = live().switched;
    const refs = [...base.refs.filter((r) => r.section !== ref.section || r.index !== ref.index), ref];
    preview.mutate(refs, {
      onSuccess: ({ values }) => {
        const { settings, draft, switched } = live(); // edits typed meanwhile are kept: they apply over the switch
        if (generation.current !== asked || switched !== base) return; // Undo / Undo all meanwhile: stale
        const next = { ref, refs, values, base, keys: unsavedKeys(settings, draft, ref.section, ref.index) };
        if (next.keys.length) setPending(next);
        else commit(next, false);
      },
      onError: (e) => onError(e instanceof Error ? e.message : String(e)),
    });
  };
  return {
    pending,
    /** a preview is on its way: no other pick until it's back */
    busy: preview.isPending,
    pick,
    commit,
    cancel: () => setPending(null),
    /** Undo all: drop the switch on its way or waiting on the dialog */
    reset: () => {
      generation.current += 1;
      setPending(null);
    },
  };
}
