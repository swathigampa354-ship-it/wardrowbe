'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { Sidebar } from '@/components/sidebar';
import { MobileSidebar } from '@/components/mobile-sidebar';
import { MobileNav } from '@/components/mobile-nav';
import { Header } from '@/components/header';
import { OfflineIndicator } from '@/components/offline-indicator';
import { ImageLightbox } from '@/components/image-lightbox';
import { LightboxProvider } from '@/lib/lightbox-context';
import { useAuth } from '@/lib/hooks/use-auth';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const t = useTranslations('dashboard');

  const { user, isAuthenticated, isLoading, error } = useAuth();

  useEffect(() => {
    // Trial build: the API serves a single implicit demo user, so there is no
    // login gate here. If you set REQUIRE_AUTH=true on the server, point this
    // at /login again (the dev-credentials NextAuth flow still exists).
  }, [isLoading, isAuthenticated, router]);

  // Check onboarding status from API user
  useEffect(() => {
    if (user && user.onboarding_completed === false) {
      router.push('/onboarding');
    }
  }, [user, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">{t('layout.loading')}</p>
        </div>
      </div>
    );
  }

  return (
    <LightboxProvider>
      <div className="min-h-screen bg-background">
        <Sidebar />
        <MobileSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <div className="lg:pl-72">
          <Header onMenuClick={() => setSidebarOpen(true)} />
          <main className="py-6 px-4 sm:px-6 lg:px-8 pb-20 lg:pb-6 overflow-x-hidden">
            {children}
          </main>
        </div>
        <MobileNav />
        <OfflineIndicator />
        <ImageLightbox />
      </div>
    </LightboxProvider>
  );
}
