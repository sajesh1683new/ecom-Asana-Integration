import {useState} from 'react';
import {roles} from './api';
export function App(){const [role,setRole]=useState(localStorage.getItem('vqa-role')||'QA reviewer');return <main><header><h1>Visual QA Bridge</h1><select aria-label="Current role" value={role} onChange={e=>{setRole(e.target.value);localStorage.setItem('vqa-role',e.target.value)}}>{roles.map(r=><option key={r}>{r}</option>)}</select></header><p>Asana requests → browser evidence → AI first pass → human review → Asana write-back</p><p>Mock-first · Nimbus Sleep Co. · All data is fictional</p></main>}
