import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { App } from './App'

function renderApp(fetchImpl: typeof fetch) {
  vi.stubGlobal('fetch', fetchImpl)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

const healthy: typeof fetch = () =>
  Promise.resolve(Response.json({ status: 'ok', version: '0.1.0', schema: 1 }))

describe('App shell', () => {
  it('opens on the Library tab with its empty state', () => {
    renderApp(healthy)
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Add your first show' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Library' })).toHaveAttribute('aria-current', 'page')
  })

  it('switches tabs', async () => {
    renderApp(healthy)
    await userEvent.click(screen.getByRole('button', { name: 'Up Next' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Up Next' })).toBeInTheDocument()
    expect(screen.getByText('Swipe left on any episode to add it here.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Library' })).not.toHaveAttribute('aria-current')
  })

  it('says when the server is unreachable', async () => {
    renderApp(() => Promise.reject(new TypeError('Load failed')))
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't reach the server")
  })

  it('shows no alert when the server is up', async () => {
    const fetchSpy = vi.fn<typeof fetch>(healthy)
    renderApp(fetchSpy)
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/health', { method: 'GET' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
