'use client';

/**
 * Item hooks for the trial build.
 *
 * The upstream file was 1,294 lines because it drove a durable, offline-first
 * upload pipeline: IndexedDB staging (lib/upload-queue.ts), a background drain
 * loop (lib/upload-manager.ts), queue cancellation, batch cursors for
 * "select all", pHash duplicate reports, wash logging, multi-photo galleries,
 * rotation and background-removal jobs. All of that exists only because
 * analysis used to happen in a Redis/arq worker.
 *
 * Here the API analyzes synchronously, so an upload is a single request that
 * already contains the tags. These hooks keep the same names and return shapes
 * so the existing UI works unchanged; the removed features are deleted from the
 * UI too, not left as buttons that call missing endpoints.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getAccessToken, ApiError, NetworkError } from '@/lib/api';
import { Item, ItemListResponse, ItemFilter, TaggingProgress, BulkUploadResponse } from '@/lib/types';
import { chunkArray } from '@/lib/utils';

// The trial's server cap (MAX_BULK_UPLOAD_COUNT); chunk so one request never
// carries the whole wardrobe.
const BULK_UPLOAD_CHUNK_SIZE = 20;

export function useItems(filters: ItemFilter = {}, page = 1, pageSize = 20) {

  return useQuery({
    queryKey: ['items', filters, page, pageSize],
    queryFn: async () => {
      const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
      if (filters.type) params.type = filters.type;
      if (filters.colors?.length) params.colors = filters.colors.join(',');
      if (filters.search) params.search = filters.search;
      if (filters.favorite !== undefined) params.favorite = String(filters.favorite);
      if (filters.needs_wash !== undefined) params.needs_wash = String(filters.needs_wash);
      if (filters.is_archived !== undefined) params.is_archived = String(filters.is_archived);
      if (filters.sort_by) params.sort_by = filters.sort_by;
      if (filters.sort_order) params.sort_order = filters.sort_order;
      if (filters.ids) params.ids = filters.ids;

      return api.get<ItemListResponse>('/items', { params });
    },
    // Analysis is synchronous, so a "processing" row only appears if an upload
    // is still in flight in another tab. Poll fast while that is true.
    refetchInterval: (query) => {
      const data = query.state.data as ItemListResponse | undefined;
      return data?.items?.some((item) => item.status === 'processing') ? 5000 : 30000;
    },
  });
}

export function useTaggingProgress() {
  return useQuery({
    queryKey: ['tagging-progress'],
    queryFn: () => api.get<TaggingProgress>('/items/tagging-progress'),
    refetchInterval: 30000,
  });
}

export function useItem(itemId: string) {
  return useQuery({
    queryKey: ['item', itemId],
    queryFn: () => api.get<Item>(`/items/${itemId}`),
    enabled: !!itemId,
  });
}

export function useItemTypes() {
  return useQuery({
    queryKey: ['item-types'],
    queryFn: () => api.get<{ types: { type: string; count: number }[] }>('/items/types'),
    staleTime: 5 * 60 * 1000,
  });
}

export async function uploadSingleItem(file: File, name?: string): Promise<Item> {
  const token = getAccessToken();
  const formData = new FormData();
  formData.append('image', file);
  if (name) formData.append('name', name);

  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch('/api/v1/items', {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers,
    });
  } catch {
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      throw new NetworkError('You appear to be offline. Please check your connection.');
    }
    throw new NetworkError('Unable to connect to the server. Please try again.');
  }

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof data.detail === 'string' ? data.detail : data.detail?.message || 'Upload failed',
      response.status,
      data
    );
  }
  return response.json();
}

export function useCreateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (formData: FormData) => {
      const token = getAccessToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;

      let response: Response;
      try {
        response = await fetch('/api/v1/items', {
          method: 'POST',
          body: formData,
          credentials: 'include',
          headers,
        });
      } catch {
        if (typeof navigator !== 'undefined' && !navigator.onLine) {
          throw new NetworkError('You appear to be offline. Please check your connection.');
        }
        throw new NetworkError('Unable to connect to the server. Please try again.');
      }

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(data.detail || 'Failed to create item', response.status, data);
      }
      return response.json() as Promise<Item>;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['items'] }),
  });
}

export function useUpdateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<Item> }) =>
      api.patch<Item>(`/items/${id}`, data),
    onSuccess: (updatedItem, variables) => {
      queryClient.setQueryData(['item', variables.id], updatedItem);
      queryClient.setQueriesData({ queryKey: ['items'] }, (old: ItemListResponse | undefined) =>
        old
          ? { ...old, items: old.items.map((item) => (item.id === variables.id ? updatedItem : item)) }
          : old
      );
    },
    onSettled: (_data, _error, variables) => {
      queryClient.invalidateQueries({ queryKey: ['items'] });
      queryClient.invalidateQueries({ queryKey: ['item', variables.id] });
    },
  });
}

export function useDeleteItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.delete(`/items/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['items'] });
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
    },
  });
}

export function useReplaceItemImage() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ itemId, file }: { itemId: string; file: File }) => {
      const token = getAccessToken();
      const formData = new FormData();
      formData.append('image', file);
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/v1/items/${itemId}/image`, {
        method: 'PUT',
        body: formData,
        credentials: 'include',
        headers,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new ApiError(data.detail || 'Failed to replace image', response.status, data);
      }
      return response.json() as Promise<Item>;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['items'] });
      queryClient.invalidateQueries({ queryKey: ['item', variables.itemId] });
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
    },
  });
}

export function useReanalyzeItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.post<Item>(`/items/${id}/analyze`),
    onSuccess: (item) => {
      queryClient.setQueryData(['item', item.id], item);
      queryClient.invalidateQueries({ queryKey: ['items'] });
      queryClient.invalidateQueries({ queryKey: ['tagging-progress'] });
    },
  });
}

export interface BulkOperationParams {
  item_ids?: string[];
  select_all?: boolean;
  excluded_ids?: string[];
  filters?: ItemFilter;
}

export function useBulkDeleteItems() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (params: BulkOperationParams) => {
      const ids = params.select_all
        ? (
            await api.get<ItemListResponse>('/items', {
              params: { page: '1', page_size: '100', ...(params.filters as Record<string, string>) },
            })
          ).items.map((i) => i.id)
        : params.item_ids || [];
      const filtered = params.excluded_ids?.length
        ? ids.filter((id) => !params.excluded_ids!.includes(id))
        : ids;
      return api.post<BulkUploadResponse>('/items/bulk/delete', filtered);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['items'] }),
  });
}

/** XHR is used instead of fetch so we can report upload progress. */
function uploadBulkChunk(
  files: File[],
  skipAi: boolean,
  onProgress: (percent: number) => void
): Promise<BulkUploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    files.forEach((file) => formData.append('images', file));
    formData.append('skip_ai', String(skipAi));

    const token = getAccessToken();
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as BulkUploadResponse);
        } catch {
          reject(new ApiError('Invalid response from server', xhr.status, {}));
        }
      } else {
        let message = 'Failed to upload items';
        try {
          message = JSON.parse(xhr.responseText).detail || message;
        } catch {
          /* keep the default */
        }
        reject(new ApiError(message, xhr.status, {}));
      }
    });
    xhr.addEventListener('error', () => {
      if (typeof navigator !== 'undefined' && !navigator.onLine) {
        reject(new NetworkError('You appear to be offline. Please check your connection.'));
      } else {
        reject(new NetworkError('Unable to connect to the server. Please try again.'));
      }
    });
    xhr.addEventListener('abort', () => reject(new NetworkError('Upload was cancelled.')));

    xhr.open('POST', '/api/v1/items/bulk');
    xhr.withCredentials = true;
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.send(formData);
  });
}

