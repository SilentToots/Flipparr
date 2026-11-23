# Flipparr View on the App Store

The listing, App Review notes and the answers App Store Connect asks for,
drafted 2026-11-23 for Flipparr View 1.0 (bundle `com.silenttoots.flipparr.view`,
iOS and iPadOS 15+). Limits are Apple's (App Store Connect Help: app
information, platform version information, screenshot specifications; App
Review Guidelines), read the same day; `tools/` has nothing that checks them,
so the counts below were measured when this was written. **Every answer marked
*owner's call* is a judgement, not a fact, and is Brandon's to make.**

## Product page

**Name** (30 max) — 13:

    Flipparr View

**Subtitle** (30 max) — 27:

    Comics from your own server

**Promotional text** (170 max; changes without a new version) — 153:

    Read the comics on your own Flipparr server: panel by panel on iPhone, whole pages on iPad, and offline wherever you go. No account with us, no tracking.

**Keywords** (100 bytes max, comma-separated, no spaces after commas) — 88:

    comic,comics,reader,manga,cbz,cbr,self-hosted,library,offline,panel,graphic novel,server

No other app's name ("plex", "kavita"): Apple rejects keywords that name
other apps or companies (Guideline 2.3.7). The app's own name is indexed
without repeating it here.

**Description** (4,000 max, plain text) — 1,777:

    Flipparr View is the reading app for Flipparr, the comic library server you run at home. Point it at your Flipparr and your whole collection is on your iPhone and iPad -- runs, issues, collections and story arcs, with your place kept in every one.

    READ THE WAY THE COMIC WAS DRAWN
    • Panel view steps through a page one panel at a time, fitted to your screen, and shows the whole page when you want it.
    • Whole pages and two-page spreads fill the iPad, in portrait or landscape.
    • Pinch, pan and swipe the way you expect.

    TAKE IT WITH YOU
    • Download an issue, the next unread issues of a run, or a whole story arc. Downloads finish even with the device locked.
    • Read offline on a plane or the subway. Your place, ratings and read marks wait on the device and reach your server when you are back.
    • Set how much space downloads may use; comics you have read make room for new ones.

    ONE LIBRARY FOR THE HOUSEHOLD
    • Everyone in the house gets their own profile, with their own place, ratings and reading list.
    • Profiles can be limited by age rating, and shared devices can ask for a PIN.
    • Pick up on the iPad where you left off on the iPhone.

    YOUR COMICS, YOUR SERVER
    Flipparr View talks only to the Flipparr server you connect it to. There is no account with us, no analytics and no tracking, and nothing is sent to the app's developer. It does not sell, find or download comics from the internet: it reads the library on your own server.

    Flipparr View needs a Flipparr server (free and open source, installed with Docker on a NAS or home server) with your own comics in it. Setup: https://github.com/SilentToots/Flipparr

    The comics shown in the screenshots are Pepper&Carrot by David Revoy (www.peppercarrot.com), used under the Creative Commons Attribution 4.0 licence.

**What's New** (version 1.0) — leave empty for the first release.

**Category** — primary **Books**, secondary **Entertainment**. (*Owner's
call*; comic readers sit in Books on the store.)

**URLs**

| Field | Value |
|---|---|
| Support URL (required) | `https://github.com/SilentToots/Flipparr/issues` -- public, issues on. |
| Privacy Policy URL (required) | `https://github.com/SilentToots/Flipparr/blob/v0.3.0/docs/PRIVACY.md` -- the release's own copy. **Not** `main`: the default branch was last updated 2026-10-13 and lacks 27 lines of the privacy document, including what Flipparr View keeps on a device. (*Owner's call:* bring `main` up to date and link it instead.) |
| Marketing URL (optional) | `https://github.com/SilentToots/Flipparr` |

