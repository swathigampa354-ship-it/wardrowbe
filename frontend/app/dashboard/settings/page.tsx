'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocale, useTranslations } from 'next-intl';
import { Loader2, MapPin, Navigation, RotateCcw, Save, Server, Sparkles, Store } from 'lucide-react';
import { toast } from 'sonner';

import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useOccasions } from '@/lib/hooks/use-translated-constants';
import {
  DEFAULT_PREFERENCES,
  usePreferences,
  useResetPreferences,
  useUpdatePreferences,
} from '@/lib/hooks/use-preferences';
import { useUpdateUserProfile, useUserProfile } from '@/lib/hooks/use-user';

/**
 * Settings for the trial build.
 *
 * Everything the full product kept here that needed another service - the
 * style profile, body measurements, notification thresholds, per-user AI
 * endpoint keys, password/OIDC account controls - is gone. What survives is
 * the small set of values the trial's API really reads: display name,
 * location (for weather), default occasion and the temperature unit. AI
 * configuration itself is env-only, so this page *reports* it rather than
 * editing it; the keys never reach the browser.
 */

interface Capabilities {
  ai: {
    vision: boolean;
    text: boolean;
    provider: string | null;
    vision_models: string[];
    text_models: string[];
    reachability: { ok: boolean; message?: string; detail?: string };
  };
  features: Record<string, boolean>;
  auth: { required: boolean };
  storage: { s3: boolean; persistent: boolean };
  version: string;
}

interface AuthStatus {
  auth_required: boolean;
  mode: string;
  ai_configured: boolean;
  storage: string;
}

interface WeatherPreview {
  temperature?: number;
  temperature_unit?: string;
  condition?: string;
  location?: string;
  detail?: string;
}

function toF(c: number) {
  return Math.round(c * 1.8 + 32);
}

