# Sudanese Arabic Song Lab

A local web app that turns songs on YouTube into study material for Sudanese (Khartoum) Arabic. It plays a song slowed down, shows each lyric line in Arabic script with a transliteration and an English gloss underneath, and sends any line or word to a spaced-repetition deck. It also records you shadowing a line, and collects questions for your tutor.

Everything runs on your own computer. Lyrics, recordings and progress stay in the `data` folder next to this file.

## Start the app

You need [Node.js](https://nodejs.org) 22.13 or newer. Check with `node --version`.

The first time, install the app's packages from this folder:

```bash
npm install
```

Then, every time:

```bash
npm start
```

The app opens in your browser at <http://localhost:5177>. If it does not open by itself, go to that address. Press Ctrl+C in the terminal to stop the app.

Always use the `http://localhost` address. Opening the files directly (`file://`) stops YouTube from playing inside the app.

To use another port: `PORT=5180 npm start` (macOS and Linux) or `$env:PORT=5180; npm start` (Windows PowerShell).

## Turn on "Draft with Claude" (optional)

Claude translates the lyrics for you: the transliteration in Khartoum pronunciation, the English, and each word with its meaning. With a key set, it does this by itself as soon as you paste lyrics (switch that off in Settings), and the **Draft with Claude** button redoes it for lines you choose. Without an API key the app works fully; you type translations yourself.

Claude is used rather than Google Translate because Google reads Sudanese as Modern Standard Arabic, gives no transliteration or word breakdown, and needs its own paid Google Cloud key.

1. Create an API key at <https://console.anthropic.com>. Requests are billed to that account.
2. Give the key to the app in one of two ways:
   - Put it in a file called `.env` in this folder, with one line: `ANTHROPIC_API_KEY=sk-ant-...` (the file is ignored by git), or
   - Set it in the terminal before `npm start`: `export ANTHROPIC_API_KEY=sk-ant-...` (macOS and Linux) or `$env:ANTHROPIC_API_KEY="sk-ant-..."` (Windows PowerShell).
3. Restart the app. The terminal says `Claude drafts: on`.

The key is read by the local server only and never reaches the browser. Drafts use `claude-opus-5-5`; to use another model, set `SONGLAB_MODEL` the same way.

What Claude does and does not do:

- It only transliterates and glosses the lines you tick. It never supplies or completes lyrics, and any word it returns that is not in the line is thrown away.
- It fills empty fields only. Text you typed and anything marked verified is never changed.
- Everything it writes is saved as a draft, tagged "Claude draft", until you or your tutor mark it verified. Words and lines it was unsure of are tagged "Claude unsure".

## Studying a song

1. **Library: add a song.** Paste a YouTube link, with a title, the artist and notes.
2. **Paste the lyrics**, one line per row. Lyrics are never fetched from the internet; you type or paste them.
3. **Tap to sync.** Press `S`, start the song with Space, and press Enter on the first syllable of each line. A line ends where the next begins. Undo a tap with Backspace. Fine-tune a line with the start and end buttons (0.25 s steps) or `,` `.` `<` `>`.
4. **Listen slowly.** Pick 0.5× or 0.75× (the app offers only the speeds YouTube allows for the video, and remembers the last one per song). Loop a line with `L`, with a number of repeats and a pause between them.
5. **Break lines down.** Type the transliteration and gloss under each line, or let Claude translate them. Open a line's words to give each word a transliteration, meaning, root, register (everyday, poetic or unknown) and a note.
6. **Hover to see which word is which.** Once a line has words, point at an Arabic word to light up its transliteration and the English words that translate it, with its meaning shown above it. It works the other way round too: point at English or transliterated words to find the Arabic. Claude fills in the links; for words you add yourself, type the matching English under "In the line's English" in the word editor (a word whose meaning appears in the line's English is linked anyway). Click a row to edit it.
7. **Make cards.** The card button next to a line or a word adds it to the deck. Phrases from lessons or the coursebook go in under Review, "Add a phrase".
8. **Review daily.** A song card plays its clip at the song's speed, then shows the Arabic and transliteration, then the meaning when you reveal it. Grade with 1 to 4. New cards per day: 10 by default, changed under Review, "All cards".
9. **Shadow.** Wear headphones, press `R` to record yourself while the line loops, `R` again to stop, and `C` to hear the original line and then your take.
10. **Before a lesson.** The Tutor page lists every draft line, draft word and open question by song. Correct and verify in place with your tutor, or send them the Markdown file or the printable page.

### Keyboard shortcuts on a song

| Key | Action |
| --- | --- |
| Space | Play or pause |
| ↑ ↓ | Previous or next line |
| ← → | Back or forward 3 seconds |
| Enter | Play the line once (in sync mode: tap) |
| L | Loop the line on or off |
| − = | Slower or faster |
| , . | Move the line's start earlier or later |
| < > | Move the line's end earlier or later |
| S | Sync mode on or off |
| R | Record a take on or off |
| C | Original line, then your take |
| Esc | Stop the loop, or leave a text field |

In review: Space reveals, 1 to 4 grade, R replays the clip.

## Transliteration

The transliteration records what the singer pronounces, in plain keyboard characters: long vowels doubled (aa, ii, uu, ee, oo), doubled consonants written twice, the article joined with a hyphen (al-beet, ash-shams), capitals for emphatic consonants and the deep h (S D T Z H), 3 for ع and ' for the glottal stop. ق is usually g in Khartoum speech.

The whole scheme is in `translit.config.json`. Edit it to match your tutor's or your coursebook's convention; Claude drafts follow the file, and Settings shows the scheme in use.

## Your data and backups

- Everything is in the `data` folder: one SQLite database and your recordings in `data/recordings`. The folder is ignored by git, so lyrics and recordings never end up in the repository.
- **Settings, Download backup** saves one JSON file with all songs, lines, words, cards, review history, questions and settings, with your recordings unless you untick them.
- **Settings, Restore from a backup** replaces everything in the app with a backup file.
- Lyrics are copyrighted. Keep backups to yourself and do not commit them.

## When something does not work

- **"This video cannot play inside the app"**: the video's owner has switched off playing on other sites. Use the "Open on YouTube" link, or look for another upload of the song.
- **Only one speed is offered**: YouTube does not allow other speeds for that video.
- **The microphone does not record**: allow microphone access for localhost in the browser (the icon left of the address), then try again.
- **"Port 5177 is already in use"**: the app is probably already running; open <http://localhost:5177>. Or start it on another port (see above).
- **Draft with Claude fails**: the message says why (key not accepted, no connection, too many requests). Check the key in `.env`, then restart the app.

## For developers

- `npm run dev` runs the server with the frontend served from source, reloading on changes.
- `npm test` runs the server and logic tests (Node's test runner, a throwaway data folder, no network).
- `npm run typecheck` checks the TypeScript.
- Layout: `server/` (HTTP API, SQLite storage, Claude drafting), `src/` (React frontend), `shared/` (types, scheduler, text helpers used by both), `test/`.
- Spaced repetition is SM-2 with short learning steps (`shared/srs.ts`).
