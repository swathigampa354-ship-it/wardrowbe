'use client';

import { useState, useEffect, useRef } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import {
  Heart,
  Pencil,
  Trash2,
  X,
  Loader2,
  Calendar,
  Tag,
  Palette,
  Shirt,
  Sparkles,
  RefreshCw,
  RotateCcw,
  RotateCw,
  Eraser,
  Undo2,
  ImagePlus,
  Layers,
  Droplets,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Plus,
  Star,
  ImageIcon,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Progress } from '@/components/ui/progress';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { toast } from 'sonner';
import { useUpdateItem, useDeleteItem, useReanalyzeItem, useReplaceItemImage } from '@/lib/hooks/use-items';
import { CLOTHING_SUBTYPES, Item } from '@/lib/types';
import { useClothingTypes, useClothingColors, useSubtypeLabel } from '@/lib/hooks/use-translated-constants';
import { ColorEyedropper } from '@/components/color-eyedropper';
import { useTranslations } from 'next-intl';

interface ItemDetailDialogProps {
  item: Item | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Images now use signed URLs from backend (item.image_url, item.thumbnail_url)

interface EditForm {
  name: string;
  type: string;
  subtype: string;
  brand: string;
  primary_color: string;
  notes: string;
  favorite: boolean;
  wash_interval: number | undefined;
}

function editFormFromItem(item: Item): EditForm {
  return {
    name: item.name || '',
    type: item.type,
    // Pre-fill a rejected AI type as the subtype so picking the nearest
    // supported type doesn't lose what the model actually saw.
    subtype: item.subtype || (item.type === 'unknown' && item.ai_unrecognized_type) || '',
    brand: item.brand || '',
    primary_color: item.primary_color || '',
    notes: item.notes || '',
    favorite: item.favorite,
    wash_interval: item.wash_interval ?? undefined,
  };
}

export function ItemDetailDialog({ item, open, onOpenChange }: ItemDetailDialogProps) {
  const t = useTranslations('wardrobe.itemDetail');
  const tc = useTranslations('common');
  const tw = useTranslations('wardrobe');
  const router = useRouter();
  const clothingTypes = useClothingTypes();
  const clothingColors = useClothingColors();
  const subtypeLabel = useSubtypeLabel();
  const [isEditing, setIsEditing] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [imageKey, setImageKey] = useState(0);
  const [editForm, setEditForm] = useState<EditForm>({
    name: '',
    type: '',
    subtype: '',
    brand: '',
    primary_color: '',
    notes: '',
    favorite: false,
    wash_interval: undefined,
  });
  const updateItem = useUpdateItem();
  const deleteItem = useDeleteItem();
  const reanalyzeItem = useReanalyzeItem();
  const replaceImage = useReplaceItemImage();
  const replaceImageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (item) {
      setEditForm(editFormFromItem(item));
      setIsEditing(false);
    }
  }, [item?.id]);

  if (!item) return null;

  const handleSave = async () => {
    try {
      await updateItem.mutateAsync({
        id: item.id,
        data: {
          name: editForm.name || undefined,
          type: editForm.type,
          // null (not undefined) so clearing the field actually clears it server-side.
          subtype: editForm.subtype.trim() || null,
          brand: editForm.brand || undefined,
          primary_color: editForm.primary_color || undefined,
          notes: editForm.notes || undefined,
          favorite: editForm.favorite,
          wash_interval: editForm.wash_interval,
        },
      });
      setIsEditing(false);
    } catch (error) {
      console.error('Failed to update item:', error);
    }
  };

  const handleDelete = async () => {
    try {
      await deleteItem.mutateAsync(item.id);
      setShowDeleteConfirm(false);
      onOpenChange(false);
      toast.success(t('actions.deleted'), {
        description: item.name ? t('actions.deletedWithName', { name: item.name }) : t('actions.deletedFallback'),
      });
    } catch (error) {
      console.error('Failed to delete item:', error);
      toast.error(t('actions.deleteError'), {
        description: t('actions.deleteErrorDescription'),
      });
    }
  };

  const handleToggleFavorite = async () => {
    try {
      await updateItem.mutateAsync({
        id: item.id,
        data: { favorite: !item.favorite },
      });
    } catch (error) {
      console.error('Failed to toggle favorite:', error);
    }
  };

  const handleReanalyze = async () => {
    try {
      await reanalyzeItem.mutateAsync(item.id);
      toast.success(tw('ai.reanalyzed') ?? 'Item re-analyzed');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Re-analysis failed');
    }
  };

  const handleReplaceImage = async (file: File) => {
    try {
      await replaceImage.mutateAsync({ itemId: item.id, file });
      setImageKey((k) => k + 1);
      toast.success(t('actions.imageReplaced'));
    } catch (error) {
      console.error('Failed to replace image:', error);
      toast.error(t('actions.imageReplaceError'));
    }
  };

  const isAnalyzing = reanalyzeItem.isPending || item.status === 'processing';

  // Use signed URL from backend for better quality in detail view
  const imageUrl = item.image_url || item.image_path;
  const colorInfo = clothingColors.find((c) => c.value === item.primary_color);
  const typeInfo = clothingTypes.find((type) => type.value === item.type);
  const unrecognizedType = item.type === 'unknown' ? item.ai_unrecognized_type : null;
  const subtypeSuggestions = CLOTHING_SUBTYPES[editForm.type] ?? [];

  // AI-generated tags
  const tags = item.tags || {};
  const hasAiTags = !!(tags.colors?.length || tags.pattern || tags.material ||
                   tags.style?.length || tags.season?.length || tags.formality || tags.fit ||
                   tags.occasion?.length || tags.condition || tags.features?.length);

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col p-0 overflow-hidden [&>button]:hidden">
          {/* Header - sticky */}
          <DialogHeader className="flex flex-row items-center gap-2 space-y-0 p-4 border-b flex-shrink-0">
            {/* Below sm the title keeps its 45% cap and the actions stay in a
                scrollable row, so the close control is always reachable on a
                phone. From sm up the title becomes the flexible item and the
                actions row is sized to its content instead, so every action
                stays visible and the name truncates. Previously the title had
                no cap at all from sm up: because a flex item claims its content
                width before a flex-1 sibling does, a long name squeezed the
                actions row and pushed edit and replace-image out of view. */}
            <DialogTitle className="text-xl min-w-0 truncate max-w-[45%] sm:max-w-none sm:flex-1">
              {item.name || (typeInfo ? typeInfo.label : item.type)}
            </DialogTitle>
            {/* The action row scrolls sideways once it stops fitting, because
                the dialog clips its own overflow: without this the buttons
                push the close control past the right edge on a phone and it
                cannot be reached at all. */}
            <div className="flex-1 min-w-0 overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:flex-none">
              <div className="flex w-max ml-auto items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleToggleFavorite}
                  disabled={updateItem.isPending}
                  title={t('titles.toggleFavorite')}
                >
                  <Heart
                    className={`h-5 w-5 ${
                      item.favorite ? 'fill-red-500 text-red-500' : 'text-muted-foreground'
                    }`}
                  />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    onOpenChange(false);
                    router.push(`/dashboard/suggest?item=${item.id}`);
                  }}
                  disabled={item.status !== 'ready'}
                  title={t('titles.suggestOutfit')}
                >
                  <Sparkles className="h-5 w-5 text-primary" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleReanalyze}
                  disabled={isAnalyzing}
                  title={isAnalyzing ? t('titles.analysisInProgress') : t('titles.reanalyzeWithAI')}
                >
                  <RefreshCw
                    className={`h-5 w-5 ${isAnalyzing ? 'animate-spin text-primary' : ''}`}
                  />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => replaceImageInputRef.current?.click()}
                  disabled={replaceImage.isPending}
                  title={t('titles.replaceImage')}
                >
                  {replaceImage.isPending ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <ImagePlus className="h-5 w-5" />
                  )}
                </Button>
                <input
                  ref={replaceImageInputRef}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      handleReplaceImage(file);
                    }
                    e.target.value = '';
                  }}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    // Re-read the item on entering edit mode: tagging can finish while the
                    // dialog is open (same id, so the effect above doesn't re-run).
                    if (!isEditing) setEditForm(editFormFromItem(item));
                    setIsEditing(!isEditing);
                  }}
                  title={isEditing ? t('actions.cancelEditing') : t('actions.editItem')}
                >
                  {isEditing ? (
                    <X className="h-5 w-5" />
                  ) : (
                    <Pencil className="h-5 w-5" />
                  )}
                </Button>
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onOpenChange(false)}
              className="rounded-full flex-shrink-0"
              title={tc('close')}
            >
              <X className="h-5 w-5" />
            </Button>
          </DialogHeader>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto overscroll-contain p-6 pt-4">
            <div className="grid gap-6 sm:grid-cols-2 [&>*]:min-w-0">
            {/* Single photo: multi-image galleries rode on the same arq
                image-worker as rotation and background removal, so the trial
                has one image per item and no /items/{id}/images endpoints. */}
            <div className="space-y-2">
              <div className="relative aspect-square bg-muted rounded-lg overflow-hidden">
                <Image
                  key={imageKey}
                  src={`${imageUrl}&v=${imageKey}`}
                  alt={item.name || item.type}
                  fill
                  className="object-cover"
                  sizes="(max-width: 640px) 100vw, 50vw"
                />
                {isAnalyzing && (
                  <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-2">
                    <Loader2 className="h-8 w-8 text-white animate-spin" />
                    <span className="text-white text-sm font-medium">{t('view.aiAnalyzing')}</span>
                  </div>
                )}
              </div>
            </div>

            {/* Details */}
            <div className="space-y-4">
              {isEditing ? (
                // Edit form
                <div className="space-y-3">
                  <div className="space-y-2">
                    <Label>{t('name')}</Label>
                    <Input
                      value={editForm.name}
                      onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                      placeholder={t('placeholders.itemName')}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('type')}</Label>
                    {unrecognizedType && (
                      <p className="text-xs text-amber-600 dark:text-amber-500">
                        {t('unrecognizedType', { value: unrecognizedType })}
                      </p>
                    )}
                    <Select
                      value={editForm.type}
                      onValueChange={(v) => setEditForm({ ...editForm, type: v })}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {clothingTypes.map((ct) => (
                          <SelectItem key={ct.value} value={ct.value}>
                            {ct.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="item-subtype">{t('subtype')}</Label>
                    <Input
                      id="item-subtype"
                      list="item-subtype-suggestions"
                      maxLength={50}
                      value={editForm.subtype}
                      onChange={(e) => setEditForm({ ...editForm, subtype: e.target.value })}
                      placeholder={t('placeholders.subtype')}
                    />
                    <datalist id="item-subtype-suggestions">
                      {subtypeSuggestions.map((st) => (
                        <option key={st} value={st}>{subtypeLabel(st)}</option>
                      ))}
                    </datalist>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('brand')}</Label>
                    <Input
                      value={editForm.brand}
                      onChange={(e) => setEditForm({ ...editForm, brand: e.target.value })}
                      placeholder={t('placeholders.brandName')}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('primaryColor')}</Label>
                    <div className="flex gap-2">
                      <Select
                        value={editForm.primary_color}
                        onValueChange={(v) => setEditForm({ ...editForm, primary_color: v })}
                      >
                        <SelectTrigger className="flex-1">
                          <SelectValue placeholder={t('placeholders.selectColor')} />
                        </SelectTrigger>
                        <SelectContent>
                          {clothingColors.map((c) => (
                            <SelectItem key={c.value} value={c.value}>
                              <div className="flex items-center gap-2">
                                <div
                                  className="w-3 h-3 rounded-full border"
                                  style={{ backgroundColor: c.hex }}
                                />
                                {c.name}
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <ColorEyedropper
                        imageUrl={imageUrl}
                        onColorSelect={(color) => setEditForm({ ...editForm, primary_color: color })}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label>{t('notes')}</Label>
                    <Textarea
                      value={editForm.notes}
                      onChange={(e) => setEditForm({ ...editForm, notes: e.target.value })}
                      placeholder={t('placeholders.additionalNotes')}
                      rows={3}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t('washInterval')} ({t('view.wears')})</Label>
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={editForm.wash_interval ?? ''}
                      onChange={(e) => setEditForm({ ...editForm, wash_interval: e.target.value ? parseInt(e.target.value) : undefined })}
                      placeholder={t('placeholders.washIntervalDefault', { count: item.effective_wash_interval })}
                    />
                    <p className="text-xs text-muted-foreground">
                      {t('view.washIntervalHint')}
                    </p>
                  </div>
                  <div className="flex gap-2 pt-2">
                    <Button
                      variant="outline"
                      className="flex-1"
                      onClick={() => setIsEditing(false)}
                    >
                      {tc('cancel')}
                    </Button>
                    <Button
                      className="flex-1"
                      onClick={handleSave}
                      disabled={updateItem.isPending}
                    >
                      {updateItem.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      ) : null}
                      {tc('save')}
                    </Button>
                  </div>
                </div>
              ) : (
                // View mode
                <div className="space-y-4">
                  {/* Basic info */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-sm">
                      <Shirt className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium">{typeInfo ? typeInfo.label : item.type}</span>
                      {item.subtype && (
                        <span className="text-muted-foreground">• {subtypeLabel(item.subtype)}</span>
                      )}
                    </div>
                    {unrecognizedType && (
                      <p className="text-xs text-amber-600 dark:text-amber-500">
                        {t('unrecognizedType', { value: unrecognizedType })}
                      </p>
                    )}
                    {item.brand && (
                      <div className="flex items-center gap-2 text-sm">
                        <Tag className="h-4 w-4 text-muted-foreground" />
                        <span>{item.brand}</span>
                      </div>
                    )}
                    {colorInfo && (
                      <div className="flex items-center gap-2 text-sm">
                        <Palette className="h-4 w-4 text-muted-foreground" />
                        <div
                          className="w-4 h-4 rounded-full border"
                          style={{ backgroundColor: colorInfo.hex }}
                        />
                        <span>{colorInfo.name}</span>
                      </div>
                    )}
                    {item.wear_count > 0 && (
                      <div className="flex items-center gap-2 text-sm">
                        <Calendar className="h-4 w-4 text-muted-foreground" />
                        <span>
                          {t('view.wornCount', { count: item.wear_count })}
                          {item.last_worn_at && (
                            <span className="text-muted-foreground">
                              {' '}{t('view.lastWornDate', { date: new Date(item.last_worn_at).toLocaleDateString() })}
                            </span>
                          )}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* AI Analysis */}
                  {(hasAiTags || item.ai_description) && item.status === 'ready' && (
                    <div className="space-y-2 pt-2 border-t">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Sparkles className="h-4 w-4 text-primary" />
                        {t('view.aiAnalysis')}
                        {item.ai_confidence !== undefined && item.ai_confidence > 0 && (
                          <Badge variant="secondary" className="text-xs">
                            {t('view.complete', { percent: Math.round(item.ai_confidence * 100) })}
                          </Badge>
                        )}
                        {item.tags?.logprobs_confidence != null && (
                          <Badge variant="outline" className="text-xs">
                            {t('view.confident', { percent: Math.round(item.tags.logprobs_confidence * 100) })}
                          </Badge>
                        )}
                      </div>
                      {item.ai_description && (
                        <p className="text-sm text-muted-foreground italic">
                          &ldquo;{item.ai_description}&rdquo;
                        </p>
                      )}
                      {hasAiTags && <div className="flex flex-wrap gap-1.5">
                        {tags.colors?.map((color) => (
                          <Badge key={color} variant="outline" className="text-xs">
                            {color}
                          </Badge>
                        ))}
                        {tags.pattern && (
                          <Badge variant="outline" className="text-xs">
                            {tags.pattern}
                          </Badge>
                        )}
                        {tags.material && (
                          <Badge variant="outline" className="text-xs">
                            {tags.material}
                          </Badge>
                        )}
                        {tags.style?.map((s) => (
                          <Badge key={s} variant="outline" className="text-xs">
                            {s}
                          </Badge>
                        ))}
                        {tags.season?.map((s) => (
                          <Badge key={s} variant="outline" className="text-xs">
                            {s}
                          </Badge>
                        ))}
                        {tags.formality && (
                          <Badge variant="outline" className="text-xs">
                            {tags.formality}
                          </Badge>
                        )}
                        {tags.fit && (
                          <Badge variant="outline" className="text-xs">
                            {tags.fit ? t('view.fitBadge', { fit: tags.fit }) : null}
                          </Badge>
                        )}
                        {tags.occasion?.map((o: string) => (
                          <Badge key={o} variant="outline" className="text-xs">
                            {o}
                          </Badge>
                        ))}
                        {tags.condition && (
                          <Badge variant="outline" className="text-xs">
                            {tags.condition}
                          </Badge>
                        )}
                        {tags.features?.map((f: string) => (
                          <Badge key={f} variant="outline" className="text-xs">
                            {f}
                          </Badge>
                        ))}
                      </div>}
                    </div>
                  )}

                  {/* Notes */}
                  {item.notes && (
                    <div className="space-y-1 pt-2 border-t">
                      <p className="text-sm font-medium">{t('notes')}</p>
                      <p className="text-sm text-muted-foreground">{item.notes}</p>
                    </div>
                  )}

                  {/* Metadata */}
                  <div className="text-xs text-muted-foreground pt-2 border-t">
                    {t('view.addedDate', { date: new Date(item.created_at).toLocaleDateString() })}
                  </div>
                </div>
              )}
            </div>
            </div>

            {/* Delete button - separated from other actions for safety */}
            {!isEditing && (
              <div className="pt-4 border-t mt-4">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive hover:bg-destructive/10"
                  onClick={() => setShowDeleteConfirm(true)}
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  {t('actions.deleteItem')}
                </Button>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('actions.deleteConfirm')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('actions.deleteDescription', { name: item.name || item.type })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteItem.isPending}
            >
              {deleteItem.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              {tc('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
