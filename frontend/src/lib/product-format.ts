export const dateTime = (value?: number | string | null) => value ? new Intl.DateTimeFormat('zh-CN', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }).format(new Date(value)).replaceAll('/', '-') : '—';
export const timeOnly = (value?: number | string | null) => value ? new Intl.DateTimeFormat('zh-CN', {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) : '—';
export const secondsLabel = (value: number) => { const n=Math.max(0, Math.round(value)); return [Math.floor(n/3600),Math.floor(n/60)%60,n%60].map(i=>String(i).padStart(2,'0')).join(':'); };
export const bytesLabel = (value: number) => value >= 1024*1024*1024 ? (value/1024/1024/1024).toFixed(1)+' GB' : (value/1024/1024).toFixed(1)+' MB';
export const localDate = () => { const date=new Date(); return [date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-'); };
export const cameraName = (id: string) => id==='camera1'?'C1':id==='camera2'?'C2':id;
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';
export interface ProductMedia { id:string; name:string; cameraId:string; kind:'image'|'video'; startedAt:number; duration:number; bytes:number; annotated:boolean; ready:boolean; url:string; }
export interface ProductEvent { id:string; trackId:string; startedAt:number; lastSeenAt:number; status:'pending'|'reviewed'; conclusion:string|null; note:string; reviewedBy:string|null; reviewedAt:number|null; payload:{evidence?:string[];target?:Record<string,unknown>;trajectory?:Array<Record<string,number>>;cameras?:Record<string,unknown[]>};media?:ProductMedia[] }
export const conclusions: Record<string,string> = {drone:'无人机',other:'非无人机',uncertain:'无法判断'};
export const mediaUrl=(value:string)=>value.startsWith('/api/')?value:value.startsWith('/media/')?'/api'+value:value;
