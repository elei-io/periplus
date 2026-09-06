"use client"

import { Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { Button } from "@/components/ui/button"

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  return <Button variant="ghost" size="icon" aria-label="Toggle color theme" title="Toggle color theme" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}>
    <Moon className="dark:hidden" aria-hidden="true" />
    <Sun className="hidden dark:block" aria-hidden="true" />
  </Button>
}
