'use client';

import { useMemo } from 'react';
import { useAuth } from '@/lib/hooks/use-auth';
import { useTranslations } from 'next-intl';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Shirt,
  Sparkles,
  Plus,
  TrendingUp,
  Cloud,
  Droplets,
  ThumbsUp,
  ThumbsDown,
  Clock,
  Bell,
  BellOff,
  Calendar,
  CheckCircle2,
  XCircle,
  Lightbulb,
  ChevronRight,
  HeartHandshake,
  Users,
} from 'lucide-react';
import Link from 'next/link';
import Image from 'next/image';
import { useWeather } from '@/lib/hooks/use-weather';
import { usePreferences } from '@/lib/hooks/use-preferences';
import { displayValue, tempSymbol, TempUnit } from '@/lib/temperature';
import { usePendingOutfits, useAcceptOutfit, useRejectOutfit } from '@/lib/hooks/use-outfits';
import { toast } from 'sonner';

function WeatherCard() {
  const { data: weather, isLoading, isError } = useWeather();
  const { data: prefs } = usePreferences();
  const t = useTranslations('dashboard');
  const unit: TempUnit = prefs?.temperature_unit === 'fahrenheit' ? 'fahrenheit' : 'celsius';

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Cloud className="h-4 w-4" />
            {t('weather.title')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-12 w-24 mb-2" />
          <Skeleton className="h-4 w-32" />
        </CardContent>
      </Card>
    );
  }

  if (isError || !weather) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Cloud className="h-4 w-4" />
            {t('weather.title')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-3">
            {t('weather.locationNotSet')}
          </p>
          <Button size="sm" variant="outline" asChild>
            <Link href="/dashboard/settings">{t('weather.setLocation')}</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Cloud className="h-4 w-4" />
          {t('weather.title')}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-3xl font-bold">{displayValue(weather.temperature, unit)}{tempSymbol(unit)}</span>
          <span className="text-muted-foreground text-sm">
            {t('weather.feelsLike', { temp: `${displayValue(weather.feels_like, unit)}°` })}
          </span>
        </div>
        <p className="text-sm text-muted-foreground capitalize mb-1">
          {weather.condition}
        </p>
        {weather.precipitation_chance > 0 && (
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            <Droplets className="h-3 w-3" />
            {t('weather.rainChance', { percent: weather.precipitation_chance })}
          </p>
        )}
        <Button size="sm" className="w-full mt-3" asChild>
          <Link href="/dashboard/suggest">
            <Sparkles className="h-4 w-4 mr-1" />
            {t('weather.getOutfitSuggestion')}
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function PendingOutfitsCard() {
  const { data, isLoading } = usePendingOutfits(2);
  const acceptOutfit = useAcceptOutfit();
  const rejectOutfit = useRejectOutfit();
  const t = useTranslations('dashboard');
  const tc = useTranslations('common');

  const handleAccept = async (id: string) => {
    try {
      await acceptOutfit.mutateAsync(id);
      toast.success(t('pendingOutfits.accepted'));
    } catch {
      toast.error(t('pendingOutfits.acceptFailed'));
    }
  };

  const handleReject = async (id: string) => {
    try {
      await rejectOutfit.mutateAsync(id);
      toast.success(t('pendingOutfits.dismissed'));
    } catch {
      toast.error(t('pendingOutfits.dismissFailed'));
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Clock className="h-4 w-4" />
            {t('pendingOutfits.title')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full mb-2" />
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  const pendingOutfits = data?.outfits || [];

  if (pendingOutfits.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-green-500" />
            {t('pendingOutfits.allCaughtUp')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {t('pendingOutfits.noPending')}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Clock className="h-4 w-4 text-orange-500" />
            {t('pendingOutfits.title')}
            <Badge variant="secondary" className="ml-1">{data?.total || pendingOutfits.length}</Badge>
          </CardTitle>
          {(data?.total ?? 0) > 2 && (
            <Link href="/dashboard/history" className="text-xs text-muted-foreground hover:text-foreground">
              {tc('viewAll')}
            </Link>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {pendingOutfits.map((outfit) => (
          <div key={outfit.id} className="flex items-center gap-3">
            <div className="flex -space-x-2">
              {outfit.items.slice(0, 3).map((item) => (
                <div
                  key={item.id}
                  className="w-10 h-10 rounded-full bg-muted overflow-hidden relative border-2 border-background"
                >
                  {item.thumbnail_url ? (
                    <Image
                      src={item.thumbnail_url}
                      alt={item.name || item.type}
                      fill
                      className="object-cover"
                      sizes="40px"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <Shirt className="h-4 w-4 text-muted-foreground" />
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium capitalize truncate">{outfit.occasion}</p>
              <p className="text-xs text-muted-foreground">
                {outfit.scheduled_for ? new Date(outfit.scheduled_for).toLocaleDateString('en-US', {
                  weekday: 'short',
                  month: 'short',
                  day: 'numeric',
                }) : t('pendingOutfits.lookbook')}
              </p>
            </div>
            <div className="flex gap-1">
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 text-red-500 hover:text-red-600 hover:bg-red-50"
                onClick={() => handleReject(outfit.id)}
                disabled={rejectOutfit.isPending}
                aria-label={t('pendingOutfits.dismissLabel')}
              >
                <ThumbsDown className="h-4 w-4" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-8 w-8 text-green-500 hover:text-green-600 hover:bg-green-50"
                onClick={() => handleAccept(outfit.id)}
                disabled={acceptOutfit.isPending}
              >
                <ThumbsUp className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function QuickActionsCard() {
  const t = useTranslations('dashboard');

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('quickActions.title')}</CardTitle>
        <CardDescription>{t('quickActions.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Button asChild className="w-full justify-start">
          <Link href="/dashboard/wardrobe">
            <Plus className="mr-2 h-4 w-4" />
            {t('quickActions.addNewItem')}
          </Link>
        </Button>
        <Button asChild variant="outline" className="w-full justify-start">
          <Link href="/dashboard/suggest">
            <Sparkles className="mr-2 h-4 w-4" />
            {t('quickActions.getOutfitSuggestion')}
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const t = useTranslations('dashboard');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          {t('welcomeBack', { name: user?.display_name?.split(' ')[0] || t('userFallback') })}
        </h1>
        <p className="text-muted-foreground">
          {t('subtitle')}
        </p>
      </div>

      {/* Top row - Weather + Pending + Next Scheduled */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <WeatherCard />
        <PendingOutfitsCard />
      </div>

      {/* Quick Actions is the entry point to the whole trial flow */}
      <div className="grid gap-4 md:grid-cols-2">
        <QuickActionsCard />
      </div>
    </div>
  );
}
