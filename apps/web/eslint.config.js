// Flat config: typescript-eslint (type-aware, strict), React hooks and jsx-a11y. Style is prettier's job.
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

const TS = ["**/*.{ts,tsx}"];

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "src/routeTree.gen.ts",
      "playwright-report/**",
      "test-results/**",
      "e2e/.state/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked.map((c) => ({ ...c, files: TS })),
  ...tseslint.configs.stylisticTypeChecked.map((c) => ({ ...c, files: TS })),
  {
    files: TS,
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // The API's JSON is typed by hand (packages/contracts/src/types.ts); a template literal of a number is fine.
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true, allowBoolean: true }],
      "@typescript-eslint/no-unnecessary-condition": ["error", { allowConstantLoopConditions: true }],
      "@typescript-eslint/consistent-type-definitions": "off",
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      "@typescript-eslint/no-misused-promises": ["error", { checksVoidReturn: { attributes: false } }],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks, "jsx-a11y": jsxA11y },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
    },
  },
  {
    // TanStack Router's redirects are thrown as plain objects by design.
    files: ["src/routes/**/*.{ts,tsx}", "src/auth/guards.ts"],
    rules: { "@typescript-eslint/only-throw-error": "off" },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**/*.{ts,tsx}", "src/**/*.d.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-empty-function": "off",
      "@typescript-eslint/no-empty-object-type": "off",
    },
  },
  {
    files: ["e2e/**/*.ts", "*.config.ts"],
    languageOptions: { globals: { ...globals.node } },
    rules: { "no-console": "off" },
  },
  {
    // Plain Node scripts (the e2e launcher, this file, the size budget): no type information.
    files: ["**/*.{js,mjs,cjs}"],
    languageOptions: { globals: { ...globals.node }, parserOptions: { projectService: false, project: false } },
    rules: { "no-console": "off" },
  },
);
