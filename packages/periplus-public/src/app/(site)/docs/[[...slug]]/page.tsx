import { notFound } from "next/navigation"
import type { Metadata } from "next"

import { PageIntro } from "@/components/site/page-intro"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { getDocEntry } from "@/lib/docs"

export const metadata: Metadata = {
  title: "Documentation",
}

export default async function DocsPage({
  params,
}: {
  params: Promise<{ slug?: string[] }>
}) {
  const { slug } = await params
  const entry = getDocEntry(slug)

  if (!entry) {
    notFound()
  }

  return (
    <div className="space-y-8">
      <PageIntro title={entry.title} description={entry.description} />
      <div className="space-y-5">
        {entry.sections.map((section) => (
          <Card key={section.title}>
            <CardHeader>
              <CardTitle>{section.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm/relaxed text-muted-foreground">
              {section.paragraphs.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
              {section.bullets ? (
                <ul className="list-disc space-y-1 pl-5">
                  {section.bullets.map((bullet) => (
                    <li key={bullet}>{bullet}</li>
                  ))}
                </ul>
              ) : null}
              {section.code ? (
                <pre className="overflow-x-auto rounded-md bg-muted p-4 text-xs/relaxed text-foreground">
                  <code>{section.code}</code>
                </pre>
              ) : null}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
