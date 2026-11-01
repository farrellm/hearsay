# Hearsay — UI and UX Design

Status: draft, 2026-11-01. Companion to [design.md](design.md) (§9 Player, §11 Frontend).
This document covers how Hearsay looks, reads and behaves. design.md covers what it does.

## 1. The idea

Most podcast apps treat the episode as a thing you play. Hearsay also treats it as a thing you
can read, search and quote. So the transcript is the main surface of the app, and the player
controls support it. You should be able to glance at the screen while listening and know
where you are, scroll back to reread something, and tap any line to hear it again.

The visual language comes from reading with a highlighter in hand: plain paper, ink, and one
yellow marker that always shows the moment you're hearing.

### Principles

1. **Read first, then control.** The transcript fills the screen. Controls sit at the bottom,
   within reach of a thumb, and stay compact.
2. **Yellow means now, and nothing else.** The marker color is used only for the current
   playback position: the current word, the current segment's gutter bar, and the scrubber
   head. Search matches, selection and focus all use other treatments. If you see yellow, it's
   what you're hearing.
3. **Every quote opens to its moment.** Any text shown with a timestamp (a transcript line, a
   search snippet, a book mention) can be tapped to go to that point in the audio. Timestamps
   are always shown in the same blue, so the affordance looks the same everywhere.
4. **Background work is visible but calm.** Transcription happens on a remote GPU, in batches,
   for money. The app says what's happening and what it costs in plain units, in one place
   (the GPU chip and the Session sheet). It never asks for attention for routine progress.
5. **Machine guesses look like guesses.** Guessed speaker names, unresolved books and
   possibly-offset timestamps are visibly marked, and each mark can be tapped to fix it.

## 2. Tokens

### 2.1 Color

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--paper` | `#F2F3F5` | `#131822` | Page background. A cool white, like copier paper. |
| `--ink` | `#1C2230` | `#DDE2EA` | Body text and primary icons. A blue-black, like ballpoint. |
| `--graphite` | `#5D6575` | `#9AA3B2` | Secondary text: speaker names, dates, durations. |
| `--rule` | `#D5D9E0` | `#2A3140` | Dividers, the scrubber track, list separators. |
| `--marker` | `#F5E15A` | `#E3CC3F` | **Now** only (principle 2). Text on it is always `--ink-on-marker`. |
| `--ink-on-marker` | `#1C2230` | `#131822` | Text on the marker. |
| `--pen` | `#2546B5` | `#8FA8FF` | Timestamps, links, primary buttons, focus rings. |
| `--alert` | `#B3261E` | `#FF8A80` | Errors and budget warnings only. |

- Contrast: `--ink` on `--paper` and `--ink-on-marker` on `--marker` are both above 12:1.
  `--pen` and `--graphite` on `--paper` are above 4.5:1 in both themes.
- The theme follows the system setting. There is no in-app theme switch in v1.
- Ad segments use a diagonal hatch of `--graphite` at 25% on the scrubber and in the transcript
  gutter, not a color, so they don't compete with the marker.
- There are no speaker colors. Speakers are told apart by name, and by the position of turns in
  the text. Six arbitrary hues would add noise and fail for color-blind readers.

### 2.2 Type

Two families, with clearly separate jobs:

- **Literata** (variable) for everything you read: transcript text, episode titles,
  summaries, quotes in search results and book mentions. It was designed for long reading on
  screens, and its tabular lining figures are used for every timestamp.
- **Atkinson Hyperlegible Next** for everything you operate or glance at: tabs, buttons, speaker
  names, metadata, the GPU chip, settings. It is built for legibility at a glance, which suits
  a screen read while walking.

Both are self-hosted (Fontsource packages, `woff2`, Latin and Latin Extended subsets) and
precached by the service worker. Nothing is fetched from a font CDN, so the app works offline
and nothing leaves the tailnet.

Scale (iOS body = 17 px, ratio about 1.2, rounded to whole pixels):

| Step | Size / line height | Family and weight | Use |
| --- | --- | --- | --- |
| `--t-xs` | 13 / 18 | Atkinson 400 | Timestamps in lists, durations, captions |
| `--t-sm` | 15 / 20 | Atkinson 400 and 700 | Metadata, speaker names, buttons, tabs |
| `--t-body` | 17 / 28 | Literata 400 | Transcript text (line height 1.65: serif body, read for long stretches) |
| `--t-lg` | 20 / 26 | Literata 600 | Show titles in lists, section headings |
| `--t-xl` | 24 / 30 | Literata 600 | Episode title on the episode screen |
| `--t-2xl` | 30 / 36 | Literata 500 | Search box text, and empty-state headings |

