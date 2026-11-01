import './Sheet.css'

import type { ReactNode } from 'react'

import { useDialog } from '@/hooks/useDialog'

export interface SheetProps {
  title: string
  onClose: () => void
  children: ReactNode
}

/** Modal bottom sheet (docs/ui.md §3): Escape or the scrim closes it, focus returns to the
 * opener. */
export function Sheet({ title, onClose, children }: SheetProps) {
  const ref = useDialog(onClose)

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden />
      <div
        ref={ref}
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="sheet-inner">
          <div className="sheet-head">
            <h2>{title}</h2>
            <button type="button" className="btn quiet" onClick={onClose}>
              Done
            </button>
          </div>
          {children}
        </div>
      </div>
    </>
  )
}
