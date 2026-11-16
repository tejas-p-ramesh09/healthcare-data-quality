import tseslint from 'typescript-eslint'
import hooks from 'eslint-plugin-react-hooks'

export default tseslint.config(
  { ignores: ['dist/**', 'src/api/schema.d.ts', 'playwright-report/**', 'test-results/**'] },
  { files: ['**/*.ts', '**/*.tsx'], extends: [tseslint.configs.recommended] },
  { files: ['**/*.tsx'], plugins: { 'react-hooks': hooks }, rules: {
    'react-hooks/rules-of-hooks': 'error', 'react-hooks/exhaustive-deps': 'error',
  } },
)
