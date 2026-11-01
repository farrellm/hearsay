import './library.css'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useState } from 'react'

import { api, type FeedPreview, type Policy } from '@/api/client'
import { queries } from '@/api/queries'
import { Sheet } from '@/components/Sheet'
import { useToast } from '@/components/toast'
import { estimate } from '@/lib/estimate'
import { formatRough } from '@/lib/format'

const DEFAULT_BACKFILL = 10

interface Props {
  /** A link typed elsewhere (the empty Library), looked up as the sheet opens. */
  initialUrl?: string
  onClose: () => void
  onOpenShow: (id: number) => void
}

/** Add a show (docs/ui.md §4.2): paste a link, see the show, choose what to transcribe. */
export function AddShowSheet({ initialUrl = '', onClose, onOpenShow }: Props) {
  const [url, setUrl] = useState(initialUrl)
  const preview = useMutation({ mutationFn: api.previewFeed })
  const lookUpInitial = useEffectEvent(() => {
    if (initialUrl) preview.mutate(initialUrl)
  })
  useEffect(() => lookUpInitial(), [])

  return (
    <Sheet title="Add a show" onClose={onClose}>
      <form
        className="add-show-link"
        onSubmit={(e) => {
          e.preventDefault()
          if (url.trim()) preview.mutate(url.trim())
        }}
      >
        <label htmlFor="feed-url">RSS link</label>
        <div className="field-row">
          <input
            id="feed-url"
            className="text-field"
            type="url"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            placeholder="https://…"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              preview.reset()
            }}
          />
          <button className="btn" type="submit" disabled={!url.trim() || preview.isPending}>
            {preview.isPending ? 'Looking…' : 'Look up'}
          </button>
        </div>
      </form>
      {preview.isError && (
        <p className="form-error" role="alert">
          {preview.error.message}
        </p>
      )}
      {preview.data && (
        <SubscribeForm
          key={preview.data.url}
          preview={preview.data}
          onDone={onClose}
          onOpenShow={onOpenShow}
        />
      )}
    </Sheet>
  )
}

function SubscribeForm({
  preview,
  onDone,
  onOpenShow,
}: {
  preview: FeedPreview
  onDone: () => void
  onOpenShow: (id: number) => void
}) {
  const client = useQueryClient()
  const toast = useToast()
  const count = preview.durations.length
  const [backfill, setBackfill] = useState(Math.min(DEFAULT_BACKFILL, count))
  const [none, setNone] = useState(count === 0)
  const [policy, setPolicy] = useState<Policy>('auto')
  const subscribe = useMutation({
    mutationFn: api.subscribe,
    onSuccess: async (feed) => {
      toast(`Subscribed to ${feed.title ?? 'the show'}`)
      await client.invalidateQueries(queries.feeds())
      onDone()
    },
  })

  const existing = preview.feed_id
  if (existing !== null) {
    return (
      <section className="add-show-step">
        <ShowHeading preview={preview} />
        <p>You already subscribe to this show.</p>
        <button
          className="btn"
          type="button"
          onClick={() => {
            onOpenShow(existing)
            onDone()
          }}
        >
          Open {preview.title}
        </button>
      </section>
    )
  }

  const n = none ? 0 : backfill
  const est = estimate(preview.durations.slice(0, n))

  return (
    <form
      className="add-show-step"
      onSubmit={(e) => {
        e.preventDefault()
        subscribe.mutate({ url: preview.url, policy, backfill_n: n })
      }}
    >
      <ShowHeading preview={preview} />

      <fieldset>
        <legend>Transcribe now</legend>
        <label className="choice">
          <input type="radio" name="backfill" checked={!none} onChange={() => setNone(false)} />
          <span>The newest</span>
          <Stepper
            value={backfill}
            min={1}
            max={Math.max(count, 1)}
            onChange={(v) => {
              setBackfill(v)
              setNone(false)
            }}
          />
          <span>{backfill === 1 ? 'episode' : 'episodes'}</span>
        </label>
        <label className="choice">
          <input type="radio" name="backfill" checked={none} onChange={() => setNone(true)} />
          <span>None</span>
        </label>
        <p className="hint" aria-live="polite">
          {n === 0
            ? 'Nothing is transcribed until a new episode comes out.'
            : `About ${formatRough(est.audioSeconds)} of audio, roughly ${formatRough(est.gpuSeconds)} on the GPU.`}
          {n > 0 && est.unknown > 0 && ` (${est.unknown} of them don't list a length.)`}
        </p>
      </fieldset>

      <fieldset>
        <legend>New episodes</legend>
        <label className="choice">
          <input
            type="radio"
            name="policy"
            checked={policy === 'auto'}
            onChange={() => setPolicy('auto')}
          />
          <span>Transcribe automatically</span>
        </label>
        <label className="choice">
          <input
            type="radio"
            name="policy"
            checked={policy === 'manual'}
            onChange={() => setPolicy('manual')}
          />
          <span>Only when I choose</span>
        </label>
      </fieldset>

      {subscribe.isError && (
        <p className="form-error" role="alert">
          {subscribe.error.message}
        </p>
      )}
      <button className="btn primary" type="submit" disabled={subscribe.isPending}>
        {subscribe.isPending ? 'Subscribing…' : 'Subscribe'}
      </button>
    </form>
  )
}

function ShowHeading({ preview }: { preview: FeedPreview }) {
  const count = preview.durations.length
  return (
    <div className="add-show-heading">
      <h3>{preview.title}</h3>
      <p>
        {preview.author ? `${preview.author}, ` : ''}
        {count} {count === 1 ? 'episode' : 'episodes'}
      </p>
    </div>
  )
}

function Stepper({
  value,
  min,
  max,
  onChange,
}: {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
}) {
  return (
    <span className="stepper">
      <button
        type="button"
        className="btn quiet"
        aria-label="Fewer"
        disabled={value <= min}
        onClick={() => onChange(value - 1)}
      >
        −
      </button>
      <output aria-label="Episodes to transcribe">{value}</output>
      <button
        type="button"
        className="btn quiet"
        aria-label="More"
        disabled={value >= max}
        onClick={() => onChange(value + 1)}
      >
        +
      </button>
    </span>
  )
}
