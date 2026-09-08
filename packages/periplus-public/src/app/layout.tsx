import type { Metadata } from "next"
import { Geist } from "next/font/google"
import { ThemeProvider } from "next-themes"

import { Toaster } from "@/components/ui/sonner"
import { cn } from "@/lib/utils"
import { QueryProvider } from "@/components/providers/query-provider"
import { publicOrigin } from "@/lib/seo"

import { PublicFooter } from "@/components/public-footer"
import { PublicNav } from "@/components/public-nav"

import "./globals.css"

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" })

export const metadata: Metadata = {
  metadataBase: publicOrigin ? new URL(publicOrigin) : undefined,
  robots: { index: Boolean(publicOrigin), follow: true },
  title: {
    default: "Periplus — a shared web databank",
    template: "%s · Periplus",
  },
  description:
    "A shared web databank. Explore available sources, query text, links, and HTML with SQL, and define your own datasets.",
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html suppressHydrationWarning lang="en" className={cn("font-sans", geist.variable)}>
      <body className="min-h-svh antialiased">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange><QueryProvider><PublicNav />{children}<PublicFooter /><Toaster /></QueryProvider></ThemeProvider>
      </body>
    </html>
  )
}
