export const SUPPORTED_LOCALES = ['en'] as const;

export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

export const DEFAULT_LOCALE: SupportedLocale = 'en';

export const LOCALE_COOKIE = 'NEXT_LOCALE';

export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export const LOCALE_METADATA: Record<SupportedLocale, { name: string; nativeName: string; dir: 'ltr' | 'rtl' }> = {
  en: { name: 'English', nativeName: 'English', dir: 'ltr' },
};

export const NAMESPACES = [
  'common',
  'nav',
  'dashboard',
  'wardrobe',
  'suggest',
  'settings',
  'outfits',
  'constants',
  'errors',
] as const;

export type Namespace = (typeof NAMESPACES)[number];

export function isValidLocale(locale: string | undefined | null): locale is SupportedLocale {
  return !!locale && (SUPPORTED_LOCALES as readonly string[]).includes(locale);
}

/** The trial ships one locale, so every Accept-Language header resolves to it. */
export function resolveLocale(candidate: string | undefined | null): SupportedLocale {
  void candidate;
  return DEFAULT_LOCALE;
}
