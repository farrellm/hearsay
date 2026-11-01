import './library.css'

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { FeedSummary } from '@/api/client'
import { queries } from '@/api/queries'
import { Artwork } from '@/components/Artwork'

import { AddShowSheet } from './AddShowSheet'
import { OpmlImport } from './OpmlImport'

/** The Library tab's list of shows, newest episode first (docs/ui.md §4.2). */
export function LibraryScreen({ onOpenShow }: { onOpenShow: (id: number) => void }) {
  const feeds = useQuery(queries.feeds())
  // The sheet opens with a link when one was typed into the empty Library.
  const [adding, setAdding] = useState<{ url: string } | null>(null)

  let body
  if (feeds.isPending) body = <p className="loading">Loading</p>
  else if (feeds.isError)
    body = null // the shell's "Couldn't reach the server" covers it
  else if (feeds.data.length === 0) body = <EmptyLibrary onAdd={(url) => setAdding({ url })} />
  else
    body = <ShowList feeds={feeds.data} onOpen={onOpenShow} onAdd={() => setAdding({ url: '' })} />

  return (
    <>
      {body}
      {adding && (
        <AddShowSheet
          initialUrl={adding.url}
          onClose={() => setAdding(null)}
          onOpenShow={onOpenShow}
        />
      )}
    </>
  )
}

function ShowList({
  feeds,
  onOpen,
  onAdd,
}: {
  feeds: FeedSummary[]
  onOpen: (id: number) => void
  onAdd: () => void
}) {
  return (
    <ul className="rows">
      {feeds.map((f) => (
        <li key={f.id}>
          <button type="button" className="row show-row" onClick={() => onOpen(f.id)}>
            <Artwork sha={f.artwork} size={48} />
            <span className="row-text">
              <span className="row-title">{f.title ?? f.url}</span>
              {f.error ? (
                <span className="row-note alert">Can't update this show</span>
              ) : (
                <span className="row-note">{f.newest_title ?? 'No episodes yet'}</span>
              )}
            </span>
            {f.unplayed > 0 && (
              <span className="row-count" aria-label={`${f.unplayed} unplayed`}>
                {f.unplayed}
              </span>
            )}
          </button>
        </li>
      ))}
      <li>
        <button type="button" className="row add-row" onClick={onAdd}>
          + Add a show
        </button>
      </li>
    </ul>
  )
}

function EmptyLibrary({ onAdd }: { onAdd: (url: string) => void }) {
  const [url, setUrl] = useState('')
  return (
    <section className="empty">
      <h2>Add your first show</h2>
      <form
        className="field-row"
        onSubmit={(e) => {
          e.preventDefault()
          if (url.trim()) onAdd(url.trim())
        }}
      >
        <input
          className="text-field"
          type="url"
          inputMode="url"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          aria-label="RSS link"
          placeholder="Paste an RSS link"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="btn primary" type="submit" disabled={!url.trim()}>
          Add
        </button>
      </form>
      <p>
        or <OpmlImport label="import an OPML file" className="link" /> from another podcast app.
      </p>
    </section>
  )
}
