// One rule matters here: `no-undef`.
//
// `jobHasFailed` was used in App.jsx and never imported. Vite built it without
// complaint, every test passed, and the Pull List rendered fine on a library
// with nothing in it -- because `[].filter(predicate)` never calls the
// predicate. On a library with three failed downloads it threw on first render
// and the screen went blank. A build that succeeds while a page cannot mount
// is the gap this closes.
import globals from "globals";

export default [
  {
    files: ["src/**/*.{js,jsx}", "tests/**/*.mjs", "scripts/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node },
    },
    linterOptions: { reportUnusedDisableDirectives: true },
    rules: {
      // The whole point. Everything else stays off so this is a hard gate
      // rather than a style opinion nobody runs.
      "no-undef": "error",
      "no-unused-vars": ["warn", {
        args: "none", varsIgnorePattern: "^_", ignoreRestSiblings: true,
      }],
    },
  },
];
