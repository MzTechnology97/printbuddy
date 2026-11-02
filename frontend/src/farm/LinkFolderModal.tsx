// Printfarm (fork): link a library folder to several projects/products (subfolders included), or to an archive.
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Archive as ArchiveIcon, Briefcase, Check, FileBox, Link2, Loader2, Package, Search, X } from 'lucide-react';
import { api, type LibraryFolderTree, type LibraryFolderUpdate } from '../api/client';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi } from './api';
import './i18n';

const INPUT =
  'w-full bg-bambu-dark border border-bambu-dark-tertiary rounded pl-8 pr-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';

const matches = (text: string, query: string) =>
  query.toLowerCase().split(/\s+/).every((word) => text.toLowerCase().includes(word));

interface Props {
  folder: LibraryFolderTree;
  onClose: () => void;
  /** Upstream folder update, used for the archive link. */
  onLink: (update: LibraryFolderUpdate) => void;
  isLoading: boolean;
}

export function FarmLinkFolderModal({ folder, onClose, onLink, isLoading }: Props) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<'projects' | 'archive'>('projects');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number> | null>(null);
  const [archiveId, setArchiveId] = useState<number | null>(folder.archive_id);

  const { data: projects } = useQuery({ queryKey: ['farm', 'project-names'], queryFn: farmApi.getProjectNames });
  const { data: products } = useQuery({ queryKey: ['farm', 'products'], queryFn: farmApi.getProducts });
  const { data: linked } = useQuery({
    queryKey: ['farm', 'folder-projects', folder.id],
    queryFn: () => farmApi.getFolderProjects(folder.id),
  });
  const { data: archives } = useQuery({
    queryKey: ['archives-for-link'],
    queryFn: () => api.getArchives(undefined, undefined, 500),
    enabled: tab === 'archive',
  });

  useEffect(() => {
    if (linked && selected === null) setSelected(new Set(linked.project_ids));
  }, [linked, selected]);

  const productIds = useMemo(() => new Set((products?.products || []).map((p) => p.project_id)), [products]);
  const chosen = selected ?? new Set<number>();
  const shownProjects = (projects || []).filter((p) => matches(p.name, query.trim()));
  const shownArchives = (archives || []).filter((a) => matches(a.print_name || a.filename, query.trim()));

  const saveProjects = useMutation({
    mutationFn: () => farmApi.setFolderProjects(folder.id, [...chosen]),
    onSuccess: () => {
      for (const key of ['library-folders', 'project-folders', 'project-files', 'farm']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      showToast(t(chosen.size ? 'farm.folders.linked' : 'farm.folders.unlinked', { count: chosen.size }), 'success');
      onClose();
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const toggle = (id: number) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const tabClass = (active: boolean) =>
    `flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
      active ? 'border-bambu-green bg-bambu-green/10 text-bambu-green' : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white'
    }`;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div
        role="dialog"
        aria-label={t('fileManager.linkFolder')}
        className="bg-bambu-dark-secondary rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col border border-bambu-dark-tertiary"
      >
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Link2 className="w-5 h-5 text-bambu-green" />
            {t('fileManager.linkFolder')}
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded" aria-label={t('common.cancel')}>
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-3 flex-1 min-h-0 flex flex-col">
          <p className="text-sm text-bambu-gray">
            {t('farm.folders.description', { name: folder.name })}
          </p>
          <div className="flex gap-2">
            <button onClick={() => setTab('projects')} className={tabClass(tab === 'projects')}>
              <Briefcase className="w-4 h-4" />
              {t('farm.folders.projects')}
            </button>
            <button onClick={() => setTab('archive')} className={tabClass(tab === 'archive')}>
              <ArchiveIcon className="w-4 h-4" />
              {t('fileManager.archive')}
            </button>
          </div>
          <div className="relative">
            <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="search"
              autoFocus
              aria-label={t('farm.folders.search')}
              placeholder={t('farm.folders.search')}
              className={INPUT}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className="flex-1 min-h-[12rem] max-h-[55vh] overflow-y-auto space-y-1 bg-bambu-dark rounded-lg p-2">
            {tab === 'projects'
              ? shownProjects.map((project) => {
                  const on = chosen.has(project.id);
                  return (
                    <button
                      key={project.id}
                      role="checkbox"
                      aria-checked={on}
                      onClick={() => toggle(project.id)}
                      className={`w-full text-left px-3 py-2 rounded transition-colors flex items-start gap-3 ${
                        on ? 'bg-bambu-green/20 text-bambu-green' : 'hover:bg-bambu-dark-tertiary text-white'
                      }`}
                    >
                      <span
                        className={`mt-0.5 w-4 h-4 shrink-0 rounded border flex items-center justify-center ${
                          on ? 'bg-bambu-green border-bambu-green' : 'border-bambu-gray'
                        }`}
                      >
                        {on && <Check className="w-3 h-3 text-white" />}
                      </span>
                      <span className="break-words min-w-0 flex-1">{project.name}</span>
                      {productIds.has(project.id) && (
                        <span className="shrink-0 flex items-center gap-1 text-xs text-bambu-gray">
                          <Package className="w-3.5 h-3.5" /> {t('farm.folders.product')}
                        </span>
                      )}
                    </button>
                  );
                })
              : shownArchives.map((archive) => (
                  <button
                    key={archive.id}
                    onClick={() => setArchiveId(archive.id)}
                    className={`w-full text-left px-3 py-2 rounded transition-colors flex items-start gap-2 ${
                      archiveId === archive.id ? 'bg-bambu-green/20 text-bambu-green' : 'hover:bg-bambu-dark-tertiary text-white'
                    }`}
                  >
                    <FileBox className="w-4 h-4 mt-0.5 text-bambu-gray shrink-0" />
                    <span className="break-words min-w-0">{archive.print_name || archive.filename}</span>
                  </button>
                ))}
            {tab === 'projects' && shownProjects.length === 0 && (
              <p className="text-sm text-bambu-gray text-center py-4">{t('fileManager.noProjectsFound')}</p>
            )}
            {tab === 'archive' && shownArchives.length === 0 && (
              <p className="text-sm text-bambu-gray text-center py-4">{t('fileManager.noArchivesFound')}</p>
            )}
          </div>
          {tab === 'projects' && <p className="text-xs text-bambu-gray">{t('farm.folders.subfolders')}</p>}
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex flex-wrap items-center gap-2 justify-between">
          <span className="text-sm text-bambu-gray">
            {tab === 'projects' ? t('farm.folders.selected', { count: chosen.size }) : ''}
          </span>
          <div className="flex gap-2">
            {tab === 'archive' && folder.archive_id && (
              <Button variant="danger" onClick={() => onLink({ archive_id: 0 })} disabled={isLoading}>
                {t('fileManager.unlink')}
              </Button>
            )}
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            {tab === 'projects' ? (
              <Button onClick={() => saveProjects.mutate()} disabled={selected === null || saveProjects.isPending}>
                {saveProjects.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('farm.products.save')}
              </Button>
            ) : (
              <Button onClick={() => archiveId && onLink({ archive_id: archiveId })} disabled={!archiveId || isLoading}>
                {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('fileManager.link')}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
