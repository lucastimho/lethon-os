import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

export default [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // The radial-force pull mutates simulation node properties in-place;
      // d3 expects this and it isn't worth a refactor for the lint rule.
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