const BULK_LIMIT_ERROR = /^Maximum (\d+) images per bulk upload$/;

/** Split a chunk that the server rejected for size, using the limit it reported. */
export async function uploadFilesWithinServerLimit(
  files: File[],
  skipAi: boolean,
  onProgress: (percent: number) => void
): Promise<BulkUploadResponse> {
  try {
    return await uploadBulkChunk(files, skipAi, onProgress);
  } catch (error) {
    const match =
      error instanceof ApiError && error.status === 400
        ? error.message.match(BULK_LIMIT_ERROR)
        : null;
    const limit = match ? Number(match[1]) : null;
    if (limit && limit > 0 && limit < files.length) {
      const responses: BulkUploadResponse[] = [];
      for (let i = 0; i < files.length; i += limit) {
        responses.push(
          await uploadFilesWithinServerLimit(files.slice(i, i + limit), skipAi, onProgress)
        );
      }
      return mergeBulkUploadResponses(responses);
    }
    throw error;
  }
}

export function mergeBulkUploadResponses(responses: BulkUploadResponse[]): BulkUploadResponse {
  return responses.reduce<BulkUploadResponse>(
    (acc, response) => ({
      total: acc.total + response.total,
      successful: acc.successful + response.successful,
      failed: acc.failed + response.failed,
      results: [...acc.results, ...response.results],
    }),
    { total: 0, successful: 0, failed: 0, results: [] }
  );
}

