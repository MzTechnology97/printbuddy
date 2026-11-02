// Printfarm (fork) NAS settings API: see backend/app/farm/nas/routes.py.
import { farmRequest } from './api';

export interface NasSettings {
  host: string;
  share: string;
  path: string;
  username: string;
  password_set: boolean;
  scan_interval_seconds: number;
  folder_id: number | null;
  last_scan_at: string | null;
  last_result: Record<string, number | string> | null;
}

export interface NasConnection {
  host: string;
  share: string;
  username: string;
  // Omitted: the backend uses the stored password (it is never sent back).
  password?: string;
}

export interface NasSettingsUpdate extends NasConnection {
  path: string;
  scan_interval_seconds: number;
}

export const farmNasApi = {
  get: () => farmRequest<NasSettings>('/nas'),
  save: (data: NasSettingsUpdate) => farmRequest<NasSettings>('/nas', { method: 'PUT', body: JSON.stringify(data) }),
  test: (data: NasSettingsUpdate) =>
    farmRequest<{ ok: boolean; directories: number; print_files: number }>('/nas/test', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  browse: (data: NasConnection & { path: string }) =>
    farmRequest<{ path: string; directories: string[] }>('/nas/browse', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  sync: () => farmRequest<Record<string, unknown>>('/nas/sync', { method: 'POST' }),
};
