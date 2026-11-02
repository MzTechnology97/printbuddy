// Fork (printfarm): NAS share for the library, configured from Settings.
// Strings live here (it/en) instead of the upstream locale files, to keep merges small.
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronRight, FolderOpen, HardDrive, Loader2, RefreshCw, Save, Wifi } from 'lucide-react';
import { Card, CardContent, CardHeader } from '../components/Card';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmNasApi, type NasSettingsUpdate } from './nasApi';

const STRINGS = {
  it: {
    title: 'NAS (libreria della farm)',
    intro:
      'Printbuddy legge i file di stampa di una sottocartella della share direttamente via SMB, senza copiarli: la scansione legge solo elenco e intestazioni dei 3MF. Un file viene scaricato nella cache solo quando lo usi (slicing, stampa, download). La radice della share non viene mai letta.',
    host: 'Host (nome o IP)',
    share: 'Share',
    path: 'Sottocartella',
    username: 'Utente',
    password: 'Password',
    passwordSet: 'impostata: lascia vuoto per non cambiarla',
    interval: 'Intervallo di scansione (minuti, 0 = solo manuale)',
    browse: 'Sfoglia',
    up: 'Su',
    choose: 'Usa questa cartella',
    test: 'Prova connessione',
    save: 'Salva',
    syncNow: 'Scansiona ora',
    testOk: (d: number, f: number) => `Connessione riuscita: ${d} cartelle, ${f} file di stampa`,
    saved: 'Impostazioni NAS salvate',
    synced: 'Scansione completata',
    lastResult: 'Ultima scansione',
    noSubfolders: 'Nessuna sottocartella',
    cacheMb: 'Limite della cache (MB): i file usati meno di recente vengono tolti',
    cacheUsed: (used: string, limit: number) => `Cache: ${used} MB usati su ${limit} MB`,
  },
  en: {
    title: 'NAS (farm library)',
    intro:
      'Printbuddy reads the print files of one share subfolder straight over SMB, without copying them: the scan reads only the listing and 3MF headers. A file is downloaded into the cache only when you use it (slicing, printing, download). The share root is never read.',
    host: 'Host (name or IP)',
    share: 'Share',
    path: 'Subfolder',
    username: 'User',
    password: 'Password',
    passwordSet: 'set: leave empty to keep it',
    interval: 'Scan interval (minutes, 0 = manual only)',
    browse: 'Browse',
    up: 'Up',
    choose: 'Use this folder',
    test: 'Test connection',
    save: 'Save',
    syncNow: 'Scan now',
    testOk: (d: number, f: number) => `Connected: ${d} folders, ${f} print files`,
    saved: 'NAS settings saved',
    synced: 'Scan finished',
    lastResult: 'Last scan',
    noSubfolders: 'No subfolders',
    cacheMb: 'Cache limit (MB): least recently used files are removed',
    cacheUsed: (used: string, limit: number) => `Cache: ${used} MB used of ${limit} MB`,
  },
};

const inputClass =
  'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none';