function failedChunkResponse(files: File[], error: unknown): BulkUploadResponse {
  const message =
    error instanceof ApiError || error instanceof NetworkError
      ? error.message
      : 'Failed to upload items';
  return {
    total: files.length,
    successful: 0,
    failed: files.length,
    results: files.map((file) => ({ filename: file.name, success: false, error: message })),
  };
}

/**
 * Bulk upload. Upstream staged files in IndexedDB so a closed tab did not lose
 * them; with synchronous analysis a chunk finishes in one request, so the
 * durable queue is gone and progress is just "how many chunks are done".
 */
export function useBulkCreateItems() {
  const queryClient = useQueryClient();
  const [uploadProgress, setUploadProgress] = useState(0);

  const mutation = useMutation({
    mutationFn: async ({
      files,
      skipAi = false,
    }: {
      files: File[];
      skipAi?: boolean;
    }): Promise<BulkUploadResponse> => {
      const chunks = chunkArray(files, BULK_UPLOAD_CHUNK_SIZE);
      const responses: BulkUploadResponse[] = [];
      for (let i = 0; i < chunks.length; i++) {
        const chunkFiles = chunks[i];
        try {
          responses.push(
            await uploadFilesWithinServerLimit(chunkFiles, skipAi, (chunkPercent) => {
              setUploadProgress(Math.round(((i + chunkPercent / 100) / chunks.length) * 100));
            })
          );
        } catch (error) {
          responses.push(failedChunkResponse(chunkFiles, error));
        }
        setUploadProgress(Math.round(((i + 1) / chunks.length) * 100));
      }
      return mergeBulkUploadResponses(responses);
    },
    onMutate: () => setUploadProgress(0),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['items'] });
      queryClient.invalidateQueries({ queryKey: ['tagging-progress'] });
    },
    onSettled: () => setUploadProgress(0),
  });

  return { ...mutation, uploadProgress };
}

/* ---- helpers kept for UI compatibility ---- */

export function tagProcessingLabel(
  item: Pick<Item, 'status'>
): 'queued' | 'analyzing' | 'done' | 'failed' {
  if (item.status === 'processing') return 'analyzing';
  if (item.status === 'error') return 'failed';
  if (item.status === 'ready') return 'done';
  return 'queued';
}

export function formatDurationSeconds(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined) return null;
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

export interface QueueSummary {
  batchTotal: number;
  batchDone: number;
  batchFailed: number;
  remaining: number;
  percentComplete: number;
}

export function deriveQueueSummary(progress?: TaggingProgress): QueueSummary {
  const total = progress?.total ?? 0;
  const done = progress?.completed ?? 0;
  const failed = progress?.failed ?? 0;
  return {
    batchTotal: total,
    batchDone: done,
    batchFailed: failed,
    remaining: progress?.processing ?? 0,
    percentComplete: total > 0 ? Math.round(((done + failed) / total) * 100) : 0,
  };
}
