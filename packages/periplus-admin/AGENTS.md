# Periplus Admin

This application owns operator workflows only. Keep catalogue product experiences in
`../periplus-public`. Use shadcn components, TanStack Query for server state, shared types
under `src/types`, named exports, and `toast.error(extractApiError(error))` for mutations.
All HTTP requests use the same-origin `/api` gateway; never embed infrastructure credentials
in browser code. Vite development and nginx production authenticate operators before proxying.
Run `npm run typecheck`, `npm run lint`, `npm run test`, and `npm run build` after changes.