**Copyright** — `2026 SilentToots` (*owner's call* on the name shown).

## Screenshots

In `app-store/` beside the repository (kept out of git: they contain the
comic's art). Taken in the simulator from the build of f251034 against a
local copy of the demo server's library; status bar at 9:41, full battery.
PNG, RGB, no transparency.

**iPhone 6.9" (1320 × 2868)** -- App Store Connect scales these for the
smaller iPhones:

1. `01-home.png` -- Comics, Recommended: Keep Reading at 25 %, Downloaded, the continue bar.
2. `02-panel-view.png` -- panel view, panel 2 of 3, the rest of the page dimmed.
3. `03-run.png` -- a run: rating, issues, Begin, download.
4. `04-download.png` -- "Download Pepper&Carrot": the next 4 unread, 11 MB, finishes with the iPhone locked.
5. `05-downloads.png` -- Downloads: 10 MB of a 2 GB limit, the run's 5 issues on the device.

**iPad 13" (2064 × 2752)** -- required because the app runs on iPad:

1. `01-home.png` -- sidebar, Keep Reading, Recently Added.
2. `02-run.png` -- the run as a side panel over the library.
3. `03-reader.png` -- a whole page, edge to edge.

Suggested captions if overlaid later: "Your library, on every device" /
"Panel by panel" / "Every run, in order" / "Download for the road" /
"Read offline".

## App Review information

**Sign-in required:** yes.

- **Username:** `reviewer`
- **Password:** in `/volume1/docker/flipparr-demo/demo-credentials.txt` on
  Senpai (`reviewer_password`). Paste it into App Store Connect only; never
  into the repository.

**Notes** (paste, then add the password above):

    Flipparr View is the client for Flipparr, a self-hosted comic library server that the user runs on their own NAS or home server (open source: https://github.com/SilentToots/Flipparr). The app does not include, sell or source any content; it reads the user's own library from their own server.

    To review, on the first screen enter the server address:
    https://flipparr-demo.silenttoots.com
    then sign in with the username "reviewer" and the password below.

    The demo server's library is five episodes of Pepper&Carrot by David Revoy, used under Creative Commons Attribution 4.0 (credited on the last page of each episode). It runs the current release of the server, separate from any personal library.

    Things to try: open the run "Pepper&Carrot", read an episode (tap the right side to step through panels; the grid button shows all pages), go back and use the download button on the run to download episodes, then turn on Airplane Mode and read them from the Downloads tab.

    On guideline 4.2.3: the app needs the user's own Flipparr server, the way a Plex or Jellyfin client needs its server; the demo above stands in for one. On guideline 5.2.3: downloads are copies of comics from the user's own server to the user's own device, for reading offline, as described in the app.

    Password: [paste from the demo credentials]

**Contact:** the owner's name, phone and email (*owner's*).

## App Privacy ("nutrition label")

**Recommended answer: Data Not Collected** -- *owner's call*.

Apple counts data as *collected* when it leaves the device in a way that lets
the developer or its partners keep it beyond serving the request. Flipparr
View sends data only to the Flipparr server the user connects it to, which the
user runs and controls (docs/PRIVACY.md, "On an iPad or iPhone with Flipparr
View"): no analytics, no crash reporting to us, no third-party SDKs, nothing
sent to the developer. Apple's page does not name the case of a server the
user operates; this reading follows its definition. The app keeps on the
device only what docs/PRIVACY.md lists (downloaded comics, places waiting to
sync, profiles with a PIN hash, tokens in the Keychain).

## Age rating

The questionnaire describes what the app itself contains. *Owner's call*;
the reasoning:

- **Content categories** (violence, sexual content, mature themes,
  and so on): **None**. The app ships no content. It shows comics from the
  user's own library, as a photo viewer shows the user's photos.
- **Unrestricted Web Access: No.** It connects only to the user's Flipparr
  server; there is no browser.
- **User-Generated Content: No.** Nothing is shared between users of the app;
  a household's profiles share only their own server.
- **Parental Controls: Yes.** Profiles can be limited by age rating
  (server-side) and opened with a PIN.

This gives 4+. If App Review asks for a higher rating because a library may
hold mature comics, 16+ (13+ on earlier iOS versions' scale it maps to) is
the conservative fallback; Apple's page says to ask Developer Support where
the questionnaire does not fit.

## Export compliance

`ITSAppUsesNonExemptEncryption` is `false` in `ios/App/App/Info.plist`: the
app uses only the system's HTTPS. No question per build.

## Known issues to decide on before submitting

Found while taking the screenshots (build of f251034, iPhone 17 Pro Max
simulator):

1. **Home screen shelf under the tabs, intermittently.** On some cold launches
   the first shelf on Comics › Recommended starts about 62 pt higher than it
   should, its heading behind the tab bar, at the top of the page (the
   difference is the iPhone's top safe-area inset). 1 of 3 cold launches in a
   row; not seen on the web at the same width. Evidence:
   `app-store/bug-launch-overlap.png` (launches 1-3 side by side). Worth
   checking on a real iPhone (force-quit and reopen the TestFlight build a
   few times) before submitting: a reviewer's first screen.
2. **"Begin Issue #1" on a run whose #1 is 25 % read.** The run page's main
   button says Begin while the issue's card shows 25 % and Keep Reading has it.
3. **The reader's title is the file name** ("Pepper&Carrot 003 (2014).cbz")
   rather than the issue ("Pepper&Carrot #3 · The Secret Ingredients").
4. **A keyboard focus ring** shows on the reader's close and the run's back
   button after using a hardware keyboard's Return key -- correct for keyboard
   use; it only matters for screenshots.
