'use client';

import { useLocale } from 'next-intl';

/**
 * The full product shipped eight locales and let the visitor switch between
 * them. This trial only carries English strings (see `messages/`), so there is
 * nothing to switch to and the control is a no-op that renders nothing.
 *
 * Put a locale back by adding `messages/en/<ns>.json` siblings and restoring
 * the cookie + profile write that used to live here.
 */
export function LocaleSwitcher() {
  useLocale();
  return null;
}
