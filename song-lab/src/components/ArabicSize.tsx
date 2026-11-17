import { useArabicSize } from '../lib/arabicSize'

/** A− and A+ buttons for the size of Arabic text. */
export function ArabicSizeButtons() {
  const { smaller, bigger } = useArabicSize()
  return (
    <span className="ar-size" role="group" aria-label="Arabic text size">
      <button type="button" className="btn btn-sm" disabled={!smaller} onClick={() => smaller?.()} aria-label="Smaller Arabic" title="Smaller Arabic">
        <span lang="ar" className="ar">أ</span>−
      </button>
      <button type="button" className="btn btn-sm" disabled={!bigger} onClick={() => bigger?.()} aria-label="Bigger Arabic" title="Bigger Arabic">
        <span lang="ar" className="ar">أ</span>+
      </button>
    </span>
  )
}
