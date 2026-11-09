import {resourceUrl} from './api.js';

const NS='http://www.w3.org/2000/svg';
const paths={
  grid:['M3 3h7v7H3z','M14 3h7v7h-7z','M3 14h7v7H3z','M14 14h7v7h-7z'],
  globe:['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0','M3 12h18','M12 3c5 5 5 13 0 18-5-5-5-13 0-18'],
  pages:['M7 3h14v16H7z','M3 7v14h14','M11 7h6','M11 11h6'],
  calendar:['M4 5h16v16H4z','M4 10h16','M8 3v4','M16 3v4','M8 14h2','M14 14h2'],
  settings:['M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
  key:['M14 14a6 6 0 1 0-4-4L3 17v4h4v-3h3v-3z'],
  activity:['M3 12h4l3-8 4 16 3-8h4'],
  refresh:['M20 7V3l-3 3A8 8 0 0 0 4 10','M4 17v4l3-3a8 8 0 0 0 13-4'],
  plus:['M12 5v14','M5 12h14'],
  chevron:['m9 5 7 7-7 7'],
  down:['m6 9 6 6 6-6'],
  check:['m5 12 4 4L19 6'],
  shield:['M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z','m8 12 3 3 5-6'],
  search:['M21 21l-5-5','M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0'],
  x:['m6 6 12 12','M18 6 6 18'],
  clock:['M12 8v5l3 2','M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  alert:['M12 3 2 21h20z','M12 9v5','M12 17v1'],
  external:['M14 3h7v7','M21 3l-10 10','M10 3H3v18h18v-7'],
  copy:['M8 8h13v13H8z','M16 8V3H3v13h5'],
  eye:['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
  link:['M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-3 3','M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l3-3'],
  logout:['M9 3H3v18h6','M9 12h12','m17 8 4 4-4 4'],
  info:['M12 11v6','M12 7v1','M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  posts:['M4 3h16v18H4z','M8 7h8','M8 11h8','M8 15h5'],
};
export function icon(name,size=20){const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('width',size);svg.setAttribute('height',size);svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.7');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');for(const data of paths[name]||paths.info){const path=document.createElementNS(NS,'path');path.setAttribute('d',data);svg.append(path)}return svg}
export function el(tag,attrs={},...children){const node=document.createElement(tag);for(const [key,value] of Object.entries(attrs)){if(value===null||value===undefined||value===false)continue;if(key==='class')node.className=value;else if(key==='text')node.textContent=value;else if(key.startsWith('on')&&typeof value==='function')node.addEventListener(key.slice(2).toLowerCase(),value);else if(key==='value')node.value=value;else if(key==='disabled'||key==='checked'||key==='required')node[key]=!!value;else node.setAttribute(key,String(value))}for(const child of children.flat())if(child!==null&&child!==undefined)node.append(child instanceof Node?child:document.createTextNode(String(child)));return node}
export function button(text,action,{style='secondary',iconName,disabled=false,small=false,...attrs}={}){return el('button',{type:'button',class:'button '+style+(small?' small':''),disabled,onClick:action,...attrs},iconName?icon(iconName,16):null,el('span',{text}))}
export function badge(text,tone='neutral'){return el('span',{class:'badge '+tone},el('i'),text)}
export function empty(title,message,action){return el('div',{class:'empty-state'},el('span',{class:'empty-icon'},icon('pages',28)),el('h3',{text:title}),el('p',{text:message}),action)}
export function errorBox(message,retry){return el('div',{class:'error-box',role:'alert'},icon('alert',18),el('div',{},el('strong',{text:'Chưa tải được dữ liệu'}),el('p',{text:message})),retry?button('Thử lại',retry,{small:true}):null)}
export function skeleton(lines=4){const box=el('div',{class:'skeletons','aria-label':'Đang tải dữ liệu',role:'status'});for(let i=0;i<lines;i++)box.append(el('div',{class:'skeleton'}));return box}
export function announce(message){document.querySelector('#announcer').textContent=message}
export function toast(message,tone='success'){const item=el('div',{class:'toast '+tone,role:tone==='error'?'alert':'status'},icon(tone==='error'?'alert':'check',18),el('span',{text:message}));document.querySelector('#toasts').append(item);setTimeout(()=>item.remove(),6500)}
export function dateTime(value,timezone){if(!value)return 'Chưa có dữ liệu';try{const p=Object.fromEntries(new Intl.DateTimeFormat('vi-VN',{timeZone:timezone,day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(part=>[part.type,part.value]));return `${p.day}/${p.month}/${p.year.padStart(4,'0')} ${p.hour}:${p.minute}`}catch{return 'Chưa xác định'}}
export function relative(value){if(!value)return 'Chưa đồng bộ';const minutes=Math.floor((Date.now()-new Date(value).getTime())/60000);if(!Number.isFinite(minutes))return 'Chưa xác định';if(minutes<1)return 'Vừa cập nhật';if(minutes<60)return minutes+' phút trước';if(minutes<1440)return Math.floor(minutes/60)+' giờ trước';return Math.floor(minutes/1440)+' ngày trước'}
export function number(value){return typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('vi-VN').format(value):'—'}
export function avatar(name,id,avatarUrl){
  const words=String(name||'Page').trim().split(/\s+/),initials=(words[0]?.[0]||'P')+(words.length>1?words.at(-1)?.[0]||'':''),colors=['blue','violet','teal','rose','amber'];
  const node=el('span',{class:'avatar '+colors[[...String(id)].reduce((a,c)=>a+c.charCodeAt(0),0)%colors.length],role:'img','aria-label':'Ảnh đại diện Page '+(name||'chưa có tên')},el('span',{class:'avatar-initials','aria-hidden':'true',text:initials.toLocaleUpperCase('vi')}));
  // Catalog metadata can opt into only this Page's authenticated app route.
  // Never load a remote URL, query token, or a different Page's image.
  if(/^[0-9]{1,32}$/.test(String(id))&&avatarUrl==='/api/pages/'+id+'/avatar'){
    node.append(el('img',{alt:'',loading:'lazy',decoding:'async',referrerpolicy:'no-referrer',
      onLoad:()=>node.classList.add('has-photo'),onError:event=>{node.classList.remove('has-photo');event.currentTarget.remove()},src:resourceUrl(avatarUrl)}));
  }
  return node;
}
export function facebookUrl(value){try{const url=new URL(value);if(url.protocol!=='https:'||!['facebook.com','www.facebook.com','m.facebook.com'].includes(url.hostname)||url.username||url.password||url.port&&url.port!=='443')return null;const query=new URLSearchParams();for(const key of ['id','story_fbid','fbid'])if(url.searchParams.has(key))query.set(key,url.searchParams.get(key));return 'https://www.facebook.com'+url.pathname+(query.size?'?'+query:'')}catch{return null}}
export function openDialog(dialog){dialog.showModal();document.body.classList.add('dialog-open');const close=()=>{if(!document.querySelector('dialog[open]'))document.body.classList.remove('dialog-open');dialog.removeEventListener('close',close)};dialog.addEventListener('close',close)}
export function safeMessage(error){return error?.name==='ApiError'?error.message:'Chưa hoàn tất thao tác. Vui lòng thử lại.'}
