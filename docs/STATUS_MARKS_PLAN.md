# Status marks: one vocabulary for the web and Flipparr View

Owner's note (2026-10-11): a check on a cover means *read* on the web and
*downloaded* in the app. Evaluate every status mark across both hosts and
plan what to change so the patterns make sense on each and agree wherever
they mean the same thing.

## The rule this plan adopts

**One meaning, one mark; one mark, one meaning, across both hosts.** A host
may leave out a mark it has no use for (the web has no downloads, the app
shows no ownership), but it never reuses that mark for something else.
Where a host needs a mark of its own, it takes one neither host already
uses.

Colour follows the same rule:
- **Green** is done or complete: read, fully owned, success.
- **Violet** is under way or active: reading, downloading, scanning, the
  selected thing.
- **Amber** is waiting or needs a look: upcoming, partly owned, offline,
  pending.
- **Red** is broken or failed.
- **Muted** is a plain fact: a type, a format.

## What the inventory found (2026-10-11, 22 conflicts)

The full list is the session's inventory, in the form `file:line`. The
conflicts fall into five groups.

1. **The check on a cover has two meanings.**
   - Web: a green check in a 24 px circle in the bottom-right corner means
     *read* (`.issue-tile-mark.done`, `drawers/frame.jsx`). A dark circled
     check in the top-right means *finished* in profile history
     (`.profile-history-done`).
   - App: the same 24 px check, in the same corner on a dark circle, means
     *downloaded* (`CoverStatus`). On the issue cards in a drawer, the two
     are drawn by neighbouring lines of the same component.
   - This one is mine: I took Kindle's mark without checking the web.
2. **Reading state is drawn six ways, in two words.**
   - On issue tiles: a centred green "Read" pill and a violet "In progress"
     pill.
   - On run cards: a centred dark "✓ Read" or "In progress".
   - On drawer cards: a green corner check.
   - Under the cover: two different progress hairlines.
   - In drawer heroes: a green or violet badge.
   - In the app only: a corner sash and a percentage tag.
   - The word is "Read" in most places and "Finished" in others. In the app,
     the Downloaded shelf and the Downloads page describe the same comic in
     different words.
   - The app's issue **list** still shows the web's pills while its **grid**
     shows the sash, so one screen uses two vocabularies depending on the
     view toggle.
3. **The same pill means two things.**
   - "In volume" (ownership) and "In progress" (reading) are the same violet
     pill, in the same spot. The reader also reuses that class for its
     "Reading" page.
   - "Single + volume" (ownership) and "Read" are the same green pill.
   - On a tile, an ownership pill hides the reading pill, so an issue owned
     in a volume never shows that it's read.
   - "Missing", "Date needed" and "Missing" for an issue that's already on
     the way all look alike, and the last should say "On the way".
   - "Upcoming" is amber on an issue but green as "Waiting for release" on
     the Pull List.
   - "Following" and "Read" are the same green chip, side by side, in a run's
     header.
   - "X of Y read" counts issues and volumes in a run, issues in an arc, and
     runs in a collection, with no unit named.
4. **Icons do double duty.**
   - Check: in a menu, the leading check means *chosen* in a select but is
     the *Mark as read* action in an issue's menu.
   - `FollowingIcon` is a check-circle drawn for "On Pull List" and "In
     library", while Following itself is the eye.
   - Arrows: the app's "Download" menu item uses the download arrow, which
     on the web means *export*. The app's own download controls use the
     cloud.
   - The collection mark sits bottom-right on the web and bottom-left in the
     app.
   - Partly owned shows an amber *check*-circle.
