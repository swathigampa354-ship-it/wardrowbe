import Image from 'next/image';
import Link from 'next/link';
import { getTranslations } from 'next-intl/server';

/**
 * Landing page for the trial. There is no sign-up and no login: the API serves
 * a single implicit demo user, so the only useful call to action is "open the
 * wardrobe". Set `DEMO_PASSWORD` on the server if you want to gate access.
 */
export default async function Home() {
  const t = await getTranslations('nav');
  const d = await getTranslations('dashboard');

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <Image src="/logo.svg" alt={t('brandAlt')} width={80} height={80} priority />
      <h1 className="text-4xl font-bold tracking-tight">{t('brandName')}</h1>
      <p className="max-w-md text-muted-foreground">{d('subtitle')}</p>
      <Link
        href="/dashboard"
        className="inline-flex h-10 items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        {d('openWardrobe')}
      </Link>
    </main>
  );
}
