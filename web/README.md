# web

The Next.js front end (a Vercel service at `/`). Created in the slice with `create-next-app` after the thin
design system (planning step 4). It reads published data with the anon key from the `api` schema only, and
calls the Python `api` service under `/api/*` for the command bar and admin actions.
