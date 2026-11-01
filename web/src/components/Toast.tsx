import { type ReactNode, useCallback, useState } from 'react'

import { useAutoDismiss } from '@/hooks/useAutoDismiss'

import { ToastContext } from './toast'

const TOAST_MS = 4000

/** One toast at a time, above the tab bar, for 4 s (docs/ui.md §5.2). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ message: string; key: number } | null>(null)
  useAutoDismiss(toast, TOAST_MS, () => setToast(null))
  const show = useCallback((message: string) => setToast({ message, key: Date.now() }), [])

  return (
    <ToastContext value={show}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toast && (
          <p className="toast" key={toast.key}>
            {toast.message}
          </p>
        )}
      </div>
    </ToastContext>
  )
}
