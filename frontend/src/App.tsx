import {useState} from 'react';
import {roles} from './api';
import {Board} from './Board';
import {Routes,Route,Link} from 'react-router-dom';
export function App(){const [role,setRole]=useState(localStorage.getItem('vqa-role')||'QA reviewer');return <main><header><Link to="/">Visual QA Bridge</Link><select aria-label="Current role" value={role} onChange={e=>{setRole(e.target.value);localStorage.setItem('vqa-role',e.target.value)}}>{roles.map(r=><option key={r}>{r}</option>)}</select></header><nav><Link to="/mock-asana">Mock Asana board</Link></nav><Routes><Route path="/mock-asana" element={<Board/>}/><Route path="*" element={<><h1>Visual QA Bridge</h1><p>Asana requests → browser evidence → AI first pass → human review → Asana write-back</p><p>Mock-first · Nimbus Sleep Co. · All data is fictional</p></>}/></Routes></main>}
