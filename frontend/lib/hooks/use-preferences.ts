'use client';

/**
 * Preferences for the trial build.
 *
 * The original hit a dedicated /users/me/preferences table with 11 settings
 * (style profile, temperature thresholds, AI endpoint rotation, variety
 * levels...). The trial has no such table: the two values the UI actually
 * reads — the unit for weather and the default occasion for suggestions — live
 * on the user row, which the API already exposes at /users/me.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

export interface TrialPreferences {
  default_occasion: string;
  temperature_unit: 'celsius' | 'fahrenheit';
}

export const DEFAULT_PREFERENCES: TrialPreferences = {
  default_occasion: 'casual',
  temperature_unit: 'celsius',
};

export function usePreferences() {
  return useQuery<TrialPreferences>({
    queryKey: ['preferences'],
    queryFn: async () => {
      const me = await api.get<{ default_occasion: string | null; temperature_unit: string | null }>(
        '/users/me'
      );
      return {
        default_occasion: me.default_occasion || DEFAULT_PREFERENCES.default_occasion,
        temperature_unit: me.temperature_unit === 'fahrenheit' ? 'fahrenheit' : 'celsius',
      };
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useUpdatePreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<TrialPreferences>) => api.patch('/users/me', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['preferences'] });
      queryClient.invalidateQueries({ queryKey: ['user'] });
    },
  });
}

export function useResetPreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.patch('/users/me', {
        default_occasion: DEFAULT_PREFERENCES.default_occasion,
        temperature_unit: DEFAULT_PREFERENCES.temperature_unit,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['preferences'] }),
  });
}

/** Not available in the trial: AI endpoints are configured by server env vars. */
export function useTestAIEndpoint() {
  return useMutation<{ ok: boolean; message: string }, Error, unknown>({
    mutationFn: async () => ({
      ok: false,
      message:
        'The trial configures the AI provider with server environment variables (AI_BASE_URL, AI_MODEL, AI_API_KEY).',
    }),
  });
}
