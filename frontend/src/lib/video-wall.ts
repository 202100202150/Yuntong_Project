export const layouts=[1,2,4,9,16] as const;
export type VideoLayout=typeof layouts[number];
export function normalizeLayout(value:unknown):VideoLayout { const number=Number(value);return layouts.includes(number as VideoLayout)?number as VideoLayout:9 }
export function orderedVisibleIds(allIds:string[],hiddenIds:string[],order:string[]):string[]{const available=new Set(allIds);return [...new Set([...order.filter(id=>available.has(id)),...allIds])].filter(id=>!hiddenIds.includes(id))}
export function pageCount(total:number,size:number){return Math.max(1,Math.ceil(total/size))}
export function moveCamera(ids:string[],source:string,target:string){const result=ids.filter(id=>id!==source),index=result.indexOf(target);if(index<0||!ids.includes(source))return ids.slice();result.splice(index,0,source);return result}
