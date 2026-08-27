import type { Metadata } from "next"
import { Geist } from "next/font/google"

import { cn } from "@/lib/utils"

import "./globals.css"
import "periplus-web-shell/styles.css"

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" })

export const metadata: Metadata = {
  title: {
    default: "Periplus — query the web with SQL",
    template: "%s · Periplus",
  },
  description:
    "Explore durable web observations, immutable content, and links through a portable SQL catalogue.",
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={cn("font-sans", geist.variable)}>
      <body className="min-h-svh antialiased">{children}</body>
    </html>
  )
}
