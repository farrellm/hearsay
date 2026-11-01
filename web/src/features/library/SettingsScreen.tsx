import './library.css'

import { OPML_EXPORT_URL } from '@/api/client'

import { OpmlImport } from './OpmlImport'

/** Settings, reached from the Library header. Shows in and out as OPML for now. */
export function SettingsScreen() {
  return (
    <section className="settings">
      <h2>Shows</h2>
      <div className="settings-row">
        <div>
          <p>Import OPML</p>
          <p className="row-note">
            Subscribe to every show in a file from another podcast app. New episodes are transcribed
            automatically; older ones only when you choose.
          </p>
        </div>
        <OpmlImport label="Import" className="btn" />
      </div>
      <div className="settings-row">
        <div>
          <p>Export OPML</p>
          <p className="row-note">Every show you subscribe to, for another app.</p>
        </div>
        <a className="btn" href={OPML_EXPORT_URL} download="hearsay.opml">
          Export
        </a>
      </div>
    </section>
  )
}
