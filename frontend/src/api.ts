export const roles=['Requester / Product owner','Designer','QA reviewer','QA lead','Developer','Release owner'];
export async function api(path:string,body?:unknown,method?:string):Promise<any>{
 const r=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json','X-Role':localStorage.getItem('vqa-role')||'QA reviewer'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
}
