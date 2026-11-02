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

async function farmRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
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
  createComponent: (name: string) =>
    farmRequest<FarmComponent>('/components', { method: 'POST', body: JSON.stringify({ name }) }),
  renameComponent: (code: string, name: string) =>
    farmRequest<FarmComponent>(`/components/${code}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
};
