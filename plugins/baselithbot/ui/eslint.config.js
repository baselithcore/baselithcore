import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

// Flat config (ESLint 9+). `typescript-eslint`'s recommended config already
// turns `no-undef` off for TS files (TS itself catches unresolved
// identifiers; the ESLint rule produces false positives on ambient/global
// types), so no manual override is needed here.
export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', '.tsbuild-node/**', 'coverage/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    ...reactHooks.configs['recommended-latest'],
    rules: {
      ...reactHooks.configs['recommended-latest'].rules,
      // Match the underscore-prefix convention already used for intentionally
      // unused parameters (e.g. mock signatures in tests).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  }
);
