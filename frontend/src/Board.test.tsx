import {render,screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {describe,it,expect,vi} from 'vitest';
import {Board} from './Board';
vi.mock('./api',()=>({api:async()=>({sections:[{gid:'1',name:'Intake'}],tasks:[{id:1,gid:'42',section_gid:'1',name:'Synthetic task',notes:'Missing sections',custom_fields:{}}],stories:[{id:1,task_gid:'42',author:'QA lead',text:'Please identify changed sections.'}],attachments:[]})}));
describe('Mock Asana board',()=>{
  it('labels the simulation and follows a task link to its persisted comments',async()=>{
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={['/mock-asana?task=42']}><Board/></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText('Please identify changed sections.')).toBeInTheDocument();
    expect(screen.getByText(/not your real Asana account/)).toBeInTheDocument();
  });
});