- The root size follows iOS Dynamic Type through `font: -apple-system-body` on `html`. Every
  size above is set in `rem`, so the whole scale grows with the reader's text-size setting.
- Transcript measure is limited to 34 em, about 65 characters. On a phone that's the full
  width after the gutter. On an iPad or desktop the column stays that wide and centers.
- No all-caps text anywhere. Labels are sentence case.
- Speaker names are Atkinson 700 at `--t-sm` in `--graphite`, not a different color.

### 2.3 Space, shape and depth

- 4 px base grid. Side gutter 16 px. List rows are at least 56 px tall, and every tap target is
  at least 44 × 44 px.
- Radii by role: artwork 6 px, sheets 16 px (top corners only), buttons and chips fully
  rounded. Lists and transcript rows have no radius and no background.
- Depth is used only for things that float above the page: the mini-player and the bottom
  sheets. They get one shadow, `0 -1px 0 var(--rule), 0 -8px 24px rgb(28 34 48 / 0.08)`.
  Nothing on the page itself has a shadow.
- Safe areas: the tab bar and the player controls pad with `env(safe-area-inset-bottom)`, and
  the header with `env(safe-area-inset-top)`.

## 3. Structure

```
┌──────────────────────────────────────┐
│ Header: screen title        GPU chip │  The GPU chip appears on every top-level screen
├──────────────────────────────────────┤
│                                      │
│           Screen content             │
│                                      │
├──────────────────────────────────────┤
│ ▢  Episode title          ▶   ────── │  Mini-player, when something is loaded
├──────────────────────────────────────┤
│  Library   Search   Books   Up Next  │  Tab bar
└──────────────────────────────────────┘
```

- **Library**, **Search**, **Books** and **Up Next** are the four tabs. Settings is reached
  from the Library header.
- The **episode screen** is pushed on top of any tab. When the episode is the one playing, it
  is the player: the same screen with the controls docked at the bottom, not a separate modal.
- The **mini-player** expands into the episode screen with a shared-element transition
  (artwork and title move into place). Swiping down on the episode screen's handle collapses
  it back.
- **Sheets** (Session, Add show, segment actions, speaker rename) rise from the bottom and are
  dismissed by swiping down or tapping outside.

### Vocabulary

The interface uses the listener's words, not the system's. These names are used in every
label, button, toast and notification:

| In the system (design.md) | In the interface |
| --- | --- |
| Feed | Show |
| Episode states / stage pills | Downloaded, Transcribed, Searchable, Scanned |
| Process now | Transcribe now |
| GPU session | Transcription session (short: "session") |
| Backlog | Waiting |
| Compute units | Compute units (shown with a monthly budget, so the number has context) |
| `auto` / `manual` policy | "Transcribe new episodes automatically" / "Only when I choose" |
| Items, entities | Mentions, books (or films, people, links) |
| Up Next queue | Up Next |

## 4. Screens

### 4.1 Episode screen and player

This is the most important screen, and it is where the design spends its boldness.

```
┌──────────────────────────────────────┐
│ ‹ Gridlock                      ⋯    │
│                                      │
│ The battery problem nobody budgets   │  --t-xl Literata
│ for                                  │
│ Oct 28, 1 h 12 min                   │  --graphite
│ ✓ Transcribed  ✓ Searchable  ✓ Scanned│
├──────────────────────────────────────┤
│ 38:02  Dana                          │
│        So the question is whether    │
│        the grid can take it.         │
│                                      │
│ 38:09  Marcus?                       │  "?" + dotted underline: guessed name
│▌       It can't, not without █████   │  ▌ marker gutter bar, █ marker on the word
│▌       storage. That's the part      │
│▌       nobody budgets for.           │
│                                      │
│ 38:21 ╱╱ Ad: Northwind ╱╱╱  Skip ad  │  hatched band, never skipped automatically
│ 39:40  Dana                          │
│        Right, and the storage…       │
├──────────────────────────────────────┤
│ ──────────╱╱───┼──●──────────┼────── │  scrubber: chapter ticks, ad hatch, marker head
│ 38:12                       −34:01   │
│  ↺15      ▶‖      ↻30     1.5×   ☾   │
└──────────────────────────────────────┘
```

