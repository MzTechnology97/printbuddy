// Printfarm (fork): a kit's groups of parts assembled together before the product (e.g. the two
// halves of a handle). Only the product's own parts; the parts stay the full bill of materials.
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Plus, Trash2 } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmAssembly, type FarmAssemblyInput, type FarmProductView } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';

const asInput = (a: FarmAssembly): FarmAssemblyInput => ({
  key: a.key,
  name: a.name,
  per_unit: a.per_unit,
  parts: a.parts.map((p) => ({ component_code: p.component_code, quantity: p.quantity })),
});

export function AssembliesPanel({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const groups = view.assemblies || [];
  const names = new Map(view.parts.map((p) => [p.code, p.name]));
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [perUnit, setPerUnit] = useState('1');
  const [chosen, setChosen] = useState<Record<string, string>>({}); // code -> quantity per group

  const save = useMutation({
    mutationFn: (next: FarmAssemblyInput[]) => farmApi.setAssemblies(view.project_id, next),
    onSuccess: () => {
      setAdding(false);
      setName('');
      setPerUnit('1');
      setChosen({});
      queryClient.invalidateQueries({ queryKey: ['farm'] });
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const parts = Object.entries(chosen).map(([code, q]) => ({ component_code: code, quantity: Number(q) || 1 }));
  const add = () =>
    save.mutate([...groups.map(asInput), { name: name.trim(), per_unit: Number(perUnit) || 1, parts }]);
  const remove = (key: string) =>
    window.confirm(t('farm.assemblies.removeConfirm', { name: groups.find((g) => g.key === key)?.name })) &&
    save.mutate(groups.filter((g) => g.key !== key).map(asInput));

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-white">{t('farm.assemblies.title')}</h4>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.assemblies.intro')}</p>
      {groups.length > 0 && (
        <ul className="space-y-2">
          {groups.map((g) => (
            <li key={g.key} className="bg-bambu-dark rounded-lg p-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-white font-medium">{g.name}</span>
                <span className="text-bambu-gray">{t('farm.assemblies.perUnit', { count: g.per_unit })}</span>
                <Button size="sm" variant="ghost" className="ml-auto" disabled={save.isPending}
                  aria-label={`${t('farm.assemblies.remove')} ${g.name}`} onClick={() => remove(g.key)}>
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
              <p className="text-bambu-gray">
                {g.parts.map((p) => `${p.component_code} ${names.get(p.component_code) ?? ''} ×${p.quantity}`).join(' + ')}
              </p>
              {(g.warnings || []).map((w) => (
                <p key={w.component_code} className="text-xs text-amber-400 flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                  {t('farm.assemblies.warning', w)}
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}
      {!adding ? (
        view.parts.length > 0 && (
          <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>
            <Plus className="w-4 h-4" /> {t('farm.assemblies.add')}
          </Button>
        )
      ) : (
        <div className="bg-bambu-dark rounded-lg p-3 space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <input aria-label={t('farm.assemblies.name')} placeholder={t('farm.assemblies.name')} className={INPUT}
              value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
            <label className="flex items-center gap-2 text-bambu-gray">
              {t('farm.assemblies.groupsPerUnit')}
              <input aria-label={t('farm.assemblies.groupsPerUnit')} type="number" min={1} className={`${INPUT} w-20`}
                value={perUnit} onChange={(e) => setPerUnit(e.target.value)} />
            </label>
          </div>
          <ul className="space-y-1">
            {view.parts.map((p) => (
              <li key={p.code} className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-2 flex-1 min-w-0">
                  <input type="checkbox" checked={p.code in chosen}
                    onChange={(e) => {
                      const next = { ...chosen };
                      if (e.target.checked) next[p.code] = '1';
                      else delete next[p.code];
                      setChosen(next);
                    }} />
                  <span className="font-mono text-bambu-green">{p.code}</span>
                  <span className="text-bambu-gray break-all">{p.name} · ×{p.quantity}</span>
                </label>
                {p.code in chosen && (
                  <input aria-label={`${t('farm.assemblies.perGroup')} ${p.code}`} type="number" min={1}
                    className={`${INPUT} w-20`} value={chosen[p.code]}
                    onChange={(e) => setChosen({ ...chosen, [p.code]: e.target.value })} />
                )}
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Button size="sm" disabled={!name.trim() || parts.length === 0 || save.isPending} onClick={add}>
              {t('farm.assemblies.save')}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setAdding(false)}>{t('farm.assemblies.cancel')}</Button>
          </div>
        </div>
      )}
    </div>
  );
}
