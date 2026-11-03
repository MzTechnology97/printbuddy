// Printfarm (fork): on the project page of a product, the farm view replaces upstream's
// Production Plan and BOM (which list every file as a part and have no quantities for products).
import { useState, type ReactNode } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, FileBox, ListPlus, Loader2, Package } from 'lucide-react';
import { api, type LibraryFolderTree } from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmPlanComponent, type FarmProductView } from './api';

type Part = FarmProductView['parts'][number];
const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
import { AccodaSummary, PrinterChip } from './PrintPlanPage';
import { ProductPiecesPanel } from './ProductPiecesPanel';
import { FilePlates } from './PlatesPanel';
import './i18n';

const duration = (s: number | null) => (s ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : null);
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

/** Picture of the component from its 3MF pieces; an old whole-product component shows the product's photo. */
function ComponentPicture({ part, view, hex }: { part: Part; view: FarmProductView; hex: string | null }) {
  const [failed, setFailed] = useState(false);
  const whole = !part.shape_id && !part.recipe?.length;
  return (
    <div className="w-24 h-24 shrink-0 rounded bg-gray-300 flex items-center justify-center overflow-hidden">
      {!failed ? (
        <img src={farmApi.componentImageUrl(part.code, hex)} alt={part.name} loading="lazy"
          className="w-full h-full object-contain" onError={() => setFailed(true)} />
      ) : whole && view.has_cover ? (
        <img src={api.getProjectCoverImageUrl(view.project_id)} alt={part.name} className="w-full h-full object-cover" />
      ) : (
        <span className="text-xs font-mono text-bambu-dark">{part.code}</span>
      )}
    </div>
  );
}

