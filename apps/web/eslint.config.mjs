import { defineConfig, globalIgnores } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

/**
 * WHAT REPLACED `eslint-config-next`, AND HOW CLOSE IT IS.
 *
 * That preset was the only lint configuration this app had, so swapping it
 * out is a chance to quietly stop enforcing thirty rules. It was therefore
 * not replaced by "a sensible default": its two entry points were dumped
 * (`core-web-vitals` + `typescript`) and the rule maps below are those
 * dumps, minus the parts that describe a framework this app no longer runs
 * on.
 *
 * KEPT, AT THE SAME LEVEL:
 *   - `typescript-eslint` recommended — the same list, not type-checked,
 *     which is what `next/typescript` was. Including its two DOWNGRADES:
 *     `no-unused-vars` and `no-unused-expressions` end at `warn` there, so
 *     they end at `warn` here. Promoting them would be a different repo.
 *   - the 17 `react-hooks` rules at their exact levels. `next/core-web-
 *     vitals` shipped the whole React-Compiler set (`purity`, `refs`,
 *     `immutability`, `set-state-in-effect`, …) at error, not just
 *     `rules-of-hooks`, and `recommended-latest` is the same set.
 *   - the 20 `react` rules and the 6 `jsx-a11y` rules, verbatim.
 *
 * DROPPED, on purpose:
 *   - `@next/next/*` (23 rules). They lint `next/image`, `next/script`,
 *     `pages/`, `_document` — none of which exist here.
 *   - `import/no-anonymous-default-export` (warn). The only rule lost to
 *     dropping a plugin rather than to dropping a framework; no source in
 *     this app has an anonymous default export.
 *
 * ADDED:
 *   - `react-refresh/only-export-components`, at WARN. Vite's Fast Refresh
 *     silently degrades to a full page reload when a module mixes
 *     component and non-component exports, which is the kind of thing that
 *     gets diagnosed as "the dev server feels slow" six weeks later. It
 *     currently reports 19 sites, every one of them a pre-existing shape
 *     (`button.tsx` exporting `buttonVariants` beside `Button` is shadcn's
 *     own layout, and the chart module exports six helpers). They are a
 *     real cost and they are named, but splitting nineteen modules is not
 *     a thing a framework port gets to do quietly, so the rule reports and
 *     does not fail.
 */

/** `next/core-web-vitals`, react half — dumped from the preset, verbatim. */
const NEXT_REACT_RULES = {
  "react/display-name": "error",
  "react/jsx-key": "error",
  "react/jsx-no-comment-textnodes": "error",
  "react/jsx-no-duplicate-props": "error",
  "react/jsx-no-target-blank": "off",
  "react/jsx-no-undef": "error",
  "react/jsx-uses-react": "error",
  "react/jsx-uses-vars": "error",
  "react/no-children-prop": "error",
  "react/no-danger-with-children": "error",
  "react/no-deprecated": "error",
  "react/no-direct-mutation-state": "error",
  "react/no-find-dom-node": "error",
  "react/no-is-mounted": "error",
  "react/no-render-return-value": "error",
  "react/no-string-refs": "error",
  "react/no-unescaped-entities": "error",
  "react/no-unknown-property": "off",
  "react/no-unsafe": "off",
  "react/prop-types": "off",
  "react/react-in-jsx-scope": "off",
  "react/require-render-return": "error",
};

/** `next/core-web-vitals`, a11y half — dumped from the preset, verbatim. */
const NEXT_A11Y_RULES = {
  "jsx-a11y/alt-text": ["warn", { elements: ["img"], img: ["Image"] }],
  "jsx-a11y/aria-props": "warn",
  "jsx-a11y/aria-proptypes": "warn",
  "jsx-a11y/aria-unsupported-elements": "warn",
  "jsx-a11y/role-has-required-aria-props": "warn",
  "jsx-a11y/role-supports-aria-props": "warn",
};

export default defineConfig([
  globalIgnores(["dist/**", "node_modules/**", "src/lib/types.gen.ts"]),

  ...tseslint.configs.recommended,
  reactHooks.configs.flat["recommended-latest"],
  reactRefresh.configs.vite,

  {
    files: ["**/*.{ts,tsx,mts,mjs}"],
    plugins: { react, "jsx-a11y": jsxA11y },
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    rules: {
      ...NEXT_REACT_RULES,
      ...NEXT_A11Y_RULES,
      // The two levels `next/typescript` ended on. See the note above.
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-unused-expressions": "warn",
    },
  },

  {
    // See the note above: reported, not enforced.
    files: ["**/*.{ts,tsx}"],
    rules: { "react-refresh/only-export-components": "warn" },
  },

  {
    // Tests mount components from modules that also export helpers, and
    // fixtures export data. Fast Refresh never sees either.
    files: ["**/*.test.{ts,tsx}", "**/__fixtures__/**"],
    rules: { "react-refresh/only-export-components": "off" },
  },
]);
