import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { mockApi } from '@/test/mockApi'
import { renderApp } from '@/test/render'

describe('App shell', () => {
  it('opens on the Library tab with its empty state', async () => {
    mockApi()
    renderApp()
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Add your first show' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Library' })).toHaveAttribute('aria-current', 'page')
  })

  it('switches tabs', async () => {
    mockApi()
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Up Next' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Up Next' })).toBeInTheDocument()
    expect(screen.getByText('Swipe left on any episode to add it here.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Library' })).not.toHaveAttribute('aria-current')
  })

  it('says when the server is unreachable', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Load failed')))
    renderApp()
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't reach the server")
  })

  it('shows no alert when the server is up', async () => {
    const fetchMock = mockApi()
    renderApp()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/health', { method: 'GET' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