/** One component of the product: picture, name, colour, per-unit quantity (kits), plates. */
function PartRow({ part, view, colors }: { part: Part; view: FarmProductView; colors: Record<string, string> }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState(part.name);
  const [quantity, setQuantity] = useState(String(part.quantity));
  const done = {
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  };
  const update = useMutation({
    mutationFn: (data: { name?: string; color?: string | null }) => farmApi.updateComponent(part.code, data),
    ...done,
  });
  const setQty = useMutation({ mutationFn: (q: number) => farmApi.setPart(view.project_id, part.code, q), ...done });
  const isSet = part.recipe != null;
  const recipe = useMutation({
    mutationFn: (shapeId: number) => farmApi.setRecipeEntry(view.project_id, shapeId, 0),
    ...done,
  });
  const askRemove = () =>
    window.confirm(
      part.used_by.length
        ? t('farm.project.removeKeepConfirm', { code: part.code, products: part.used_by.map((u) => u.name).join(', ') })
        : t('farm.project.removeConfirm', { code: part.code }),
    ) && setQty.mutate(0);
  const hex = part.color ? colors[part.color.toLowerCase()] ?? null : null;
  return (
    <li className="bg-bambu-dark rounded-lg p-3 flex flex-col sm:flex-row gap-3">
      <ComponentPicture part={part} view={view} hex={hex} />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-bambu-green">{part.code}</span>
          <input aria-label={`${t('farm.project.componentName')} ${part.code}`} className={`${INPUT} flex-1 min-w-48`}
            value={name} onChange={(e) => setName(e.target.value)}
            onBlur={() => name.trim() && name.trim() !== part.name && update.mutate({ name: name.trim() })} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-bambu-gray">
          <label className="flex items-center gap-1">
            {t('farm.project.componentColor')}
            <select aria-label={`${t('farm.project.componentColor')} ${part.code}`} className={INPUT}
              value={part.color ? capitalize(part.color.toLowerCase()) : ''}
              onChange={(e) => update.mutate({ color: e.target.value || null })}>
              <option value="">{t('farm.project.noColor')}</option>
              {Object.keys(colors).map((c) => (
                <option key={c} value={capitalize(c)}>{capitalize(c)}</option>
              ))}
            </select>
          </label>
          {!isSet && (
            <label className="flex items-center gap-1">
              {t('farm.project.perUnit')}
              <input type="number" min={1} max={1000} aria-label={`${t('farm.project.perUnit')} ${part.code}`}
                className={`${INPUT} w-20`} value={quantity} onChange={(e) => setQuantity(e.target.value)}
                onBlur={() => Number(quantity) >= 1 && Number(quantity) !== part.quantity && setQty.mutate(Number(quantity))} />
            </label>
          )}
          <Button size="sm" variant="secondary" disabled={setQty.isPending} onClick={askRemove}
            aria-label={`${t('farm.project.removePart')} ${part.code}`}>
            {t('farm.project.removePart')}
          </Button>
        </div>
        {isSet && part.recipe!.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs text-bambu-gray">
              {t('farm.project.perSet', {
                pieces: part.recipe!.map((r) => `${r.quantity}× ${r.shape ?? r.shape_id}`).join(' + '),
              })}
            </p>
            <div className="flex flex-wrap gap-1">
              {part.recipe!.map((r) => (
                <button key={r.shape_id} type="button" disabled={recipe.isPending}
                  aria-label={`${t('farm.project.removeFromSet')} ${r.shape ?? r.shape_id}`}
                  className="text-xs bg-bambu-dark-tertiary text-bambu-gray hover:text-red-400 rounded px-2 py-0.5"
                  onClick={() =>
                    window.confirm(t('farm.project.removeFromSetConfirm', { shape: r.shape ?? r.shape_id })) &&
                    recipe.mutate(r.shape_id)
                  }>
                  × {r.shape ?? r.shape_id}
                </button>
              ))}
            </div>
          </div>
        )}
        {part.plates.length === 0 ? (
          <p className="text-xs text-amber-400 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5" /> {t('farm.components.noPlateYet')}
          </p>
        ) : (
          <ul className="space-y-1">
            {part.plates.map((p) => (
              <li key={p.plate_id} className="text-xs text-bambu-gray break-all">
                <span className="text-white">{fileName(p.file)}</span> ·{' '}
                {t('farm.plates.plate', { index: p.plate })} ·{' '}
                {[
                  p.printer_model,
                  p.material,
                  t('farm.project.yield', { count: p.yield }),
                  duration(p.print_time_seconds),
                  p.filament_grams ? `${Math.round(p.filament_grams)} g` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                {!p.sliced && <span className="text-amber-400"> · {t('farm.project.notSliced')}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

/** Add an existing component, or a new one by name, to a kit. */
function AddPart({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const components = useQuery({ queryKey: ['farm', 'components'], queryFn: farmApi.getComponents });
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const add = useMutation({
    mutationFn: async () => {
      const chosen = code || (await farmApi.createComponent({ name: name.trim() })).code;
      return farmApi.setPart(view.project_id, chosen, 1);
    },
    onSuccess: () => {
      setCode('');
      setName('');
      queryClient.invalidateQueries({ queryKey: ['farm'] });
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const mine = new Set(view.parts.map((p) => p.code));
  const choices = (components.data || []).filter((c) => !c.retired && !mine.has(c.code));
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-bambu-gray">{t('farm.project.addPart')}</span>
      <select aria-label={t('farm.project.addPart')} className={`${INPUT} max-w-full`} value={code}
        onChange={(e) => setCode(e.target.value)}>
        <option value="">{t('farm.project.pickComponent')}</option>
        {choices.map((c) => (
          <option key={c.code} value={c.code}>{c.code} {c.name}</option>
        ))}
      </select>
      {!code && (
        <input aria-label={t('farm.project.newComponentName')} placeholder={t('farm.project.newComponentName')}
          className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
      )}
      <Button size="sm" disabled={(!code && !name.trim()) || add.isPending} onClick={() => add.mutate()}>
        {t('farm.project.add')}
      </Button>
    </div>
  );
}

/** The plates of the product's 3MF files, those to confirm first, each file opened in place. */
function ProductPlates({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const shapes = useQuery({ queryKey: ['farm', 'shapes'], queryFn: farmApi.getShapes });
  const files = view.files
    .filter((f) => !f.excluded)
    .sort((a, b) => Number(b.proposed > 0 || !b.opened) - Number(a.proposed > 0 || !a.opened));
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-white">{t('farm.project.plates')}</h3>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.project.platesIntro')}</p>
      {files.length === 0 ? (
        <p className="text-sm text-bambu-gray">{t('farm.project.noFiles')}</p>
      ) : (
        <ul className="space-y-2">
          {files.map((f) => {
            const isOpen = open === f.library_file_id;
            const todo = f.proposed > 0 || !f.opened;
            return (
              <li key={f.library_file_id} className="bg-bambu-dark rounded-lg">
                <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : f.library_file_id)}
                  className="w-full text-left p-3 flex items-start gap-2 text-sm">
                  {isOpen ? <ChevronDown className="w-4 h-4 mt-0.5 shrink-0" /> : <ChevronRight className="w-4 h-4 mt-0.5 shrink-0" />}
                  <span className="min-w-0 flex-1 break-all">
                    <span className="text-white">{f.filename}</span>
                    <span className="text-xs text-bambu-gray block">{f.folder}</span>
                  </span>
                  <span className={`text-xs shrink-0 ${todo ? 'text-amber-400' : 'text-bambu-gray'}`}>
                    {f.opened ? t('farm.plates.counts', { todo: f.proposed, done: f.confirmed }) : t('farm.plates.notOpened')}
                  </span>
                </button>
                {isOpen && (
                  <FilePlates file={{ library_file_id: f.library_file_id, path: f.path }} shapes={shapes.data || []}
                    aside={f.unrelated_plates}
                    onShapeCreated={() => queryClient.invalidateQueries({ queryKey: ['farm', 'shapes'] })} />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

type Fit = FarmPlanComponent['plates'][number]['printers'][number];
const fitRank = (f: Fit) => (f.idle && f.material_ok && f.color_ok ? 2 : f.idle ? 1 : 0);

/** Per printer model: the product's plates for it, and its printers (best fit over those plates). */
function ProductPrinters({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const plan = useQuery({
    queryKey: ['farm', 'product-plan', view.project_id],
    queryFn: () => farmApi.getProductPlan(view.project_id),
  });
  const printers = useQuery({ queryKey: ['printers'], queryFn: api.getPrinters });
  const models = new Map<string, { plates: string[]; printers: Map<number, Fit> }>();
  for (const c of plan.data?.components || []) {
    for (const p of c.plates) {
      const m = models.get(p.printer_model) ?? { plates: [] as string[], printers: new Map<number, Fit>() };
      m.plates.push(
        `${c.code} · ${fileName(p.file)} · ${t('farm.plates.plate', { index: p.plate })} · ${p.material} · ${t('farm.project.perPrint', { count: p.yield })}`,
      );
      for (const f of p.printers) {
        const seen = m.printers.get(f.id);
        if (!seen || fitRank(f) > fitRank(seen)) m.printers.set(f.id, f);
      }
      models.set(p.printer_model, m);
    }
  }
  const covered = new Set([...models.keys()].map((m) => m.toLowerCase()));
  const missing = [
    ...new Set(
      (printers.data || [])
        .filter((p) => p.is_active && p.model && !covered.has(p.model.toLowerCase()))
        .map((p) => p.model as string),
    ),
  ].sort();
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-white">{t('farm.project.printers')}</h3>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.project.printersIntro')}</p>
      {plan.isLoading ? (
        <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
      ) : (
        <ul className="space-y-2">
          {[...models.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([model, m]) => (
            <li key={model} className="bg-bambu-dark rounded-lg p-3 space-y-1">
              <p className="text-white text-sm font-semibold">{model}</p>
              <ul className="text-xs text-bambu-gray break-all">
                {[...new Set(m.plates)].map((line) => <li key={line}>{line}</li>)}
              </ul>
              <div className="flex flex-wrap gap-1">
                {m.printers.size ? (
                  [...m.printers.values()].map((f) => <PrinterChip key={f.id} p={f} />)
                ) : (
                  <span className="text-xs text-bambu-gray">{t('farm.project.noActivePrinter')}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {missing.length > 0 && (
        <p className="text-xs text-amber-400">{t('farm.project.noProfile', { models: missing.join(', ') })}</p>
      )}
    </section>
  );
}

const flatFolders = (tree: LibraryFolderTree[], prefix = ''): { id: number; path: string }[] =>
  tree.flatMap((f) => [{ id: f.id, path: `${prefix}${f.name}` }, ...flatFolders(f.children || [], `${prefix}${f.name} / `)]);

/** The library folders linked to the product: link another, unlink one. */
function ProductFolders({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const tree = useQuery({ queryKey: ['library-folders'], queryFn: api.getLibraryFolders });
  const link = useMutation({
    mutationFn: ({ id, linked }: { id: number; linked: boolean }) => farmApi.setProductFolder(view.project_id, id, linked),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const mine = new Set(view.folders.map((f) => f.folder_id));
  const choices = flatFolders(tree.data || []).filter((f) => !mine.has(f.id));
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-white">{t('farm.project.folders')}</h3>
      {view.folders.length === 0 && <p className="text-sm text-amber-400">{t('farm.project.noFolders')}</p>}
      <ul className="space-y-1">
        {view.folders.map((f) => (
          <li key={f.folder_id} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-white min-w-0 flex-1 break-all">{f.name}</span>
            <Button size="sm" variant="secondary" disabled={link.isPending}
              onClick={() => window.confirm(t('farm.project.unlinkConfirm', { name: f.name })) &&
                link.mutate({ id: f.folder_id, linked: false })}>
              {t('farm.project.unlink')}
            </Button>
          </li>
        ))}
      </ul>
      <select aria-label={t('farm.project.linkFolder')} className={`${INPUT} max-w-full`} value=""
        disabled={link.isPending} onChange={(e) => e.target.value && link.mutate({ id: Number(e.target.value), linked: true })}>
        <option value="">{t('farm.project.linkFolder')}</option>
        {choices.map((f) => (
          <option key={f.id} value={f.id}>{f.path}</option>
        ))}
      </select>
    </section>
  );
}

/** Set or kit, and what is still missing before the product can be printed. */
function ProductStatus({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const pieces = useQuery({
    queryKey: ['farm', 'product-pieces', view.project_id],
    queryFn: () => farmApi.getProductPieces(view.project_id),
  });
  const kind = useMutation({
    mutationFn: (k: 'set' | 'kit') => farmApi.setProductKind(view.project_id, k),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const choose = (k: 'set' | 'kit') => {
    if (k === view.kind) return;
    if (k === 'set' && view.parts.length > 1 && !window.confirm(t('farm.project.kindConfirm'))) return;
    const theSet = view.parts.find((p) => p.recipe?.length);
    if (k === 'kit' && theSet && !window.confirm(t('farm.project.kitConfirm', { code: theSet.code }))) return;
    kind.mutate(k);
  };
  const open = pieces.data?.pieces.filter((p) => p.quantity == null).length ?? 0;
  const todo = [
    !view.kind && t('farm.project.todoKind'),
    view.kind === 'set' &&
      view.parts.some((p) => (p.recipe?.length ?? 0) > 1 && p.plates.length === 0) &&
      t('farm.project.setNoPlate'),
    open > 0 && t('farm.project.todoPieces', { count: open }),
    ...view.parts.filter((p) => p.plates.length === 0).map((p) => t('farm.project.todoPlates', { code: p.code })),
  ].filter(Boolean) as string[];
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-white font-semibold">{t('farm.project.kind')}</span>
        {(['set', 'kit'] as const).map((k) => (
          <Button key={k} size="sm" variant={view.kind === k ? 'primary' : 'secondary'} disabled={kind.isPending}
            aria-pressed={view.kind === k} onClick={() => choose(k)}>
            {t(k === 'set' ? 'farm.project.kindSet' : 'farm.project.kindKit')}
          </Button>
        ))}
      </div>
      <p className="text-xs text-bambu-gray max-w-3xl">
        {t(view.kind === 'set' ? 'farm.project.kindSetHint' : view.kind === 'kit' ? 'farm.project.kindKitHint' : 'farm.project.kindNone')}
      </p>
      {todo.length === 0 && view.parts.length > 0 ? (
        <p className="text-sm text-bambu-green flex items-center gap-1">
          <CheckCircle2 className="w-4 h-4" /> {t('farm.project.ready')}
        </p>
      ) : (
        <ul className="text-sm text-amber-400 space-y-0.5">
          {todo.map((x) => (
            <li key={x} className="flex items-center gap-1"><AlertTriangle className="w-4 h-4 shrink-0" /> {x}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FarmProductPanel({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [printing, setPrinting] = useState<FarmPlanComponent[] | null>(null);
  const settings = useQuery({ queryKey: ['farm', 'plan-settings'], queryFn: farmApi.getPlanSettings });
  const navigate = useNavigate();
  const active = useMutation({
    mutationFn: (on: boolean) => farmApi.setProductActive(view.project_id, on),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const remove = useMutation({
    mutationFn: () => farmApi.unmarkProduct(view.project_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['farm'] });
      navigate('/farm/products');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const plan = useMutation({
    mutationFn: () => farmApi.getProductPlan(view.project_id),
    onSuccess: (p) => setPrinting(p.components),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const exclude = useMutation({
    mutationFn: ({ id, excluded }: { id: number; excluded: boolean }) =>
      farmApi.setFileExcluded(view.project_id, id, excluded),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <Card>
      <CardContent className="p-4 space-y-4">
        <div className="flex flex-wrap items-start gap-4">
          <div className="w-24 h-24 shrink-0 rounded bg-bambu-dark overflow-hidden flex items-center justify-center">
            {view.has_cover ? (
              <img alt="" className="w-full h-full object-cover" src={api.getProjectCoverImageUrl(view.project_id)} />
            ) : (
              <Package className="w-10 h-10 text-bambu-gray/50" />
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <h1 className="text-xl font-semibold text-white break-words">{view.name}</h1>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <Link to="/farm/products" className="text-bambu-green hover:underline">{t('farm.project.allProducts')}</Link>
              <Link to={`/projects/${view.project_id}?view=project`} className="text-bambu-green hover:underline">
                {t('farm.project.projectPage')}
              </Link>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" disabled={active.isPending} onClick={() => active.mutate(view.disabled)}>
              {t(view.disabled ? 'farm.project.enable' : 'farm.project.disable')}
            </Button>
            <Button size="sm" variant="danger" disabled={remove.isPending}
              onClick={() => window.confirm(t('farm.project.removeProductConfirm', { name: view.name })) && remove.mutate()}>
              {t('farm.project.removeProduct')}
            </Button>
          </div>
        </div>
        {view.disabled && <p className="text-sm text-amber-400">{t('farm.project.disabledNote')}</p>}
        {(view.skus.length > 0 || view.asins.length > 0) && (
          <p className="text-xs text-bambu-gray break-words">
            {view.skus.join(' · ')}
            {view.asins.length > 0 && ` — ASIN ${view.asins.join(' · ')}`}
          </p>
        )}

        <ProductStatus view={view} />

        <section className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-white">{t('farm.project.components')}</h3>
            <Button size="sm" disabled={view.parts.length === 0 || plan.isPending} title={t('farm.project.printNowHint')}
              onClick={() => plan.mutate()}>
              {plan.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ListPlus className="w-4 h-4" />}
              {t('farm.project.printNow')}
            </Button>
          </div>
          {printing && (
            <AccodaSummary components={printing} byHand onClose={() => setPrinting(null)} onDone={() => undefined} />
          )}
          {view.parts.length === 0 && <p className="text-sm text-bambu-gray">{t('farm.products.noParts')}</p>}
          <ul className="space-y-3">
            {view.parts.map((part) => (
              <PartRow key={`${part.code}-${part.name}-${part.quantity}`} part={part} view={view}
                colors={settings.data?.colors || {}} />
            ))}
          </ul>
          {view.kind !== 'set' && <AddPart view={view} />}
        </section>

        <ProductPiecesPanel projectId={view.project_id} />

        <ProductPlates view={view} />

        <ProductPrinters view={view} />

        <ProductFolders view={view} />

        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-white">{t('farm.project.files')}</h3>
          {view.files.length === 0 ? (
            <p className="text-sm text-bambu-gray">{t('farm.project.noFiles')}</p>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary">
              {view.files.map((f) => (
                <li key={f.library_file_id}
                  className={`py-2 flex flex-wrap items-center gap-2 text-sm ${f.excluded ? 'opacity-50' : ''}`}
                  title={f.excluded ? t('farm.project.excludeHint') : undefined}>
                  <FileBox className="w-4 h-4 text-bambu-gray shrink-0" />
                  <span className="min-w-0 flex-1 break-all">
                    <span className={f.excluded ? 'text-bambu-gray line-through' : 'text-white'}>{f.filename}</span>
                    <span className="text-xs text-bambu-gray block">{f.folder}</span>
                  </span>
                  {f.excluded ? (
                    <span className="text-xs text-bambu-gray">{t('farm.project.excluded')}</span>
                  ) : (
                    <span className="text-xs text-bambu-gray">
                      {f.opened
                        ? t('farm.plates.counts', { todo: f.proposed, done: f.confirmed })
                        : t('farm.plates.notOpened')}
                    </span>
                  )}
                  <Button size="sm" variant="secondary" disabled={exclude.isPending}
                    aria-label={`${t(f.excluded ? 'farm.project.include' : 'farm.project.exclude')} ${f.filename}`}
                    onClick={() => exclude.mutate({ id: f.library_file_id, excluded: !f.excluded })}>
                    {t(f.excluded ? 'farm.project.include' : 'farm.project.exclude')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function useProductView(projectId: number) {
  return useQuery({
    queryKey: ['farm', 'product-view', projectId],
    queryFn: () => farmApi.getProductView(projectId).catch(() => null), // 404: not a product
    enabled: projectId > 0,
  });
}

/** Everything about one product on one page: ``/farm/products/:id``. */
export function ProductPage() {
  const { t } = useTranslation();
  const projectId = Number(useParams<{ id: string }>().id) || 0;
  const view = useProductView(projectId);
  return (
    <div className="p-4 md:p-8">
      {view.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : view.data ? (
        <FarmProductPanel view={view.data} />
      ) : (
        <p className="text-bambu-gray">
          {t('farm.project.notProduct')}{' '}
          <Link to={`/projects/${projectId}`} className="text-bambu-green hover:underline">{`/projects/${projectId}`}</Link>
        </p>
      )}
    </div>
  );
}

/** Renders ``children`` (upstream panels) for normal projects. A product's project page opens its
 * product sheet; with ``?view=project`` (photo, notes, print history) it stays, with a link back. */
export function FarmProductGate({ projectId, panel = false, children }: {
  projectId: number;
  panel?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const view = useProductView(projectId);
  if (view.isLoading) return null;
  if (!view.data) return <>{children}</>;
  if (!panel) return null;
  if (params.get('view') !== 'project') return <Navigate to={`/farm/products/${projectId}`} replace />;
  return (
    <Card>
      <CardContent className="p-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-bambu-gray">{t('farm.project.openSheet')}</p>
        <Link to={`/farm/products/${projectId}`} className="text-sm text-bambu-green hover:underline">
          {t('farm.project.openSheetLink')}
        </Link>
      </CardContent>
    </Card>
  );
}
