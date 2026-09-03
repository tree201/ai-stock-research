# AI Stock Research Web

React/TypeScript frontend for the stock research workspace.

## Development

Start the Python API first:

```bash
AI_STOCK_DB=../research.sqlite3 PYTHONPATH=../src python3 -m stock_research --web --port 8000
```

Then run the frontend:

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` requests to the Python service.

## UI stack

- ChatGPT-style workspace built for the `assistant-ui` integration seam;
- Radix ScrollArea for accessible scrolling;
- React Markdown + GFM for research responses;
- TanStack Table and ECharts dependencies reserved for richer financial tables/charts;
- Lucide icons and a compact shadcn-inspired visual system.
