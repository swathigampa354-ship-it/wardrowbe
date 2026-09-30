import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

// Helper to set token if available (for NextAuth mode)
export interface OutfitItem {
  id: string;
  type: string;
  subtype: string | null;
  name: string | null;
  primary_color: string | null;
  colors: string[];
  image_path: string;
  thumbnail_path: string | null;
  thumbnail_url?: string;
  image_url?: string;
  layer_type: string | null;
  position: number;
}

export interface FeedbackSummary {
  rating: number | null;
  comment: string | null;
  worn_at: string | null;
  actually_worn: boolean | null;
}

export type OutfitSource = 'scheduled' | 'on_demand' | 'manual' | 'pairing' | 'external';

export interface Outfit {
  id: string;
  occasion: string;
  scheduled_for: string | null;
  status: 'pending' | 'sent' | 'viewed' | 'accepted' | 'rejected' | 'skipped' | 'expired';
  source: OutfitSource;
  name: string | null;
  reasoning: string | null;
  style_notes: string | null;
  season: string | null;
  formality: string | null;
  palette: string[] | null;
  notes: string | null;
  highlights: string[] | null;
  weather: Record<string, unknown> | null;
  items: OutfitItem[];
  feedback: FeedbackSummary | null;
  is_starter_suggestion?: boolean;
  created_at: string;
}

export interface OutfitListResponse {
  outfits: Outfit[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface OutfitFilters {
  status?: string;
  occasion?: string;
  date_from?: string;
  date_to?: string;
  source?: string;
  search?: string;
}

export interface FeedbackData {
  accepted?: boolean;
  rating?: number;
  comfort_rating?: number;
  style_rating?: number;
  comment?: string;
  worn?: boolean;
  actually_worn?: boolean;
}

export interface FeedbackResponse {
  id: string;
  outfit_id: string;
  accepted: boolean | null;
  rating: number | null;
  comfort_rating: number | null;
  style_rating: number | null;
  comment: string | null;
  worn_at: string | null;
  worn_with_modifications: boolean;
  modification_notes: string | null;
  actually_worn: boolean | null;
  wore_instead_items: string[] | null;
  created_at: string;
}

export function useOutfits(filters: OutfitFilters = {}, page = 1, pageSize = 20) {

  const params: Record<string, string> = {
    page: String(page),
    page_size: String(pageSize),
  };

  if (filters.status) params.status = filters.status;
  if (filters.occasion) params.occasion = filters.occasion;
  if (filters.date_from) params.date_from = filters.date_from;
  if (filters.date_to) params.date_to = filters.date_to;
  if (filters.source) params.source = filters.source;
  if (filters.search) params.search = filters.search;

  return useQuery({
    queryKey: ['outfits', filters, page, pageSize],
    queryFn: () => api.get<OutfitListResponse>('/outfits', { params }),
  });
}

export function useOutfit(outfitId: string | undefined) {

  return useQuery({
    queryKey: ['outfit', outfitId],
    queryFn: () => api.get<Outfit>(`/outfits/${outfitId}`),
    enabled: !!outfitId,
  });
}

export function useAcceptOutfit() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (outfitId: string) => api.post<Outfit>(`/outfits/${outfitId}/accept`),
    onSuccess: (_, outfitId) => {
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
      queryClient.invalidateQueries({ queryKey: ['outfit', outfitId] });
      queryClient.invalidateQueries({ queryKey: ['calendarOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['pendingOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['analytics'] });
    },
  });
}

export function useRejectOutfit() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (outfitId: string) => api.post<Outfit>(`/outfits/${outfitId}/reject`),
    onSuccess: (_, outfitId) => {
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
      queryClient.invalidateQueries({ queryKey: ['outfit', outfitId] });
      queryClient.invalidateQueries({ queryKey: ['calendarOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['pendingOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['analytics'] });
    },
  });
}

export function useSubmitFeedback() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ outfitId, feedback }: { outfitId: string; feedback: FeedbackData }) =>
      api.post<FeedbackResponse>(`/outfits/${outfitId}/feedback`, feedback),
    onSuccess: (_, { outfitId }) => {
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
      queryClient.invalidateQueries({ queryKey: ['outfit', outfitId] });
      queryClient.invalidateQueries({ queryKey: ['calendarOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['pendingOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['analytics'] });
    },
  });
}

export function useDeleteOutfit() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (outfitId: string) => api.delete<void>(`/outfits/${outfitId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
      queryClient.invalidateQueries({ queryKey: ['calendarOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['pendingOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['analytics'] });
    },
  });
}

export interface BulkDeleteOutfitsResponse {
  deleted: number;
  failed: number;
  errors: string[];
}

export interface BulkOutfitOperationParams {
  // Either provide explicit outfit_ids, or use select_all with excluded_ids
  outfit_ids?: string[];
  select_all?: boolean;
  excluded_ids?: string[];
  // Filters to apply when using select_all (to match the current view)
  filters?: OutfitFilters;
}

export function useBulkDeleteOutfits() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (params: BulkOutfitOperationParams) => {
      return api.post<BulkDeleteOutfitsResponse>('/outfits/bulk/delete', params);
    },
    onMutate: async (params) => {
      await queryClient.cancelQueries({ queryKey: ['outfits'] });

      const previousData = queryClient.getQueriesData({ queryKey: ['outfits'] });

      if (params.select_all) {
        const excludedSet = new Set(params.excluded_ids || []);
        queryClient.setQueriesData({ queryKey: ['outfits'] }, (old: OutfitListResponse | undefined) => {
          if (!old) return old;
          return {
            ...old,
            outfits: old.outfits.filter((outfit) => excludedSet.has(outfit.id)),
            total: excludedSet.size,
          };
        });
      } else if (params.outfit_ids) {
        const deletedSet = new Set(params.outfit_ids);
        queryClient.setQueriesData({ queryKey: ['outfits'] }, (old: OutfitListResponse | undefined) => {
          if (!old) return old;
          return {
            ...old,
            outfits: old.outfits.filter((outfit) => !deletedSet.has(outfit.id)),
            total: old.total - params.outfit_ids!.length,
          };
        });
      }

      return { previousData };
    },
    onError: (_err, _params, context) => {
      if (context?.previousData) {
        context.previousData.forEach(([queryKey, data]) => {
          queryClient.setQueryData(queryKey, data);
        });
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['outfits'] });
      queryClient.invalidateQueries({ queryKey: ['calendarOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['pendingOutfits'] });
      queryClient.invalidateQueries({ queryKey: ['analytics'] });
    },
  });
}

export function useCalendarOutfits(year: number, month: number, filters: OutfitFilters = {}) {

  // Calculate date range for the month
  const date_from = `${year}-${String(month).padStart(2, '0')}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const date_to = `${year}-${String(month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`;

  const params: Record<string, string> = {
    page: '1',
    page_size: '100', // Get all outfits for the month
    date_from,
    date_to,
  };

  if (filters.status) params.status = filters.status;
  if (filters.occasion) params.occasion = filters.occasion;

  return useQuery({
    queryKey: ['calendarOutfits', year, month, filters],
    queryFn: () => api.get<OutfitListResponse>('/outfits', { params }),
  });
}

export function usePendingOutfits(limit = 3) {

  const params: Record<string, string> = {
    page: '1',
    page_size: String(limit),
    status: 'pending',
  };

  return useQuery({
    queryKey: ['pendingOutfits', limit],
    queryFn: () => api.get<OutfitListResponse>('/outfits', { params }),
  });
}
