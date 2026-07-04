// Flat ESLint config (ESLint 9). Focused, high-signal ruleset — the goal is a
// static gate the backend already has (ruff/mypy), not a big refactor. Type-
// checked rules are intentionally off (fast, low-noise); tsc -b covers types.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "coverage/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Allow intentional unused args when prefixed with _ (event handlers etc).
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // The codebase uses a few empty catch blocks deliberately (best-effort).
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
  {
    // Tests lean on jsdom globals and expect-style assertions.
    files: ["**/*.test.{ts,tsx}", "src/test/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);
