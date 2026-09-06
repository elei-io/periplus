import type { Metadata } from "next"
import { Geist } from "next/font/google"
import { ThemeProvider } from "next-themes"

import { Toaster } from "@/components/ui/sonner"
import { cn } from "@/lib/utils"
import { QueryProvider } from "@/components/providers/query-provider"

import { PublicFooter } from "@/components/public-footer"
import { PublicNav } from "@/components/public-nav"

import "./globals.css"

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" })

export const metadata: Metadata = {
  title: {
    default: "Periplus — the web, as one dataset",
    template: "%s · Periplus",
  },
  description:
    "A different lens on the web: one shared tabular model for pages, HTML structure, text, and links. Explore with a question or SQL, and define what the data means for you.",
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
