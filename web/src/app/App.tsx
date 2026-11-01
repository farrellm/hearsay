import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { queries } from '@/api/queries'
import { ToastProvider } from '@/components/Toast'
import { LibraryScreen } from '@/features/library/LibraryScreen'
import { SettingsScreen } from '@/features/library/SettingsScreen'
import { ShowScreen } from '@/features/library/ShowScreen'

import { type Tab, TABS } from './tabs'

/** Where the Library tab is: the list of shows, one show, or settings. */
type LibraryView = { kind: 'shows' } | { kind: 'show'; id: number } | { kind: 'settings' }

/** The app shell (docs/ui.md §3): header, the current tab's screen, and the tab bar. */
export function App() {
  const [tab, setTab] = useState<Tab>('Library')
  const [library, setLibrary] = useState<LibraryView>({ kind: 'shows' })
  const health = useQuery(queries.health())
  const toShows = () => setLibrary({ kind: 'shows' })
  const nested = tab === 'Library' && library.kind !== 'shows'

  return (
    <ToastProvider>
      <div className="app">
        <header className="app-header">
          {nested ? (
            <button type="button" className="back" onClick={toShows}>
              ‹ Library
            </button>
          ) : (
            <h1>{tab}</h1>
          )}
          {tab === 'Library' && library.kind === 'shows' && (
            <button
              type="button"
              className="btn quiet"
              onClick={() => setLibrary({ kind: 'settings' })}
            >
              Settings
            </button>
          )}
          {nested && library.kind === 'settings' && <h1 className="nested-title">Settings</h1>}
        </header>
        {health.isError && (
          <p className="server-down" role="alert">
            Couldn't reach the server. Check that Tailscale is connected.
          </p>
        )}
        <main className="app-main">
          {tab === 'Library' ? (
            <Library
              view={library}
              onOpenShow={(id) => setLibrary({ kind: 'show', id })}
              onBack={toShows}
            />
          ) : (
            <Screen tab={tab} />
          )}
        </main>
        <nav className="tab-bar" aria-label="Tabs">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              aria-current={t === tab ? 'page' : undefined}
              onClick={() => {
                // Tapping the tab you're on goes back to its top.
                if (t === 'Library') toShows()
                setTab(t)
              }}
            >
              {t}
            </button>
          ))}
        </nav>
      </div>
    </ToastProvider>
  )
}

function Library({
  view,
  onOpenShow,
  onBack,
}: {
  view: LibraryView
  onOpenShow: (id: number) => void
  onBack: () => void
}) {
  switch (view.kind) {
    case 'shows':
      return <LibraryScreen onOpenShow={onOpenShow} />
    case 'show':
      return <ShowScreen key={view.id} id={view.id} onGone={onBack} />
    case 'settings':
      return <SettingsScreen />
  }
}

function Screen({ tab }: { tab: Exclude<Tab, 'Library'> }) {
  switch (tab) {
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
