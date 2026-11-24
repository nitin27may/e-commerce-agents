import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Toaster } from "sonner";
import { AuthProvider } from "@/lib/auth-context";
import { PreviewBanner } from "@/components/preview-banner";
import { PREVIEW } from "@/lib/preview/config";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const title = "E-Commerce Agents";
const description = "Multi-agent e-commerce platform powered by Microsoft Agent Framework";

export const metadata: Metadata = {
  // Absolute base for the Open Graph image (app/opengraph-image.tsx). Set NEXT_PUBLIC_SITE_URL where the
  // app is hosted; the localhost default only matters for a build nobody links to.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title,
  description,
  openGraph: { title, description, type: "website", siteName: title },
  twitter: { card: "summary_large_image", title, description },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} h-full antialiased`}
      data-preview={PREVIEW ? "" : undefined}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col font-sans">
        {/* Apply the persisted/system theme before paint to avoid a flash. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme:dark)').matches)){document.documentElement.classList.add('dark')}}catch(e){}`,
          }}
        />
        <PreviewBanner />
        <AuthProvider>{children}</AuthProvider>
        <Toaster
          position="bottom-right"
          richColors
          closeButton
          toastOptions={{ duration: 4000 }}
        />
      </body>
    </html>
  );
}
