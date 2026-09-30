'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export interface Weather {
  temperature: number;
  feels_like: number;
  humidity: number;
  precipitation_chance: number;
  precipitation_mm: number;
  wind_speed: number;
  condition: string;
  condition_code: number;
  is_day: boolean;
  uv_index: number;
  timestamp: string;
}

export function useWeather() {

  return useQuery({
    queryKey: ['weather'],
    queryFn: () => api.get<Weather>('/weather/current'),
    staleTime: 1000 * 60 * 15, // 15 minutes - weather doesn't change that fast
    retry: false, // Don't retry if location not set
  });
}
