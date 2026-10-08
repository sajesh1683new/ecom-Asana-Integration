import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';
const target=process.env.VQA_API_ORIGIN||'http://127.0.0.1:8000';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':target,'/files':target,'/demo-store':target}},test:{environment:'jsdom',setupFiles:['./src/test-setup.ts']}});
