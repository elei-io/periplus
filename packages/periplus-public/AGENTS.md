<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# Periplus frontend rules

- Before implementing Next.js behavior, read the relevant documentation bundled
  with the installed Next.js version under `node_modules/next/dist/docs/`. For
  dependency upgrades or ecosystem decisions, verify the current official
  Next.js documentation. Do not rely solely on model memory.
- Prefer React Server Components for server-rendered data. For client-side
  server state, caching, polling, and mutations, use TanStack Query. Do not
  implement server-data fetching or synchronization with custom `useEffect`
  logic.
- Consider extracting reusable stateful client behavior, orchestration, and
  external synchronization into focused, named hooks. Do not create hooks that
  merely hide straightforward component code.
- Build interfaces from shadcn components. Install missing components with
  `npx shadcn@latest add <component>` from `packages/periplus-public/` rather than hand-rolling common
  UI primitives.
- Use shadcn variants and design tokens. Do not add bespoke CSS, arbitrary
  visual styling, or custom UI primitives unless the user specifically requests
  them. Tailwind classes may be used only for structural layout needed to
  compose shadcn components.
