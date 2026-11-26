# Frontend (Next.js)

```
cd frontend
npm install
copy .env.local.example .env.local   # Windows (use cp on macOS/Linux); set API_URL if the API is not on :8000
npm run dev                          # http://localhost:3000
```

The browser only talks to this server; `/api/*` is forwarded to the Python API (`API_URL`).
Design tokens (colors, radius) live in `src/app/globals.css`; UI primitives in `src/components/ui`.
