import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'

import { api, type Imported } from '@/api/client'
import { queries } from '@/api/queries'
import { useToast } from '@/components/toast'

/** A button that picks an OPML file and subscribes to every show in it. */
export function OpmlImport({ label, className }: { label: string; className: string }) {
  const input = useRef<HTMLInputElement>(null)
  const client = useQueryClient()
  const toast = useToast()
  const upload = useMutation({
    mutationFn: api.importOpml,
    onSuccess: async (result) => {
      toast(importedMessage(result))
      await client.invalidateQueries(queries.feeds())
    },
    onError: (e) => toast(e.message),
  })

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={upload.isPending}
        onClick={() => input.current?.click()}
      >
        {upload.isPending ? 'Importing…' : label}
      </button>
      <input
        ref={input}
        type="file"
        accept=".opml,.xml,text/x-opml,text/xml,application/xml"
        hidden
        aria-label="OPML file"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) upload.mutate(file)
          e.target.value = ''
        }}
      />
    </>
  )
}

function shows(n: number): string {
  return `${n} ${n === 1 ? 'show' : 'shows'}`
}

function importedMessage({ added, skipped }: Imported): string {
  if (added === 0 && skipped === 0) return 'That file has no shows in it.'
  if (added === 0) return `You already subscribe to all ${shows(skipped)}.`
  const already = skipped > 0 ? `, ${skipped} already here` : ''
  return `Imported ${shows(added)}${already}`
}
