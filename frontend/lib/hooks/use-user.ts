'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

export interface UserProfile {
  id: string;
  email: string;
  display_name: string;
  avatar_url?: string;
  locale: string;
  location_lat?: number;
  location_lon?: number;
  location_name?: string;
  default_occasion?: string | null;
  temperature_unit?: 'celsius' | 'fahrenheit';
  onboarding_completed: boolean;
}

export interface UserProfileUpdate {
  display_name?: string;
  locale?: string;
  location_lat?: number;
  location_lon?: number;
  location_name?: string;
  default_occasion?: string;
  temperature_unit?: 'celsius' | 'fahrenheit';
}

export function useUserProfile() {

  return useQuery({
    queryKey: ['user-profile'],
    queryFn: () => api.get<UserProfile>('/users/me'),
  });
}

export function useUpdateUserProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: UserProfileUpdate) => {
      return api.patch<UserProfile>('/users/me', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    },
  });
}
