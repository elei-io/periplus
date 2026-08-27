export interface DocSection {
  title: string
  paragraphs: readonly string[]
  bullets?: readonly string[]
  code?: string
}

export interface DocEntry {
  title: string
  description: string
  sections: readonly DocSection[]
}

export const docsNavigation = [
  { href: "/docs", label: "Overview" },
  { href: "/docs/getting-started", label: "Getting started" },
  { href: "/docs/sql", label: "SQL catalogue" },
  { href: "/docs/observations", label: "Observations" },
  { href: "/docs/content", label: "Content and links" },
] as const

const docs: Record<string, DocEntry> = {
  "": {
    title: "Periplus documentation",
    description:
      "Learn how Periplus turns web observations and immutable content into a portable SQL catalogue.",
    sections: [
      {
        title: "Start with evidence",
        paragraphs: [
          "Periplus preserves what was observed, when it was observed, and which immutable content was retained. Interpretation begins in your own SQL, notebook, agent, or application.",
        ],
        bullets: [
          "Explore URL observations in web.observation.",
          "Join observations to immutable objects in content.object.",
          "Inspect HTML structure and observed link occurrences.",
        ],
      },
    ],
  },
  "getting-started": {
    title: "Getting started",
    description: "Open the terminal and run a bounded query against public evidence.",
    sections: [
      {
        title: "Your first query",
        paragraphs: [
          "The public terminal accepts read-only DuckDB SQL over the web and content schemas. Begin with a small result and add filters before expanding the scope.",
        ],
        code: "SELECT * FROM web.observation ORDER BY observed_at DESC LIMIT 10;",
      },
    ],
  },
  sql: {
    title: "SQL catalogue",
    description: "The complete public contract is deliberately small and portable.",
    sections: [
      {
        title: "Public relations",
        paragraphs: [
          "Queries use ordinary DuckDB SQL. Only the public web and content schemas are exposed by the hosted terminal.",
        ],
        bullets: [
          "web.observation",
          "web.link_occurrence",
          "content.object",
          "content.html_element",
        ],
      },
    ],
  },
  observations: {
    title: "Observations",
    description: "One row records one terminal observation of one requested URL.",
    sections: [
      {
        title: "History, not a mutable page",
        paragraphs: [
          "web.observation contains requested and effective URLs, observation time, outcome, HTTP status, and an optional immutable content identity. It does not silently choose a latest page state.",
        ],
      },
    ],
  },
  content: {
    title: "Content and links",
    description: "Immutable bytes and deterministic structures are reusable across observations.",
    sections: [
      {
        title: "Content-addressed evidence",
        paragraphs: [
          "content.object identifies retained byte sequences by SHA-256. Identical bytes observed at different URLs or times share the same content identity.",
          "HTML structure is exposed through content.html_element, while link resolution remains observation-contextual in web.link_occurrence.",
        ],
      },
    ],
  },
}

export function getDocEntry(slug: readonly string[] | undefined) {
  return docs[(slug ?? []).join("/")]
}
