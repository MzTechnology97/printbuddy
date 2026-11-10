// Fork (printfarm): the workspace's top bar in OrcaSlicer's order (BBLTopbar + the tab bar's right side): File ▾,
// the ≡ menu, Save / Undo / Redo, the project's name ("*" before it while there are unsaved changes), Prepare /
// Preview, and the Slice and Print buttons with their ▾ (the choice there sets what the button does).
import { useState } from 'react';
import { Box, ChevronDown, Loader2, Menu as MenuIcon, Redo2, Save, Undo2, X } from 'lucide-react';
import { ContextMenu, type MenuItem } from './Viewport';

export interface TopBarProps {
  title: string;
  subtitle: string;
  dirty: boolean;
  file: MenuItem[];
  main: MenuItem[];
  /** Tooltips and labels in the UI language. */
  labels: { close: string; file: string; menu: string; save: string; undo: string; redo: string; tabs: string };
  onClose: () => void;
  onSave?: () => void;
  onUndo?: () => void;
  onRedo?: () => void;
  modes: { key: string; label: string }[];
  mode: string;
  onMode: (mode: string) => void;
  slice: SplitButton & { busy: boolean };
  print: SplitButton;
}

interface SplitButton {
  label: string;
  run?: () => void;
  title?: string;
  options: MenuItem[];
  /** The ▾ button's name, for screen readers. */
  more: string;
}

const iconButton =
  'p-1.5 rounded text-bambu-gray-light hover:text-white hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent disabled:cursor-default';

export function TopBar(p: TopBarProps) {
  const [open, setOpen] = useState<{ which: string; at: { x: number; y: number } } | null>(null);
  const show = (which: string, e: React.MouseEvent<HTMLElement>, items: MenuItem[]) => {
    if (open?.which === which) return setOpen(null);
    const r = e.currentTarget.getBoundingClientRect();
    if (items.length) setOpen({ which, at: { x: r.left, y: r.bottom + 2 } });
  };
  const menus: Record<string, MenuItem[]> = { file: p.file, main: p.main, slice: p.slice.options, print: p.print.options };

  return (
    <header className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
      <div className="flex items-center gap-1">
        <button type="button" className={iconButton} onClick={p.onClose} aria-label={p.labels.close} title={p.labels.close}>
          <X className="w-4 h-4" />
        </button>
        <button
          type="button"
          className="flex items-center gap-1 px-2 py-1 rounded text-sm text-white hover:bg-white/10"
          aria-haspopup="menu"
          aria-expanded={open?.which === 'file'}
          onClick={(e) => show('file', e, p.file)}
        >
          {p.labels.file}
          <ChevronDown className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          className={iconButton}
          aria-haspopup="menu"
          aria-expanded={open?.which === 'main'}
          aria-label={p.labels.menu}
          title={p.labels.menu}
          onClick={(e) => show('main', e, p.main)}
        >
          <MenuIcon className="w-4 h-4" />
        </button>
        <span className="mx-1 h-5 w-px bg-bambu-dark-tertiary" aria-hidden />
        <button type="button" className={iconButton} onClick={p.onSave} disabled={!p.onSave} aria-label={p.labels.save} title={`${p.labels.save} (Ctrl+S)`}>
          <Save className="w-4 h-4" />
        </button>
        <button type="button" className={iconButton} onClick={p.onUndo} disabled={!p.onUndo} aria-label={p.labels.undo} title={`${p.labels.undo} (Ctrl+Z)`}>
          <Undo2 className="w-4 h-4" />
        </button>
        <button type="button" className={iconButton} onClick={p.onRedo} disabled={!p.onRedo} aria-label={p.labels.redo} title={`${p.labels.redo} (Ctrl+Y)`}>
          <Redo2 className="w-4 h-4" />
        </button>
      </div>

      <div className="flex items-center justify-center gap-2 min-w-0 flex-1 order-last sm:order-none w-full sm:w-auto">
        <Box className="w-4 h-4 text-bambu-green flex-shrink-0" aria-hidden />
        <div className="min-w-0 text-center">
          <h1 className="text-sm font-semibold truncate" title={p.title}>
            {p.dirty ? `*${p.title}` : p.title}
          </h1>
          <p className="text-[11px] text-bambu-gray truncate">{p.subtitle}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 ml-auto">
        <div className="flex rounded-lg bg-bambu-dark p-0.5" role="tablist" aria-label={p.labels.tabs}>
          {p.modes.map((m) => (
            <button
              key={m.key}
              role="tab"
              aria-selected={p.mode === m.key}
              onClick={() => p.onMode(m.key)}
              className={`px-4 py-1 rounded-md text-sm ${p.mode === m.key ? 'bg-bambu-green text-white' : 'text-bambu-gray-light hover:text-white'}`}
            >
              {m.label}
            </button>
          ))}
        </div>
        {(['slice', 'print'] as const).map((which) => {
          const b = p[which];
          return (
            <div key={which} className="flex rounded-md overflow-hidden">
              <button
                type="button"
                onClick={b.run}
                disabled={!b.run}
                title={b.title}
                className={`flex items-center gap-1.5 px-3 py-1 text-sm disabled:opacity-50 ${
                  which === 'slice' ? 'bg-bambu-green text-white hover:bg-bambu-green-dark' : 'bg-bambu-dark-tertiary text-white hover:bg-white/15'
                }`}
              >
                {which === 'slice' && p.slice.busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {b.label}
              </button>
              <button
                type="button"
                aria-label={b.more}
                title={b.more}
                aria-haspopup="menu"
                aria-expanded={open?.which === which}
                onClick={(e) => show(which, e, b.options)}
                className={`px-1.5 border-l border-black/20 ${
                  which === 'slice' ? 'bg-bambu-green text-white hover:bg-bambu-green-dark' : 'bg-bambu-dark-tertiary text-white hover:bg-white/15'
                }`}
              >
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}
      </div>

      {open && <ContextMenu items={menus[open.which]} at={open.at} onClose={() => setOpen(null)} />}
    </header>
  );
}