**Above the transcript.** The episode title, date and length, and the stage pills. Below
those (scrolled past once you start reading) come the summary, chapters and this episode's
mentions, each collapsed to one line with a count ("4 books mentioned").

**Transcript.**

- Each segment is a row. The left gutter (52 px) holds the timestamp in `--pen`, tabular
  figures. A timestamp is shown at every speaker turn, and at the first segment after each
  whole minute. Every row can be tapped, whether or not it shows a time.
- The speaker's name is shown on its own line at the start of each turn, never repeated within
  a turn. A guessed name ends in "?" and has a dotted underline. Tapping it opens the rename
  sheet.
- **The marker.** The segment being heard gets a 3 px `--marker` bar in the gutter. The word
  being heard gets a `--marker` background with a 2 px horizontal overhang, like a highlighter
  stroke. The marker moves from word to word with a 60 ms background-position transition, so
  it reads as a sweep, not a blink. With reduced motion it jumps.
- Episodes without word timings (feed transcripts) show only the gutter bar.
- **Follow-along.** The current segment is kept about a third of the way down the transcript
  area, scrolling smoothly. When you scroll by hand, following stops and a "Back to now" button
  appears above the controls. Following also stops while text is selected or a sheet is open.
- **Gestures.** Tap a segment: play from its start. Long-press a word: play from that word,
  with a light marker flash on the word to confirm. Long-press a segment's timestamp: the
  segment actions sheet (Copy text, Copy link to this moment, Share).
- Find in transcript is in the `⋯` menu. Matches get a 2 px `--pen` underline and the
  current match a `--pen` outline, so they never look like the marker.

**Controls.** Docked at the bottom. Back 15 s, play/pause, forward 30 s, speed, sleep timer.
Speed is a button showing the current value; tapping it opens a sheet with a slider
(0.8×–3×, in steps of 0.1) and "Use for all episodes of Gridlock". The scrubber shows chapter
boundaries as ticks, ad segments as hatching, and the playhead as a marker-colored dot. Dragging
the scrubber shows a time bubble with the chapter title.

**Episode not yet transcribed.** The transcript area shows where the episode is in the
pipeline and offers the next step:

- "Waiting for the next session. About 4 h of audio is ahead of it." with **Transcribe now**.
- "Transcribing, 63%" with a thin progress line. Live, from SSE.
- "Transcription failed: the audio file couldn't be decoded." with **Try again**.

Playback works in every state.

**Possibly-offset timestamps.** When the audio is streamed and its length doesn't match
(design.md §7.3), a line above the transcript reads "This copy is 38 s longer than the one
that was transcribed, so lines may be off. Download the original again". It's informational,
in `--graphite`, never `--alert`.

### 4.2 Library

```
┌──────────────────────────────────────┐
│ Library               ⚙   3 h waiting │
├──────────────────────────────────────┤
│ ▢  Gridlock                        2 │  unplayed count
│    The battery problem nobody…       │  newest episode
├──────────────────────────────────────┤
│ ▢  Long Division                     │
│    Margins of error, part 2          │
├──────────────────────────────────────┤
│ + Add a show                         │
└──────────────────────────────────────┘
```

- Shows are a list, sorted by newest episode. Each row has 48 px artwork, the title at
  `--t-lg`, and the newest episode title in `--graphite`.
- A show's page lists its episodes. Each row has the title, date and duration, and a single
  state mark at the right: nothing (not downloaded), a hollow circle (downloaded), a filled
  circle (transcribed and searchable), or a progress ring (working on it). Played episodes are
  shown in `--graphite`. Swipe left on a row to add it to Up Next. Swipe right to mark it
  played.
- For a show on "Only when I choose", new episodes have a **Transcribe** button on the row.
- **Empty library.** "Add your first show" in `--t-2xl`, a field for an RSS link, and "or
  import an OPML file from another podcast app."

**Add a show** (a sheet). This is a sequence:

1. Paste an RSS link. Hearsay fetches it and shows the artwork, title and episode count.
2. Choose what to transcribe now: "The newest [10] episodes", with a stepper, or "None".
   Below it, the estimate: "About 9 h of audio, roughly 40 min on the GPU and 1.2 compute
   units."
