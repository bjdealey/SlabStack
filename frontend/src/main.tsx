import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, HashRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Everything is local: refetching on every window focus is noise, and a
      // short stale time is plenty for a single-user SQLite database.
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: 1,
    },
  },
})

// GitHub Pages has no server to rewrite unknown paths to index.html, so the
// demo routes in the hash. Locally the API serves the SPA fallback and real
// paths work, including on a hard refresh.
const Router = import.meta.env.VITE_DEMO === 'true' ? HashRouter : BrowserRouter

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Router>
        <App />
        <Toaster theme="dark" position="bottom-right" richColors closeButton />
      </Router>
    </QueryClientProvider>
  </StrictMode>,
)
