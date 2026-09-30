'use client';

/**
 * Auth for the trial build.
 *
 * Upstream this hook drove a NextAuth session: it read `session.accessToken`,
 * pushed it into the API client and bounced visitors to /login on a 401. The
 * trial runs with `REQUIRE_AUTH=false`, where the backend answers every
 * request as one implicit demo user, so there is nothing to sign in to - but
 * the header still wants a display name, and the dashboard still wants to
 * know whether the row in the database is a brand-new visitor.
 *
 * If you re-enable `REQUIRE_AUTH=true`, call `POST /api/v1/auth/sync` with a
 * password (or the shared secret of your choosing), hand the returned token to
 * `setAccessToken()` from `lib/api.ts`, and put a login page back in front of
 * this.
 */

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { UserProfile } from './use-user';

export interface AuthStatus {
  auth_required: boolean;
  mode: string;
  oidc_enabled: boolean;
  demo_password_enabled: boolean;
  ai_configured: boolean;
  storage: string;
}

export function useAuthStatus() {
  return useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/status'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useAuth() {
  const userQuery = useQuery({
    queryKey: ['auth-user'],
    queryFn: () => api.get<UserProfile>('/users/me'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const status = useAuthStatus();

  return {
    user: userQuery.data,
    // Nothing gates the trial, so "authenticated" is whatever the API says.
    isAuthenticated: !userQuery.isError,
    isLoading: userQuery.isPending || status.isPending,
    error: userQuery.error ?? status.error,
    authStatus: status.data,
    signOut: async () => {
      /* no session to clear */
    },
  };
}