3. Choose new episodes: "Transcribe automatically" (default) or "Only when I choose".
4. **Subscribe**. The toast says "Subscribed to Gridlock".

Errors say what to do: "That link isn't an RSS feed. Check that it ends in the feed, not the
show's web page."

### 4.3 Search

```
┌──────────────────────────────────────┐
│ ┌──────────────────────────────────┐ │
│ │ grid storage                     │ │  --t-2xl Literata
│ └──────────────────────────────────┘ │
│ ( Both | Exact | Meaning )  Filters  │
├──────────────────────────────────────┤
│ ▢ Gridlock                           │
│   The battery problem nobody…  Oct 28│
│   38:09  Marcus                      │
│   "…not without storage. That's the  │  match underlined in --pen
│   part nobody budgets for."          │
│   52:40  Dana                        │
│   "…grid-scale storage gets cheap…"  │
├──────────────────────────────────────┤
│ ▢ Long Division                      │
│   …                                  │
└──────────────────────────────────────┘
```

- The search box is large and in Literata, because what you type is often a half-remembered
  quote. It's focused when you open the tab.
- Results are grouped by episode, with up to 3 moments each. A moment is its timestamp, the
  speaker, and the snippet in Literata, in quotation marks. Keyword matches are underlined in
  `--pen`. Meaning-only hits have no underline, because no words matched exactly.
- Tapping a moment opens the episode screen with that segment centered and a "Play from 38:09"
  button on it. If something else is playing, it keeps playing until you press that button, so
  browsing results never interrupts what you're listening to. If nothing is playing, the
  episode is loaded there, paused.
- Filters (a sheet): show, date range, speaker, "mentions a book". Active filters appear as
  removable chips below the mode toggle.
- Recent searches are listed under the empty box.
- **No results.** "Nothing matched 'grid storag'." If the mode is Exact: "Try Meaning to
  find related passages." If filters are on: "Remove filters" as a button.

### 4.4 Books (and Media, People)

A segmented control at the top switches Books, Media and People.

```
┌──────────────────────────────────────┐
│ ( Books | Media | People )   Recent ▾│
├──────────────────────────────────────┤
│ ▯  The Power Broker                  │  cover 40×60
│    Robert Caro                       │
│    Recommended in 4 episodes         │
├──────────────────────────────────────┤
│ ▯? Thinking in Systems               │  "?" unresolved
│    Recommended in 1 episode          │
└──────────────────────────────────────┘
```

- Sort by most recent mention (default) or most mentioned.
- A book's page shows its cover, title and author, an "Open Library" link, and each mention:
  the show and episode, the timestamp, who mentioned it, and how ("recommends", "mentions",
  "criticizes"), with the quote. Each mention opens its moment, as search results do.
- **Unresolved books** have a "?" on the cover. Their page leads with "Which book is this?" and
  up to 5 Open Library candidates. Picking one merges the mention into that book.
- **Wrong mentions.** Each mention's `⋯` menu has "Not a book mention", which hides it. The
  toast says "Hidden", with Undo.

### 4.5 Up Next

- A plain list. Drag the handle to reorder, swipe left to remove.
- "Clear played" in the header removes finished episodes.
- **Empty.** "Up Next is empty. Swipe left on any episode to add it here."

### 4.6 The GPU chip and the Session sheet

The chip sits at the right of every top-level header. It is a fully rounded pill in
`--t-sm` Atkinson, and shows one of:

| State | Chip | Notes |
| --- | --- | --- |
| Nothing waiting | (hidden) | |
| Waiting | "3 h waiting" | Hours of audio, not episode count |
| Starting | "Starting session" | Includes VM start and model load |
| Transcribing | "Transcribing 2 of 7" | With a thin progress line under the text |
| Paused by budget | "Paused: budget" | In `--alert` |
| Error | "Session failed" | In `--alert` |

Tapping it opens the Session sheet:

```
┌──────────────────────────────────────┐
│ ─────                                │
│ Transcribing                         │
│ Gridlock: The battery problem…  63%  │
│ T4, running for 18 min               │
│                                      │
│ Waiting: 6 episodes, about 5 h       │
│ About 35 min of GPU time, 1 unit     │
│                                      │
│ This month: 14 of 50 compute units   │
│ ━━━━━━━━━━━━━━━━──────────────────── │
│                                      │
│ [ Stop session ]                     │
│ Session settings                     │
└──────────────────────────────────────┘
```

