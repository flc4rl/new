import { useEffect, useState } from 'react'
import { Languages } from 'lucide-react'
import { api } from '../api'
import type { GoogleLookup } from '../../shared/types'

/** Google Translate's dictionary entries for one word. Clicking a sense makes it the word's meaning. */
export function GoogleDictionary({ arabic, onPick }: { arabic: string; onPick: (meaning: string) => void }) {
  const [result, setResult] = useState<GoogleLookup | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    api
      .googleLookup(arabic)
      .then((r) => !cancelled && setResult(r))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
    return () => {
      cancelled = true
    }
  }, [arabic])

  return (
    <div className="gdict" aria-label="Google dictionary" aria-live="polite">
      <div className="gdict-head">
        <Languages size={14} />
        <span>Google dictionary</span>
        {result?.romanization && (
          <span className="muted small" title="Google spells out the letters; it is not the Khartoum pronunciation">
            spelling: {result.romanization}
          </span>
        )}
      </div>
      {error ? (
        <p className="muted small">{error}</p>
      ) : !result ? (
        <p className="muted small">Looking up…</p>
      ) : result.dictionary.length === 0 ? (
        <p className="small">
          No dictionary entry. Translation:{' '}
          {result.translation ? (
            <button type="button" className="gdict-term" onClick={() => onPick(result.translation)}>
              {result.translation}
            </button>
          ) : (
            '(none)'
          )}
        </p>
      ) : (
        <dl className="gdict-list">
          {result.dictionary.map((entry) => (
            <div key={entry.pos + entry.terms.join()}>
              <dt>{entry.pos || 'meaning'}</dt>
              <dd>
                {entry.terms.map((term) => (
                  <button key={term} type="button" className="gdict-term" title="Use as the meaning" onClick={() => onPick(term)}>
                    {term}
                  </button>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
