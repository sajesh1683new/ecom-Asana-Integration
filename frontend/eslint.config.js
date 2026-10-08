import tseslint from 'typescript-eslint';
export default tseslint.config({files:['src/**/*.{ts,tsx}'],languageOptions:{parser:tseslint.parser},rules:{'no-debugger':'error','no-constant-condition':'error'}});