- When idle with work waiting, the primary button is **Transcribe now**, and the sheet says
  when the next automatic session will start: "Starts on its own when 2 h is waiting, or by
  9:40 pm."
- **Over budget.** "This month's budget is used up. Automatic sessions resume on Dec 1."
  Transcribe now still works, after confirming "This will use about 1 unit beyond your
  budget."
- Session settings: the batch threshold, the longest wait, idle grace, the GPU, and the
  monthly budget and reserve, each with a one-line explanation of its effect.

### 4.7 Show settings

Reached from a show's page. One screen, grouped:

- **New episodes:** transcribe automatically or only when I choose.
- **Speakers:** "Tell speakers apart" (diarization, on or off, with "Turn this off for shows
  with one voice"). Default names: a list of "S0 is usually …" entries.
- **Playback:** default speed.
- **Storage:** keep audio for 30 days after playing, or forever, with "Keep forever if you want
  timestamps to stay exact."
- **Notifications:** "Tell me when new episodes are transcribed".
- **Unsubscribe**, in `--alert`, with a choice to keep or delete the transcripts.

## 5. Behavior details

### 5.1 Motion

Motion is reserved for things you caused, plus one ambient effect: the marker.

- The marker sweep (§4.1) and the follow-along scroll are the only motion you didn't trigger.
- User-triggered transitions: the mini-player expanding (300 ms, ease-out), sheets rising
  (250 ms), the "Back to now" button sliding in, and a short marker flash on a long-pressed word.
- No entrance animations on lists, no hover effects beyond a `--rule` background on desktop
  rows, and no skeleton shimmer. Lists that are loading show nothing for the first 300 ms and
  then a plain "Loading" line.
- With `prefers-reduced-motion`, scrolling jumps, the marker jumps between words, and sheets
  and the mini-player appear without movement.

### 5.2 Feedback

- **Toasts** appear above the mini-player for 4 s, one at a time, named after the action:
  "Subscribed to Gridlock", "Added to Up Next", "Hidden" (with Undo), "Link copied".
- **Errors** say what happened and the next step, in the interface's voice, without
  apologizing: "Couldn't reach the server. Check that Tailscale is connected." Errors on a
  stage show the stage, the reason and **Try again**.
- **Notifications** (Web Push): "5 new episodes transcribed: Gridlock, Long Division and 1
  more." Budget: "Hearsay has used 45 of 50 compute units this month."

### 5.3 Accessibility

- Every transcript row is a button with the label "Marcus, 38:09: It can't, not without
  storage…". The current row also has `aria-current="true"`. The transcript doesn't announce
  every change during playback; it is a list you can explore with VoiceOver at any time.
- "Now" is never shown by color alone: the gutter bar's position and `aria-current` carry it
  too.
- Focus rings are 2 px `--pen` with a 2 px offset, shown for keyboard focus (`:focus-visible`).
- All targets are at least 44 px, the scrubber's touch area is 44 px tall over a 4 px track,
  and every control has a text label for VoiceOver ("Skip back 15 seconds").
- Text scales with Dynamic Type (§2.2). The layout is tested at the largest non-accessibility
  size and at AX2.

### 5.4 Other platforms

The design is for an iPhone first. On wider screens (iPad, the desktop browser) the tab bar
becomes a left rail, and the episode screen puts the transcript column in the center with the
summary, chapters and mentions in a right column. The controls stay docked at the bottom.

## 6. Design review notes

These record what was considered and rejected, so later changes don't drift back to them.

- **Court-transcript styling** (Courier, numbered lines, page and line citations) was the
  first direction, because of the name. It was dropped: monospace is tiring to read for an
  hour, and it would turn a pun into the whole identity. What survived is the citation idea:
  the left-margin timestamps.
- **Warm cream paper with a serif and a clay accent** was rejected as the default look of
  generated reading apps. The cool paper and blue-black ink come from copier paper and
  ballpoint, which fit the highlighter idea.
- **A dark-first player with album-art color washes** (the common podcast-app look) was
  rejected because it makes the art the hero, and Hearsay's hero is the text.
- **Per-speaker colors** were rejected (§2.1).
- **Card layouts** for shows, results and books were rejected in favor of plain lists. A
  library is a list, and cards would waste height on a phone.
