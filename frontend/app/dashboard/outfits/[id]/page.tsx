'use client';

import Link from 'next/link';
import Image from 'next/image';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { ChevronLeft, Star, ThumbsDown, ThumbsUp, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useAcceptOutfit,
  useDeleteOutfit,
  useOutfit,
  useRejectOutfit,
} from '@/lib/hooks/use-outfits';
import { getErrorMessage } from '@/lib/api';

/**
 * Outfit detail.
 *
 * The full product reached this page through the lookbook: a lineage card, a
 * "wear today" clone, and a list of every day a template was worn. Cloning is
 * the studio's job and the studio is not in the trial, so what is left here is
 * the outfit itself - its items, the AI's reasoning, and the accept/reject
 * actions the suggestion loop is actually built on.
 */
export default function OutfitDetailPage() {
  const t = useTranslations('outfits');
  const tc = useTranslations('common');
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const outfitId = params?.id;

  const { data: outfit, isLoading } = useOutfit(outfitId);
  const deleteMutation = useDeleteOutfit();
  const acceptMutation = useAcceptOutfit();
  const rejectMutation = useRejectOutfit();

  if (isLoading || !outfit) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const handleDelete = async () => {
    if (!confirm(t('detail.deleteConfirm'))) return;
    try {
      await deleteMutation.mutateAsync(outfit.id);
      toast.success(t('detail.deleted'));
      router.push('/dashboard/outfits');
    } catch (error) {
      toast.error(getErrorMessage(error, t('detail.deleteError')));
    }
  };

  const decide = async (accepted: boolean) => {
    try {
      await (accepted ? acceptMutation : rejectMutation).mutateAsync(outfit.id);
      toast.success(accepted ? t('cards.accepted') : t('cards.rejectedToast'));
    } catch (error) {
      toast.error(getErrorMessage(error, t('loadError')));
    }
  };

  const title =
    outfit.name ||
    outfit.reasoning ||
    t('cards.outfitFallback', { occasion: outfit.occasion });

  const isPending = outfit.status === 'pending' || outfit.status === 'sent';

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between gap-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/dashboard/outfits">
            <ChevronLeft className="h-4 w-4 mr-1" />
            {t('detail.backToOutfits')}
          </Link>
        </Button>
      </div>

      <div>
        <h1 className="text-2xl font-bold tracking-tight capitalize">{title}</h1>
        <div className="flex items-center gap-2 mt-2">
          <Badge variant="outline" className="capitalize">
            {outfit.occasion}
          </Badge>
          <Badge variant="outline" className="capitalize">
            {outfit.source.replace('_', ' ')}
          </Badge>
          <Badge variant={outfit.status === 'accepted' ? 'default' : 'secondary'}>
            {outfit.status}
          </Badge>
          <span className="text-sm text-muted-foreground">
            {outfit.scheduled_for
              ? formatDistanceToNow(parseISO(outfit.scheduled_for), {
                  addSuffix: true,
                })
              : t('detail.undated')}
          </span>
        </div>

        {/* AI reasoning */}
        {((outfit.name && outfit.reasoning) ||
          (outfit.highlights && outfit.highlights.length > 0)) && (
          <div className="mt-2 space-y-1.5 text-xs flex-1">
            {outfit.name && outfit.reasoning && (
              <p className="font-medium text-foreground break-words">{outfit.reasoning}</p>
            )}
            {outfit.highlights && outfit.highlights.length > 0 && (
              <ul className="space-y-0.5">
                {outfit.highlights.slice(0, 3).map((highlight, index) => (
                  <li key={index} className="flex items-start gap-1.5 text-muted-foreground">
                    <span className="text-primary">•</span>
                    <span>{highlight}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Styling tip */}
        {outfit.style_notes && (
          <div className="mt-2 p-2 bg-muted rounded border text-xs">
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">{t('detail.stylingTip')}</span>{' '}
              {outfit.style_notes}
            </p>
          </div>
        )}
      </div>

      <Card>
        <CardContent className="p-4">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wide">
            {t('detail.items', { count: outfit.items.length })}
          </h2>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-3">
            {outfit.items.map((item) => (
              <Link
                key={item.id}
                href={`/dashboard/wardrobe?item=${item.id}`}
                className="group"
              >
                <div className="relative aspect-square rounded-lg overflow-hidden border bg-muted">
                  {item.thumbnail_url || item.image_url ? (
                    <Image
                      src={(item.thumbnail_url || item.image_url)!}
                      alt={item.name || item.type}
                      fill
                      className="object-cover transition-transform group-hover:scale-105"
                      sizes="(max-width: 640px) 33vw, 20vw"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <span className="text-xs text-muted-foreground">{item.type}</span>
                    </div>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-1 truncate">
                  {item.name || item.type}
                </p>
              </Link>
            ))}
          </div>
        </CardContent>
      </Card>

      {(outfit.feedback?.rating || outfit.feedback?.comment) && (
        <Card>
          <CardContent className="p-4 space-y-1.5">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              {t('detail.feedbackTitle')}
            </h2>
            {outfit.feedback.rating && (
              <div className="flex items-center gap-1 text-sm">
                <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
                {outfit.feedback.rating} / 5
              </div>
            )}
            {outfit.feedback.comment && (
              <p className="text-sm text-muted-foreground break-words">
                {outfit.feedback.comment}
              </p>
            )}
            {outfit.feedback.worn_at && (
              <p className="text-xs text-muted-foreground">
                {t('detail.wornOn', { date: outfit.feedback.worn_at })}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        {isPending && (
          <>
            <Button onClick={() => decide(true)} disabled={acceptMutation.isPending}>
              <ThumbsUp className="h-4 w-4 mr-2" />
              {t('cards.accept')}
            </Button>
            <Button
              variant="outline"
              onClick={() => decide(false)}
              disabled={rejectMutation.isPending}
            >
              <ThumbsDown className="h-4 w-4 mr-2" />
              {t('cards.rejected')}
            </Button>
          </>
        )}
        <Button
          variant="outline"
          className="text-destructive hover:text-destructive"
          onClick={handleDelete}
          disabled={deleteMutation.isPending}
        >
          <Trash2 className="h-4 w-4 mr-2" />
          {tc('delete')}
        </Button>
      </div>
    </div>
  );
}
