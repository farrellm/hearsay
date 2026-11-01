import '@fontsource-variable/literata/opsz.css'
import '@fontsource-variable/literata/opsz-italic.css'
import '@fontsource-variable/atkinson-hyperlegible-next/wght.css'
import './styles/index.css'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './app/App'

const root = document.getElementById('root')
if (!root) throw new Error('#root element missing')

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1 } },
})

// The service worker keeps the app shell available offline.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  navigator.serviceWorker
    .register('/sw.js')
    .catch((e: unknown) => console.warn('service worker registration failed', e))
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
