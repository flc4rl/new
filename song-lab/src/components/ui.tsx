import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { X } from 'lucide-react'

// ---- buttons ----

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'default' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  icon?: ReactNode
  pressed?: boolean
}

export function Button({ variant = 'default', size = 'md', icon, pressed, className, children, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} btn-${size}${children == null ? ' btn-icon' : ''}${className ? ` ${className}` : ''}`}
      aria-pressed={pressed}
      {...rest}
    >
      {icon}
      {children != null && <span>{children}</span>}
    </button>
  )
}

/** Small keyboard key label. */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

// ---- modal ----

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
  }, [])
  return (
    <dialog
      ref={ref}
      className={`modal${wide ? ' modal-wide' : ''}`}
      onClose={onClose}
      onCancel={onClose}
      onMouseDown={(e) => {
        if (e.target === ref.current) onClose()
      }}
    >
      <div className="modal-body">
        <header className="modal-head">
          <h2>{title}</h2>
          <Button variant="ghost" size="sm" icon={<X size={18} />} aria-label="Close" onClick={onClose} />
        </header>
        {children}
      </div>
    </dialog>
  )
}

export function Confirm({
  title,
  children,
  confirmLabel,
  danger,
  onConfirm,
  onClose,
  extra,
}: {
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
  extra?: ReactNode
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="modal-text">{children}</div>
      <footer className="modal-foot">
        <Button onClick={onClose}>Cancel</Button>
        {extra}
        <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} autoFocus>
          {confirmLabel}
        </Button>
      </footer>
    </Modal>
  )
}

// ---- toasts ----

interface Toast {
  id: number
  kind: 'error' | 'info'
  text: string
}

interface ToastApi {
  error: (err: unknown) => void
  info: (text: string) => void
}

const ToastContext = createContext<ToastApi>({ error: () => {}, info: () => {} })

export const useToast = () => useContext(ToastContext)

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const push = useCallback((kind: Toast['kind'], text: string) => {
    const id = nextId.current++
    setToasts((list) => [...list.filter((t) => t.text !== text), { id, kind, text }])
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), kind === 'error' ? 8000 : 3500)
  }, [])

  const apiRef = useRef<ToastApi>({
    error: (err) => push('error', err instanceof Error ? err.message : String(err)),
    info: (text) => push('info', text),
  })

  return (
    <ToastContext.Provider value={apiRef.current}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <span>{t.text}</span>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setToasts((list) => list.filter((x) => x.id !== t.id))}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

// ---- states ----

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div className="empty-text">{children}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  )
}

export function LoadError({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="empty empty-error" role="alert">
      <h3>That did not load</h3>
      <div className="empty-text">{error}</div>
      <div className="empty-action">
        <Button onClick={onRetry}>Try again</Button>
      </div>
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="spinner" role="status">
      <span className="spinner-dot" />
      {label}
    </div>
  )
}
