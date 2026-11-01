import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { queries } from '@/api/queries'

import { type Tab, TABS } from './tabs'

/** The app shell (docs/ui.md §3): header, the current tab's screen, and the tab bar. */
export function App() {
  const [tab, setTab] = useState<Tab>('Library')
  const health = useQuery(queries.health())

  return (
    <div className="app">
      <header className="app-header">
        <h1>{tab}</h1>
      </header>
      {health.isError && (
        <p className="server-down" role="alert">
          Couldn't reach the server. Check that Tailscale is connected.
        </p>
      )}
      <main className="app-main">
        <Screen tab={tab} />
      </main>
      <nav className="tab-bar" aria-label="Tabs">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            aria-current={t === tab ? 'page' : undefined}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
    </div>
  )
}

function Screen({ tab }: { tab: Tab }) {
  switch (tab) {
    case 'Library':
      return (
        <section className="empty">
          <h2>Add your first show</h2>
          <p>Subscribing by RSS link or OPML import comes next.</p>
        </section>
      )
    case 'Search':
      return (
        <section>
          <input
            className="search-box"
            type="search"
            aria-label="Search every transcript"
            placeholder="Search every transcript"
            disabled
          />
          <p className="hint">Search opens once episodes are transcribed.</p>
        </section>
      )
    case 'Books':
      return (
        <section className="empty">
          <h2>No books yet</h2>
          <p>Books mentioned in episodes will be listed here.</p>
        </section>
      )
    case 'Up Next':
      return (
        <section className="empty">
          <h2>Up Next is empty</h2>
          <p>Swipe left on any episode to add it here.</p>
        </section>
      )
  }
}
