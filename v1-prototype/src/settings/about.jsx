// Settings -> About, shared by the web app and Flipparr View: the licence,
// the credits each source asks for, and what leaves the server. NOTICE and
// docs/PRIVACY.md say the same for whoever reads the repository.
import { useEffect, useState } from "react";
import { apiRequest, lastAnswer } from "../api.js";
import { SettingsCard } from "../components/SettingsControls.jsx";

// Which build the app is: the server's, from /healthz, so a phone that has
// held on to an old page can be told apart from a bug in the new one.
export function useBuild() {
  const [build, setBuild] = useState(() => lastAnswer("/healthz")?.build || "");
  useEffect(() => {
    let live = true;
    apiRequest("/healthz").then((data) => { if (live && data?.build) setBuild(data.build); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return build;
}

const DATA_CREDITS = [
  { name: "Grand Comics Database", href: "https://www.comics.org/",
    text: "Series, issue and creator data from the Grand Comics Database\u2122, used under the Creative Commons Attribution-ShareAlike 4.0 licence.",
    licence: { label: "CC BY-SA 4.0", href: "https://creativecommons.org/licenses/by-sa/4.0/" } },
  { name: "Metron", href: "https://metron.cloud/",
    text: "Issue details, release dates, age ratings and story arcs, through Metron\u2019s API with your account." },
  { name: "Comic Vine", href: "https://comicvine.gamespot.com/",
    text: "Issue and volume details through Comic Vine\u2019s API with your key, for the non-commercial use its terms allow." },
  { name: "Open Library", href: "https://openlibrary.org/",
    text: "Book details and covers for collected editions, from the Internet Archive\u2019s Open Library." },
  { name: "Google Books", href: "https://books.google.com/",
    text: "Book details for collected editions, when you add a Google Books API key. Powered by Google." },
  { name: "Community reading lists", href: "https://github.com/DieselTech/CBL-ReadingLists",
    text: "Story-arc reading lists their authors share on GitHub, fetched when you pick one. Flipparr ships none of them." },
];

const SOFTWARE_CREDITS = [
  { name: "React", licences: [["MIT", "/licenses/React-MIT.txt"]] },
  { name: "Phosphor Icons", licences: [["MIT", "/licenses/Phosphor-Icons-MIT.txt"]] },
  { name: "Heroicons", licences: [["MIT", "/licenses/Heroicons-MIT.txt"]] },
  { name: "Material Design Icons", licences: [["Pictogrammers Free License", "/licenses/MaterialDesignIcons-Pictogrammers-License.txt"], ["Apache 2.0", "/licenses/Apache-2.0.txt"]] },
  { name: "Inter", licences: [["SIL Open Font License 1.1", "/licenses/Inter-OFL-1.1.txt"]] },
];

export const PRIVACY_SECTIONS = [
  { title: "Stays on this server", points: [
    "Your library, its catalogue, and each profile\u2019s reading history, ratings and requests.",
    "Passwords, stored only as scrypt hashes, and API keys, which are never sent back to a browser.",
    "No analytics, no telemetry, nothing sent to Flipparr\u2019s authors. Fonts and icons are part of the app; it loads no outside scripts.",
  ] },
  { title: "Goes to the services you use", points: [
    "Metadata sources get titles, issue numbers and ISBNs from your files as searches: the Grand Comics Database and Open Library without an account, Metron, Comic Vine and Google Books with yours.",
    "Prowlarr, your download clients and a direct-download site you enter get the issues you want.",
    "A vision model, only if you add a key: the pages you read in panel view, sent to Anthropic or OpenAI under your account. Every page only with \u201cAsk about every page\u201d on; readers\u2019 pages only with \u201cReaders\u2019 pages too\u201d on; covers for age ratings only when that is on.",
    "GitHub, for community reading lists, only when they are turned on.",
  ] },
  { title: "In your browser", points: [
    "A sign-in cookie, and on a shared device a device cookie for its profiles. No tracking cookies.",
    "Some cover previews load straight from a metadata source. Flipparr sends no referrer with them, or with any link out.",
  ] },
  { title: "In the server log", points: [
    "Each request\u2019s page, result and profile. The address it came from only with \u201cRecord visitor addresses\u201d on, under Security.",
  ] },
];
export function AboutSettings({ base = "", privacy = PRIVACY_SECTIONS, children = null }) {
  // A local address (a licence file) is the server's: in Flipparr View it
  // would otherwise replace the app's own page.
  const href = (path) => (path.startsWith("/") ? base + path : path);
  const build = useBuild();
  return <>
    {children}
    <SettingsCard title="Flipparr">
      <p className="settings-card-lead">Free software under the <a href={href("/licenses/gpl-3.0.txt")} target="_blank" rel="noopener noreferrer">GNU General Public License, version 3</a>. It ships no comics, no catalogue data and no download sources: every service it uses is one whoever runs it sets up.{build ? ` Build ${build}.` : ""}</p>
    </SettingsCard>
    <SettingsCard title="Where the information comes from">
      <ul className="about-list">
        {DATA_CREDITS.map((item) => <li key={item.name}>
          <a href={item.href} target="_blank" rel="noopener noreferrer"><strong>{item.name}</strong></a>
          <span>{item.text}{item.licence ? <> <a href={item.licence.href} target="_blank" rel="noopener noreferrer">{item.licence.label}</a></> : null}</span>
        </li>)}
      </ul>
      <p className="settings-card-note">Flipparr is independent: none of these services makes, endorses or supports it.</p>
    </SettingsCard>
    <SettingsCard title="Software">
      <ul className="about-list">
        {SOFTWARE_CREDITS.map((item) => <li key={item.name}>
          <strong>{item.name}</strong>
          <span>{item.licences.map(([label, link], index) => <span key={link}>{index ? " or " : ""}<a href={href(link)} target="_blank" rel="noopener noreferrer">{label}</a></span>)}</span>
        </li>)}
      </ul>
      <p className="settings-card-note">The server&rsquo;s own components (Python, Pillow, ONNX Runtime, libarchive) are listed with their licences in the NOTICE file that comes with Flipparr.</p>
    </SettingsCard>
    <SettingsCard title="Privacy">
      {privacy.map((section) => <section className="about-privacy" key={section.title}>
        <h4>{section.title}</h4>
        <ul className="about-list">{section.points.map((point) => <li key={point}><span>{point}</span></li>)}</ul>
      </section>)}
    </SettingsCard>
  </>;
}