5. **Dead code and small slips.**
   - These states can never be reached:
     - the green "downloaded" checks on issue rows and on a drawer's
       Download button;
     - `issueMark`;
     - `issueBadge` ("Downloading N%" in the reading pill's violet);
     - the tile download button.
   - `StarRating`'s comment says averages are drawn in outline; they aren't.
   - A run's average shows under the heading "Your rating".

Some differences need no change, each for its reason:
- **The cloud** is acquisition on the web (Pull, Replace) and a download to
  the device in the app. Both mean "get it here", in places the other host
  never shows, and the app's is Apple's own convention.
- **Trash** removes the device copy in the app and deletes on the web; each
  menu item says which.
- **The sidebar count** is labelled per item: waiting on the admin on the
  web, downloading in the app.
- **The bookmark and eye toggles** share an "on" look. They're the same kind
  of control and labelled.

## The plan

### Phase 1: covers (both hosts, one batch)

**Downloaded gets its own mark (app).**
- An arrow-down in a filled circle, bottom-right. This is Apple's
  `arrow.down.circle.fill`, as Music and Podcasts mark a downloaded item.
  Phosphor's `ArrowCircleDown` (fill) draws it.
- The check is left for *read*.
- A run, collection or arc keeps its meaning: some of it is on the device.
- While downloading, the dimmed cover with a ring stays as it is.

**Reading state is one vocabulary on every cover, on both hosts.**
- Top-right corner: a "Read" sash once finished, a "NN%" tag while under
  way (`ReadState`).
- This replaces, on the web:
  - the centred "Read" and "In progress" pills on tiles;
  - the centred dark mark on run, arc and collection cards;
  - the green corner check on drawer cards;
  - the profile history's check;
  - both progress hairlines.
- The centre of a tile is then free for ownership alone, so "In volume" and
  "Read" can both show.

**Collection mark** goes bottom-left on both hosts.

### Phase 2: pills, rows and words (mostly web)

- **Rows** (issue list view, both hosts) say reading state as words in the
  same tones as the cover: green "Read", violet "NN%". The app's list stops
  using the old pills' words.
- **Ownership pills go muted, as plain facts.** That covers "In volume" and
  "Single + volume". Upcoming stays amber, and Waiting for release moves to
  amber to match.
- "Missing" for an issue already on its way says "On the way".
- The reader's "Reading" page label gets its own class.
- **Following** becomes a muted chip with the eye, so it no longer reads as
  "done" beside Read.
- **One word: "Read".** "Finished" goes, except as the Finish drawer's verb
  ("You finished…").
- **"X of Y read" names its unit**: "1 of 3 issues read", "2 of 5 runs read".
- A run's average rating gets its own label.

### Phase 3: icons

- **Mark as read** in menus takes a check-circle outline, the action. The
  bare check stays the select's "chosen".
- **App menu "Download"** takes the cloud, matching its own buttons. The
  download arrow is left to the web's export.
- **`FollowingIcon`** is renamed `PulledIcon`, for what it shows.
- **Partly owned** takes the amber clock, not an amber check. The clock is
  already "total not known".

### Phase 4: write it down and hold it

- **`DESIGN_SYSTEM.md` gets a "Status marks" table**: each meaning, its
  mark, position, colour, and which hosts draw it. The fix-everywhere rule
  then has one source.
- **A test holds what can drift**:
  - `CoverStatus` never draws a bare check.
  - Only `ReadState` draws in a cover's top-right corner.
  - No reading tone is used for a download state.
- **Delete the unreachable states** listed above.

## Verification

- The web's visual capture **will** change in Phases 1–3, by design. Each
  phase gets a reviewed before and after:
  - `visual:capture` / `visual:compare` at both viewports;
  - `layout:check`;
  - `a11y:audit`, because spoken labels change with the marks.
- A fresh baseline is accepted only after the owner has seen the diffs.
- The app: simulator screenshots of covers in each state, then the owner's
  iPad and iPhone.
- Unit tests for the words ("1 of 3 issues read") and for the cover status
  helpers.

## Decisions (owner, 2026-10-11)

1. **Downloaded mark**: the arrow-down circle. The check means *read*,
   everywhere.
2. **Reading on covers**: the corner sash and percentage on both hosts.
3. **Colour roles**: ownership pills and Following muted, Upcoming always
   amber, green left for done.