export function NasSettingsCard() {
  const { i18n } = useTranslation();
  const s = i18n.language?.startsWith('it') ? STRINGS.it : STRINGS.en;
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['farm-nas'], queryFn: farmNasApi.get });

  const [form, setForm] = useState({ host: '', share: '', path: '', username: '', minutes: 5, cacheMb: 4096 });
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [browser, setBrowser] = useState<{ path: string; directories: string[] } | null>(null);

  useEffect(() => {
    if (data) {
      setForm({
        host: data.host,
        share: data.share,
        path: data.path,
        username: data.username,
        minutes: Math.round(data.scan_interval_seconds / 60),
        cacheMb: data.cache_mb,
      });
    }
  }, [data]);

  const payload = (): NasSettingsUpdate => ({
    host: form.host.trim(),
    share: form.share.trim(),
    path: form.path.trim(),
    username: form.username,
    scan_interval_seconds: Math.max(0, form.minutes) * 60,
    cache_mb: Math.max(256, form.cacheMb),
    ...(password ? { password } : {}),
  });

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    try {
      await action();
    } catch (e) {
      showToast((e as Error).message, 'error');
    } finally {
      setBusy(null);
    }
  };

  const browseTo = (path: string) =>
    run('browse', async () => {
      const { host, share, username, password: pw } = payload();
      setBrowser(await farmNasApi.browse({ host, share, username, password: pw, path }));
    });

  const parent = (path: string) => path.split('/').slice(0, -1).join('/');

  return (
    <Card id="card-farm-nas">
      <CardHeader>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <HardDrive className="w-5 h-5 text-bambu-green" />
          {s.title}
        </h2>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-bambu-gray">{s.intro}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {(['host', 'share', 'username'] as const).map((field) => (
            <label key={field} className="block">
              <span className="block text-sm text-bambu-gray mb-1">{s[field]}</span>
              <input
                className={inputClass}
                value={form[field]}
                autoComplete="off"
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
              />
            </label>
          ))}
          <label className="block">
            <span className="block text-sm text-bambu-gray mb-1">{s.password}</span>
            <input
              type="password"
              className={inputClass}
              value={password}
              autoComplete="new-password"
              placeholder={data?.password_set ? s.passwordSet : ''}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        </div>

        <label className="block">
          <span className="block text-sm text-bambu-gray mb-1">{s.path}</span>
          <div className="flex gap-2">
            <input
              className={inputClass}
              value={form.path}
              onChange={(e) => setForm({ ...form, path: e.target.value })}
            />
            <Button variant="secondary" onClick={() => browseTo(form.path)} disabled={busy !== null}>
              {busy === 'browse' ? <Loader2 className="w-4 h-4 animate-spin" /> : <FolderOpen className="w-4 h-4" />}
              {s.browse}
            </Button>
          </div>
        </label>

        {browser && (
          <div className="border border-bambu-dark-tertiary rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="text-white truncate">/{browser.path}</span>
              <div className="flex gap-2">
                {browser.path && (
                  <Button size="sm" variant="ghost" onClick={() => browseTo(parent(browser.path))}>
                    {s.up}
                  </Button>
                )}
                {browser.path && (
                  <Button
                    size="sm"
                    onClick={() => {
                      setForm({ ...form, path: browser.path });
                      setBrowser(null);
                    }}
                  >
                    {s.choose}
                  </Button>
                )}
              </div>
            </div>
            {browser.directories.length === 0 && <p className="text-sm text-bambu-gray">{s.noSubfolders}</p>}
            <ul className="max-h-48 overflow-y-auto">
              {browser.directories.map((dir) => (
                <li key={dir}>
                  <button
                    type="button"
                    className="w-full flex items-center gap-1 px-2 py-1 text-left text-sm text-white rounded hover:bg-bambu-dark-tertiary"
                    onClick={() => browseTo(browser.path ? `${browser.path}/${dir}` : dir)}
                  >
                    <ChevronRight className="w-3 h-3 text-bambu-gray" />
                    {dir}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <label className="block">
          <span className="block text-sm text-bambu-gray mb-1">{s.interval}</span>
          <input
            type="number"
            min={0}
            max={1440}
            className={`${inputClass} w-32`}
            value={form.minutes}
            onChange={(e) => setForm({ ...form, minutes: parseInt(e.target.value, 10) || 0 })}
          />
        </label>

        <label className="block">
          <span className="block text-sm text-bambu-gray mb-1">{s.cacheMb}</span>
          <input
            type="number"
            min={256}
            step={256}
            className={`${inputClass} w-32`}
            value={form.cacheMb}
            onChange={(e) => setForm({ ...form, cacheMb: parseInt(e.target.value, 10) || 0 })}
          />
          {data && (
            <span className="block text-xs text-bambu-gray mt-1">
              {s.cacheUsed((data.cache_used_bytes / 1024 / 1024).toFixed(0), data.cache_mb)}
            </span>
          )}
        </label>

        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={busy !== null}
            onClick={() =>
              run('test', async () => {
                const r = await farmNasApi.test(payload());
                showToast(s.testOk(r.directories, r.print_files));
              })
            }
          >
            {busy === 'test' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
            {s.test}
          </Button>
          <Button
            disabled={busy !== null}
            onClick={() =>
              run('save', async () => {
                await farmNasApi.save(payload());
                setPassword('');
                await queryClient.invalidateQueries({ queryKey: ['farm-nas'] });
                showToast(s.saved);
              })
            }
          >
            {busy === 'save' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {s.save}
          </Button>
          {data?.folder_id && (
            <Button
              variant="secondary"
              disabled={busy !== null}
              onClick={() =>
                run('sync', async () => {
                  await farmNasApi.sync();
                  await queryClient.invalidateQueries({ queryKey: ['farm-nas'] });
                  showToast(s.synced);
                })
              }
            >
              {busy === 'sync' ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {s.syncNow}
            </Button>
          )}
        </div>

        {data?.last_result && (
          <p className="text-xs text-bambu-gray">
            {s.lastResult}
            {data.last_scan_at ? ` (${new Date(`${data.last_scan_at}Z`).toLocaleString()})` : ''}:{' '}
            {Object.entries(data.last_result)
              .map(([k, v]) => `${k}: ${v}`)
              .join(' · ')}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