export default function SettingsPage() {
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const locale = useLocale();
  const occasions = useOccasions();

  const queryClient = useQueryClient();
  const { data: profile } = useUserProfile();
  const updateProfile = useUpdateUserProfile();
  const { data: prefs } = usePreferences();
  const updatePrefs = useUpdatePreferences();
  const resetPrefs = useResetPreferences();

  const { data: caps } = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => api.get<Capabilities>('/capabilities'),
    staleTime: 60_000,
  });
  const { data: authStatus } = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api.get<AuthStatus>('/auth/status'),
    staleTime: 5 * 60_000,
  });

  const [displayName, setDisplayName] = useState<string | null>(null);
  const [locationName, setLocationName] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const name = displayName ?? profile?.display_name ?? '';
  const city = locationName ?? profile?.location_name ?? '';

  const saveProfile = async () => {
    try {
      await updateProfile.mutateAsync({ display_name: name });
      toast.success(t('location.saved'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const saveCity = async () => {
    setPreviewError(null);
    setPreview(null);
    try {
      await api.post('/users/me/location', { location: city });
      setLocationName(city);
      setPreviewError(null);
      toast.success(t('location.saved'));
      // Refetch the profile so the coordinates the server resolved from the
      // name show up in the readout below and everywhere else.
      await queryClient.invalidateQueries({ queryKey: ['user-profile'] });
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : String(error));
    }
  };

  const detectLocation = () => {
    if (!navigator.geolocation) {
      setPreviewError(t('location.errors.geolocationUnsupported'));
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const { latitude, longitude } = pos.coords;
          await api.post('/users/me/location', { latitude, longitude });
          setLocationName(`${latitude.toFixed(2)}, ${longitude.toFixed(2)}`);
          toast.success(t('location.detected'));
          await queryClient.invalidateQueries({ queryKey: ['user-profile'] });
        } catch (error) {
          setPreviewError(error instanceof Error ? error.message : String(error));
        } finally {
          setLocating(false);
        }
      },
      () => {
        setLocating(false);
        setPreviewError(t('location.errors.geolocationDenied'));
      },
      { timeout: 10_000 }
    );
  };

  const testWeather = async () => {
    setPreviewError(null);
    setPreview(null);
    try {
      const body: Record<string, number | string> = city ? { location: city } : {};
      const w = await api.post<WeatherPreview>('/weather/preview', body);
      const celsius = typeof w.temperature === 'number' ? w.temperature : null;
      const temp =
        celsius === null
          ? ''
          : `${(prefs?.temperature_unit ?? 'celsius') === 'fahrenheit' ? toF(celsius) : Math.round(celsius)}°${
              (prefs?.temperature_unit ?? 'celsius') === 'fahrenheit' ? 'F' : 'C'
            }`;
      setPreview([w.location, w.condition, temp].filter(Boolean).join(' · ') || JSON.stringify(w));
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
      </div>

      {/* ------------------------------------------------------- account --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('account.title')}</CardTitle>
          <CardDescription>{t('account.description')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="display-name">{t('account.name')}</Label>
            <div className="flex gap-2">
              <Input
                id="display-name"
                value={name}
                onChange={(e) => setDisplayName(e.target.value)}
                maxLength={100}
              />
              <Button
                variant="outline"
                onClick={saveProfile}
                disabled={updateProfile.isPending || name === (profile?.display_name ?? '')}
              >
                {updateProfile.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Save className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t('account.email')}: {profile?.email ?? 'demo@wardrowbe.local'}
            </p>
          </div>
          <div className="space-y-2">
            <Label>{t('account.language', { defaultValue: 'Language' })}</Label>
            <p className="text-sm text-muted-foreground">
              {locale} — {t('account.languageNote', {
                defaultValue: 'The trial ships with English strings only.',
              })}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* -------------------------------------------------------- location --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <MapPin className="h-4 w-4" />
            {t('location.cityLabel')}
          </CardTitle>
          <CardDescription>{t('location.description')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              value={city}
              placeholder={t('location.cityPlaceholder')}
              onChange={(e) => setLocationName(e.target.value)}
              className="flex-1"
            />
            <Button variant="outline" onClick={saveCity} disabled={!city.trim()}>
              {t('location.saveLocation')}
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={detectLocation} disabled={locating}>
              {locating ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Navigation className="h-4 w-4 mr-2" />
              )}
              {t('location.useMyLocation')}
            </Button>
            <Button variant="ghost" size="sm" onClick={testWeather}>
              {t('location.test', { defaultValue: 'Check weather' })}
            </Button>
          </div>
          {preview && (
            <p className="text-sm text-muted-foreground rounded-md border bg-muted/40 px-3 py-2">
              {preview}
            </p>
          )}
          {previewError && <p className="text-sm text-destructive">{previewError}</p>}
          {profile?.location_lat != null && profile?.location_lon != null && (
            <p className="text-xs text-muted-foreground">
              {t('location.latitude')}: {profile.location_lat?.toFixed(3)} ·{' '}
              {t('location.longitude')}: {profile.location_lon?.toFixed(3)}
            </p>
          )}
        </CardContent>
      </Card>

      {/* --------------------------------------------------- recommendations --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('recommendations.title')}</CardTitle>
          <CardDescription>{t('recommendations.description')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>{t('recommendations.defaultOccasion')}</Label>
            <Select
              value={prefs?.default_occasion ?? DEFAULT_PREFERENCES.default_occasion}
              onValueChange={(value) => updatePrefs.mutate({ default_occasion: value })}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {occasions.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>{t('temperature.unit')}</Label>
            <Select
              value={prefs?.temperature_unit ?? DEFAULT_PREFERENCES.temperature_unit}
              onValueChange={(value) =>
                updatePrefs.mutate({ temperature_unit: value as 'celsius' | 'fahrenheit' })
              }
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="celsius">{t('temperature.celsius')}</SelectItem>
                <SelectItem value="fahrenheit">{t('temperature.fahrenheit')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => resetPrefs.mutate()}
            disabled={resetPrefs.isPending}
          >
            <RotateCcw className="h-4 w-4 mr-2" />
            {t('reset')}
          </Button>
        </CardContent>
      </Card>

      {/* --------------------------------------------------------------- AI --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            {t('ai.title', { defaultValue: 'AI provider' })}
          </CardTitle>
          <CardDescription>
            {t('ai.description', {
              defaultValue:
                'The trial reads its AI credentials from the server environment, so they can be rotated without a rebuild and never reach the browser.',
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t('ai.status', { defaultValue: 'Status' })}</span>
            <Badge variant={caps?.ai.vision ? 'default' : 'destructive'}>
              {caps?.ai.vision
                ? t('ai.configured', { defaultValue: 'Configured' })
                : t('ai.missing', { defaultValue: 'No API key set' })}
            </Badge>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t('ai.reachability', { defaultValue: 'Reachable' })}</span>
            <span className="truncate max-w-[60%]" title={caps?.ai.reachability?.message ?? ''}>
              {caps?.ai.reachability?.ok ? 'ok' : (caps?.ai.reachability?.message ?? '…')}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t('ai.baseUrl', { defaultValue: 'Base URL' })}</span>
            <code className="text-xs truncate max-w-[60%]">{caps?.ai.provider ?? '—'}</code>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">
              {t('ai.visionModels', { defaultValue: 'Vision models' })}
            </span>
            <code className="text-xs truncate max-w-[60%]">
              {caps?.ai.vision_models?.join(', ') || '—'}
            </code>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">
              {t('ai.textModels', { defaultValue: 'Text models' })}
            </span>
            <code className="text-xs truncate max-w-[60%]">
              {caps?.ai.text_models?.join(', ') || '—'}
            </code>
          </div>
          {!caps?.ai.vision && (
            <p className="text-xs text-muted-foreground">
              {t('ai.howTo', {
                defaultValue:
                  'Set AI_API_KEY (and AI_BASE_URL / AI_VISION_MODEL if you are not using the default provider) on the server and restart it.',
              })}
            </p>
          )}
        </CardContent>
      </Card>

      {/* --------------------------------------------------------- storage --- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Store className="h-4 w-4" />
            {t('storage.title', { defaultValue: 'Data & limits' })}
          </CardTitle>
          <CardDescription>
            {t('storage.description', {
              defaultValue:
                'Where this build keeps your photos and rows - and what that means for a free-tier trial.',
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground flex items-center gap-2">
              <Server className="h-4 w-4" />
              {t('storage.backend', { defaultValue: 'Image storage' })}
            </span>
            <span>
              {caps?.storage.s3 ? (
                <Badge variant="outline">S3</Badge>
              ) : (
                <Badge variant="outline">
                  {t('storage.local', { defaultValue: 'Local disk (ephemeral)' })}
                </Badge>
              )}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            {caps?.storage.persistent
              ? t('storage.persistentNote', {
                  defaultValue: 'Objects live in an S3-compatible bucket, so they survive redeploys.',
                })
              : t('storage.ephemeralNote', {
                  defaultValue:
                    'Render\'s free tier has no persistent disk: photos are re-downloaded by the AI but deleted when the service restarts. Set STORAGE_S3_BUCKET (and its key/secret) if you need uploads to last.',
                })}
          </p>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t('storage.auth', { defaultValue: 'Sign-in' })}</span>
            <span>
              {authStatus?.auth_required
                ? t('storage.authOn', { defaultValue: 'Password required' })
                : t('storage.authOff', { defaultValue: 'None - single demo user' })}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{t('storage.version', { defaultValue: 'Build' })}</span>
            <code className="text-xs">{caps?.version ?? '…'}</code>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
