// Printfarm (fork) products API: see backend/app/farm/products/routes.py.
import { getAuthToken } from '../api/client';

export interface FarmPlate {
  library_file_id: number;
  plate: number;
  printer_model: string;
  material: string;
  yield: number;
}

export interface FarmPart {
  component_code: string;
  name: string;
  quantity: number;
  plates: FarmPlate[];
}

export interface FarmProduct {
  project_id: number;
  name: string;
  skus: string[];
  asins: string[];
  updated_at: string;
  parts: FarmPart[];
}

export interface FarmComponent {
  code: string;
  name: string;
  shape_id: number | null;
  shape: string | null;
  color: string | null;
  retired: boolean;
}

export interface FarmPlateSummary {
  id: number;
  library_file_id: number;
  plate_index: number;
  status: string;
  yields: { object_name: string; quantity: number; shape_id: number | null; component_code: string | null }[];
}

export interface FarmShape {
  id: number;
  name: string;
}

export interface FarmProductOverview {
  project_id: number;
  has_cover: boolean;
  queued: number;
  printing: number;
  printed_recent: number;
}

export interface FarmProductInput {
  name?: string;
  project_id?: number;
  skus: string[];
  asins: string[];
  parts: { component_code: string; quantity: number }[];
}

export async function farmRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(`/api/v1/farm${path}`, {
    ...init,
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = Array.isArray(error.detail) ? error.detail.map((d: { msg?: string }) => d.msg).join('; ') : error.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export const farmApi = {
  getProducts: () => farmRequest<{ generated_at: string; products: FarmProduct[] }>('/products'),
  getOverview: () => farmRequest<FarmProductOverview[]>('/products/overview'),
  getComponents: () => farmRequest<FarmComponent[]>('/components'),
  createProduct: (data: FarmProductInput) =>
    farmRequest<{ project_id: number }>('/products', { method: 'POST', body: JSON.stringify(data) }),
  updateProduct: (id: number, data: FarmProductInput) =>
    farmRequest<{ project_id: number }>(`/products/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  unmarkProduct: (id: number) => farmRequest<{ project_id: number }>(`/products/${id}`, { method: 'DELETE' }),
  createComponent: (data: { name?: string; shape_id?: number; color?: string }) =>
    farmRequest<FarmComponent>('/components', { method: 'POST', body: JSON.stringify(data) }),
  updateComponent: (code: string, data: { name?: string; retired?: boolean }) =>
    farmRequest<FarmComponent>(`/components/${code}`, { method: 'PATCH', body: JSON.stringify(data) }),
  getShapes: () => farmRequest<FarmShape[]>('/shapes'),
  getProjectNames: () => farmRequest<{ id: number; name: string }[]>('/projects'),
  renameShape: (id: number, name: string) =>
    farmRequest<FarmShape>(`/shapes/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  getPlates: (status?: string) =>
    farmRequest<FarmPlateSummary[]>(`/plates${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getFolderProjects: (folderId: number) =>
    farmRequest<{ folder_id: number; project_ids: number[] }>(`/folders/${folderId}/projects`),
  setFolderProjects: (folderId: number, projectIds: number[]) =>
    farmRequest<{ folder_id: number; project_ids: number[] }>(`/folders/${folderId}/projects`, {
      method: 'PUT',
      body: JSON.stringify({ project_ids: projectIds }),
    }),
  createShape: (name: string) => farmRequest<FarmShape>('/shapes', { method: 'POST', body: JSON.stringify({ name }) }),
};
