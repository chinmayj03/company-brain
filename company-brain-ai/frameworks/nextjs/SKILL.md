You are extracting from a **Next.js** codebase. Apply these conventions
when picking entities, edges, and call chains for the brain store.

Next.js has two routing systems. Detect which one the repo uses before
asking about endpoints:

- **App Router** — files under `app/` with `page.tsx`, `layout.tsx`,
  `route.ts`, `loading.tsx`, `error.tsx`, etc. Default for Next 13+.
- **Pages Router** — files under `pages/` and `pages/api/`. Legacy but
  still common.

If both directories exist, App Router takes precedence per Next's own
resolution rules.

## App Router entry points

- `app/<segment>/page.tsx` — a page React component. Default-exported
  function is the entry; props are `{ params, searchParams }`. Capture
  the route as the directory path with dynamic segments expanded
  (`app/foo/[id]/page.tsx` → `/foo/:id`).
- `app/<segment>/layout.tsx` — wraps every nested page; record as
  ANNOTATES (cross-cutting) on the pages it wraps.
- `app/<segment>/route.ts` — HTTP route handler. Named exports
  `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`, `HEAD` are
  entry points. The handler's `Request` parameter and `NextResponse`
  return shape are inputs/outputs.
- `app/<segment>/loading.tsx`, `error.tsx`, `not-found.tsx` — UI
  state components, not data entry points.

## Pages Router entry points

- `pages/<path>.tsx` (or `.jsx`/`.ts`/`.js`) — page component. Default
  export is the entry. Route is the file path minus `pages/` and the
  extension; `[param]` segments map to `:param`.
- `pages/api/<path>.ts` — API handler. Default export
  `(req: NextApiRequest, res: NextApiResponse) => void` is the entry.
- Data hooks: `getStaticProps`, `getServerSideProps`, `getInitialProps`
  are exported alongside the page component; they're separate entry
  points worth capturing as DEPENDS_ON.

## Server vs. client components (App Router)

- Default is server. A component declares itself client by adding
  `"use client"` at the top of the file. Server components can fetch
  directly; client components must call API routes.
- `"use server"` at the top of a file (or before a function) marks it
  as a Server Action. Server Actions are callable from client
  components and act as RPC endpoints — record them as entry points
  even though they're not under `app/.../route.ts`.

## Data-access patterns

- `fetch(url, { cache, next: { revalidate } })` inside a server
  component or route handler — capture the URL and cache hint.
- ORM clients are imported and called directly in server contexts:
  - **Prisma**: `prisma.user.findUnique(...)`, `prisma.user.create(...)`.
  - **Drizzle**: `db.select().from(users).where(...)`.
  - **Mongoose / Mongo driver**: `User.findOne(...)`, `db.collection(...)`.
- `cookies()`, `headers()`, `redirect()`, `notFound()` from
  `next/headers` and `next/navigation` are framework helpers, not
  data-access.

## Middleware & config

- `middleware.ts` at the project root (or under `src/`) runs before
  matched routes. The exported `config.matcher` defines which paths.
  Treat as ANNOTATES on the matched routes.
- `next.config.js` / `next.config.mjs` — build / runtime config; not
  business logic.

## Common false positives

- `Image` from `next/image`, `Link` from `next/link`, `Script` from
  `next/script` — framework components; not edges to chase.
- `useRouter`, `usePathname`, `useSearchParams`, `useState`, `useEffect`
  — React / Next hooks; ignore as edges unless the user asks about
  client-side flow.
- `metadata` / `generateMetadata` exports on pages — SEO config, not
  business logic. Skip the methods.
- `revalidatePath`, `revalidateTag` — cache invalidation primitives;
  capture only when the question is about cache flow.

## Components to skip

- Pure presentation components in `components/ui/` (shadcn-style) —
  Card, Button, Dialog, etc. Skip unless they contain handler logic.
- `*.styles.ts`, `*.css.ts`, `*.module.css` — styling files.

## When in doubt

- The mental model: page or route handler → server-side function or
  ORM call → response. Server Components blur the line; treat the
  component itself as the data-access call site.
- Prefer one batched `extract_methods_from_class` per file over many
  per-method calls.
