import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { FeedPreview } from '@/api/client'
import { GRIDLOCK, gridlockDetail } from '@/test/fixtures'
import { apiError, mockApi, sentJson } from '@/test/mockApi'
import { renderApp } from '@/test/render'

const PREVIEW: FeedPreview = {
  url: GRIDLOCK.url,
  title: 'Gridlock',
  author: 'Dana',
  image: null,
  feed_id: null,
  // 12 episodes of an hour each, but one doesn't say how long it is.
  durations: [...Array.from({ length: 11 }, () => 3600), null],
}

describe('Library', () => {
  it('lists shows with their newest episode and unplayed count', async () => {
    mockApi({
      'GET /api/feeds': () => [GRIDLOCK, { ...GRIDLOCK, id: 2, title: 'Broken', error: 'x' }],
    })
    renderApp()
    const row = await screen.findByRole('button', { name: /Gridlock/ })
    expect(row).toHaveTextContent('The battery problem nobody budgets for')
    expect(within(row).getByLabelText('2 unplayed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Broken/ })).toHaveTextContent(
      "Can't update this show",
    )
  })

  it('adds a show from the empty Library', async () => {
    let subscribed = false
    const fetchMock = mockApi({
      'GET /api/feeds': () => (subscribed ? [GRIDLOCK] : []),
      'POST /api/feeds/preview': () => PREVIEW,
      'POST /api/feeds': () => {
        subscribed = true
        return gridlockDetail().feed
      },
    })
    renderApp()
    await userEvent.type(await screen.findByLabelText('RSS link'), GRIDLOCK.url)
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))

    const sheet = await screen.findByRole('dialog', { name: 'Add a show' })
    expect(await within(sheet).findByRole('heading', { name: 'Gridlock' })).toBeInTheDocument()
    expect(sheet).toHaveTextContent('Dana, 12 episodes')
    expect(sheet).toHaveTextContent('About 10 h of audio, roughly 50 min on the GPU.')

    // Fewer episodes, and only the ones I pick from now on.
    await userEvent.click(within(sheet).getByRole('button', { name: 'Fewer' }))
    expect(within(sheet).getByLabelText('Episodes to transcribe')).toHaveTextContent('9')
    expect(sheet).toHaveTextContent('About 9 h of audio, roughly 45 min on the GPU.')
    await userEvent.click(within(sheet).getByLabelText('Only when I choose'))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Subscribe' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Subscribed to Gridlock')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /Gridlock/ })).toBeInTheDocument()
    expect(sentJson(fetchMock, 'POST /api/feeds')).toEqual({
      url: GRIDLOCK.url,
      policy: 'manual',
      backfill_n: 9,
    })
  })

  it('says what is wrong with a link', async () => {
    mockApi({
      'POST /api/feeds/preview': () =>
        apiError(
          422,
          "That link isn't an RSS feed. Check that it's the show's feed, not its web page.",
        ),
    })
    renderApp()
    await userEvent.type(await screen.findByLabelText('RSS link'), 'https://pods.example/')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    const sheet = await screen.findByRole('dialog', { name: 'Add a show' })
    expect(await within(sheet).findByRole('alert')).toHaveTextContent("isn't an RSS feed")
    expect(within(sheet).queryByRole('button', { name: 'Subscribe' })).not.toBeInTheDocument()
  })

  it('offers None, which transcribes nothing yet', async () => {
    const fetchMock = mockApi({
      'GET /api/feeds': () => [GRIDLOCK],
      'POST /api/feeds/preview': () => ({ ...PREVIEW, url: 'https://b.example/f' }),
      'POST /api/feeds': () => gridlockDetail().feed,
    })
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: '+ Add a show' }))
    const sheet = screen.getByRole('dialog', { name: 'Add a show' })
    await userEvent.type(within(sheet).getByLabelText('RSS link'), 'https://b.example/f')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Look up' }))
    await userEvent.click(await within(sheet).findByLabelText('None'))
    expect(sheet).toHaveTextContent('Nothing is transcribed until a new episode comes out.')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Subscribe' }))
    await screen.findByRole('status')
    expect(sentJson(fetchMock, 'POST /api/feeds')).toMatchObject({ backfill_n: 0, policy: 'auto' })
  })

  it("opens a show's episodes and goes back", async () => {
    mockApi({
      'GET /api/feeds': () => [GRIDLOCK],
      'GET /api/feeds/1': () => gridlockDetail(),
    })
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: /Gridlock/ }))
    expect(await screen.findByRole('heading', { name: 'Gridlock' })).toBeInTheDocument()
    const [newest, played] = screen.getAllByRole('listitem') as [HTMLElement, HTMLElement]
    expect(newest).toHaveTextContent('The battery problem nobody budgets forOct 28, 1 h 12 min')
    expect(within(newest).getByRole('img', { name: 'Queued' })).toBeInTheDocument()
    expect(within(played).getByRole('img', { name: 'Downloaded' })).toBeInTheDocument()
    expect(played).toHaveClass('played')
    // An automatic show: nothing to pick.
    expect(screen.queryByRole('button', { name: 'Transcribe' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '‹ Library' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
  })

  it('transcribes a picked episode of a manual show', async () => {
    const fetchMock = mockApi({
      'GET /api/feeds': () => [{ ...GRIDLOCK, policy: 'manual' }],
      'GET /api/feeds/1': () => gridlockDetail({ policy: 'manual' }),
      'POST /api/episodes/1/process': () => new Response(null, { status: 204 }),
    })
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: /Gridlock/ }))
    // Only one: the others are queued or downloaded already.
    await userEvent.click(await screen.findByRole('button', { name: 'Transcribe' }))
    expect(sentJson(fetchMock, 'POST /api/episodes/1/process')).toEqual({ now: false })
  })

  it('shows a feed error and unsubscribes after confirming', async () => {
    let gone = false
    mockApi({
      'GET /api/feeds': () => (gone ? [] : [GRIDLOCK]),
      'GET /api/feeds/1': () =>
        gridlockDetail({ error: 'pods.example answered HTTP 404 for the feed.' }),
      'DELETE /api/feeds/1': () => {
        gone = true
        return new Response(null, { status: 204 })
      },
    })
    renderApp()
    await userEvent.click(await screen.findByRole('button', { name: /Gridlock/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 404')
    await userEvent.click(screen.getByRole('button', { name: 'Show settings' }))
    const sheet = screen.getByRole('dialog', { name: 'Show settings' })
    await userEvent.click(within(sheet).getByRole('button', { name: 'Unsubscribe' }))
    expect(sheet).toHaveTextContent('Unsubscribe from Gridlock and delete its episodes?')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Unsubscribe' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Unsubscribed from Gridlock')
    expect(await screen.findByRole('heading', { name: 'Add your first show' })).toBeInTheDocument()
  })

  it('imports an OPML file from Settings', async () => {
    const fetchMock = mockApi({
      'POST /api/feeds/import-opml': () => ({ added: 12, skipped: 1 }),
    })
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('link', { name: 'Export' })).toHaveAttribute(
      'href',
      '/api/feeds/export-opml',
    )
    const file = new File(['<opml/>'], 'podcasts.opml', { type: 'text/x-opml' })
    await userEvent.upload(screen.getByLabelText('OPML file'), file)
    expect(await screen.findByRole('status')).toHaveTextContent('Imported 12 shows, 1 already here')
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/feeds/import-opml')
    expect(call?.[1]?.body).toBe(file)
  })
})
