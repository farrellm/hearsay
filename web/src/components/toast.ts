import { createContext, use } from 'react'

export const ToastContext = createContext<(message: string) => void>(() => undefined)

/** Show a toast named after what just happened: "Subscribed to Gridlock". */
export function useToast(): (message: string) => void {
  return use(ToastContext)
}
