import './library.css'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type EpisodeSummary, type Feed, type Policy } from '@/api/client'
import { queries } from '@/api/queries'
import { Artwork } from '@/components/Artwork'
import { Sheet } from '@/components/Sheet'
import { useToast } from '@/components/toast'
import { formatDate, formatDuration } from '@/lib/format'

/** A show's page: its episodes, newest first (docs/ui.md §4.2). */
export function ShowScreen({ id, onGone }: { id: number; onGone: () => void }) {
  const detail = useQuery(queries.feed(id))
  const client = useQueryClient()
  const [settings, setSettings] = useState(false)
  const refresh = useMutation({
    mutationFn: () => api.refreshFeed(id),
    onSettled: () => client.invalidateQueries(queries.feeds()),
  })

  if (detail.isPending) return <p className="loading">Loading</p>
  if (detail.isError) return <p className="form-error">{detail.error.message}</p>
  const { feed, episodes } = detail.data

  return (
    <>
      <section className="show-head">
        <Artwork sha={feed.artwork} size={96} />
        <div>
          <h2>{feed.title ?? feed.url}</h2>
          {feed.author && <p className="row-note">{feed.author}</p>}
          <p className="row-note">
            {feed.policy === 'auto'
              ? 'New episodes are transcribed automatically'
              : 'Episodes are transcribed when you choose'}
          </p>
        </div>
      </section>
      <div className="show-actions">
        <button
          type="button"
          className="btn"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? 'Checking…' : 'Check for episodes'}
        </button>
        <button type="button" className="btn" onClick={() => setSettings(true)}>
          Show settings
        </button>
      </div>
      {feed.error && (
        <p className="feed-error" role="alert">
          Couldn't update this show: {feed.error}
        </p>
      )}
      {episodes.length === 0 ? (
        <p className="hint">This show has no episodes yet.</p>
      ) : (
        <ul className="rows">
          {episodes.map((e) => (
            <EpisodeRow key={e.id} episode={e} feedId={id} manual={feed.policy === 'manual'} />
          ))}
        </ul>
      )}
      {settings && (
        <ShowSettings feed={feed} onClose={() => setSettings(false)} onUnsubscribed={onGone} />
      )}
    </>
  )
}

function EpisodeRow({
  episode,
  feedId,
  manual,
}: {
  episode: EpisodeSummary
  feedId: number
  manual: boolean
}) {
  const client = useQueryClient()
  const transcribe = useMutation({
    mutationFn: () => api.process(episode.id),
    onSuccess: () => client.invalidateQueries(queries.feed(feedId)),
  })
  const meta = [
    episode.published_at && formatDate(episode.published_at),
    episode.duration_s !== null && formatDuration(episode.duration_s),
  ].filter(Boolean)
  const failed = episode.task === 'error'

  return (
    <li className={episode.finished ? 'row episode-row played' : 'row episode-row'}>
      <span className="row-text">
        <span className="row-title episode-title">{episode.title ?? 'Untitled episode'}</span>
        <span className="row-note">{meta.join(', ')}</span>
        {failed && episode.error && <span className="row-note alert wrap">{episode.error}</span>}
      </span>
      {failed ? (
        <button
          type="button"
          className="btn small"
          disabled={transcribe.isPending}
          onClick={() => transcribe.mutate()}
        >
          Try again
        </button>
      ) : manual && episode.task === null && episode.state === 'discovered' ? (
        <button
          type="button"
          className="btn small"
          disabled={transcribe.isPending}
          onClick={() => transcribe.mutate()}
        >
          Transcribe
        </button>
      ) : (
        <StateMark episode={episode} />
      )}
    </li>
  )
}

/** One mark for where an episode is (docs/ui.md §4.2): nothing, hollow (downloaded), filled
 * (transcribed), or a ring (being worked on). A failure is the row's note and Try again. */
function StateMark({ episode }: { episode: EpisodeSummary }) {
  const { state, task } = episode
  let kind: string | null = null
  let label = ''
  if (['transcribed', 'indexed', 'extracted'].includes(state))
    [kind, label] = ['done', 'Transcribed']
  else if (task === 'running') [kind, label] = ['working', 'Downloading']
  else if (task === 'pending') [kind, label] = ['working', 'Queued']
  else if (state !== 'discovered') [kind, label] = ['downloaded', 'Downloaded']
  if (kind === null) return null
  return <span className={`state-mark ${kind}`} role="img" aria-label={label} title={label} />
}

function ShowSettings({
  feed,
  onClose,
  onUnsubscribed,
}: {
  feed: Feed
  onClose: () => void
  onUnsubscribed: () => void
}) {
  const client = useQueryClient()
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)
  const update = useMutation({
    mutationFn: (policy: Policy) => api.updateFeed(feed.id, { policy }),
    onSuccess: () => client.invalidateQueries(queries.feeds()),
  })
  const unsubscribe = useMutation({
    mutationFn: () => api.unsubscribe(feed.id),
    onSuccess: async () => {
      toast(`Unsubscribed from ${feed.title ?? 'the show'}`)
      client.removeQueries(queries.feed(feed.id))
      await client.invalidateQueries(queries.feeds())
      onUnsubscribed()
    },
  })
  const policy = update.variables ?? feed.policy

  return (
    <Sheet title="Show settings" onClose={onClose}>
      <fieldset>
        <legend>New episodes</legend>
        <label className="choice">
          <input
            type="radio"
            name="show-policy"
            checked={policy === 'auto'}
            onChange={() => update.mutate('auto')}
          />
          <span>Transcribe automatically</span>
        </label>
        <label className="choice">
          <input
            type="radio"
            name="show-policy"
            checked={policy === 'manual'}
            onChange={() => update.mutate('manual')}
          />
          <span>Only when I choose</span>
        </label>
      </fieldset>
      <div className="danger-zone">
        {confirming ? (
          <>
            <p>Unsubscribe from {feed.title ?? 'this show'} and delete its episodes?</p>
            <div className="show-actions">
              <button
                type="button"
                className="btn danger"
                disabled={unsubscribe.isPending}
                onClick={() => unsubscribe.mutate()}
              >
                Unsubscribe
              </button>
              <button type="button" className="btn quiet" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <button type="button" className="btn danger" onClick={() => setConfirming(true)}>
            Unsubscribe
          </button>
        )}
      </div>
    </Sheet>
  )
}
