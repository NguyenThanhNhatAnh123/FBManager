// Only rendered Facebook UI. No browser storage, framework internals or private endpoints.
({mode})=>{
 const visible=e=>{
  if(!e?.getClientRects().length||e.closest('[aria-hidden="true"],[hidden]'))return false;
  for(let node=e;node;node=node.parentElement){
   const style=getComputedStyle(node);
   if(style.visibility==='hidden'||style.visibility==='collapse'||style.display==='none'||Number(style.opacity)===0)return false;
  }
  return true;
 };
 const clean=s=>(s||'').replace(/\s+/g,' ').trim();
 const buttons=[...document.querySelectorAll('button,[role=button]')].filter(visible);
 const dialogs=[...document.querySelectorAll('dialog,[role=dialog]')].filter(visible);
 const actionPatterns={review:/^(Review Invite|Review Invitation|Xem lại lời mời|Xem lời mời|Xem xét lời mời)$/i,next:/^(Next|Tiếp|Tiếp tục|Suivant)$/i,accept:/^(Accept(?: invitation)?|Chấp nhận(?: lời mời)?|Accepter)$/i,continue:/^(Continue|Tiếp tục|Continuer)$/i,switch_now:/^(Switch now|Chuyển ngay|Basculer maintenant)$/i};
 const labels=e=>{
  const text=clean(e.innerText||e.textContent);
  const labelledby=clean((e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' '));
  return {accessible:labelledby||clean(e.getAttribute('aria-label'))||text,text};
 };
 const actionsFor=scope=>Object.fromEntries(Object.entries(actionPatterns).map(([key,pattern])=>{
  const match=buttons.filter(e=>scope===document||scope.contains(e)).map(labels).find(value=>pattern.test(value.accessible)||pattern.test(value.text));
  return [key,match?.accessible||null];
 }));
 const titleFor=scope=>clean([...scope.querySelectorAll('h1,h2,h3,[role=heading]')].find(visible)?.innerText);
 const reviewContext=/review invit(?:e|ation)|xem (?:lại |xét )?lời mời|partial control|quyền kiểm soát một phần|full control|toàn quyền kiểm soát/i;
 const emailContext=/check your email|enter the code|confirm it.s you|kiểm tra email|nhập mã|vérifiez.{0,20}(?:email|e-mail)/i;
 const acceptedContext=/invite accepted|invitation accepted|đã chấp nhận lời mời|lời mời đã được chấp nhận/i;
 const ranked=dialogs.map((element,index)=>{
  const actions=actionsFor(element),title=titleFor(element),text=clean(element.innerText);
  const relevant=reviewContext.test(title)||emailContext.test(title)||acceptedContext.test(title);
  const contextual=reviewContext.test(text)||emailContext.test(text)||acceptedContext.test(text);
  const current=actions.accept&&reviewContext.test(text)||actions.continue&&emailContext.test(text)||actions.switch_now&&acceptedContext.test(text);
  const score=(relevant?1000:contextual?200:0)+(current?200:0)+(Object.values(actions).some(Boolean)?100:0);
  return {element,index,score};
 }).sort((a,b)=>b.score-a.score||b.index-a.index);
 const dialog=ranked[0]?.element,scope=dialog||document;
 const title=dialog?titleFor(dialog):'';
 if(mode==='invites'){
  const invitations=[];
  for(const a of document.querySelectorAll('a[href]')){
   if(!visible(a))continue;
   const label=clean(a.innerText||a.getAttribute('aria-label'));
   if(!/invited you to manage|mời bạn (?:quản lý|làm)|invit[eé].{0,60}(?:gérer|page)/i.test(label))continue;
   try{
    const u=new URL(a.href,location.href);
    if(u.protocol!=='https:'||!['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname))continue;
    const match=label.match(/invited you to manage (.+?)(?:'s Page|’s Page|\.?$)/i);
    invitations.push({url:u.href,name:match?.[1]||'',label:label.slice(0,240)});
   }catch{}
  }
  return {invitations};
 }
 if(mode==='stage'){
  const inputs=[...scope.querySelectorAll('input')].filter(visible);
  const textInputs=inputs.filter(e=>['text','tel','number',''].includes(e.type)&&!e.disabled&&!e.readOnly);
  const inputLabel=e=>[...(e.labels||[])].filter(visible).map(label=>label.innerText).join(' ')+' '+(e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ');
  const code=textInputs.find(e=>/code|mã|security|verification/i.test([e.name,e.id,e.autocomplete,e.getAttribute('aria-label'),e.placeholder,inputLabel(e)].join(' ')))||(emailContext.test(clean(scope.innerText))&&textInputs.length===1?textInputs[0]:null);
  const host=location.hostname;
  const blocked=/\/checkpoint|\/login(?:[/?]|\.php)|\/recover\//.test(location.pathname)||[...document.querySelectorAll('input[name=email],input[name=pass]')].some(visible);
  const actions=actionsFor(scope);
  const codeStage=!!code&&emailContext.test(clean(scope.innerText));
  const accepted=acceptedContext.test(title||clean(scope.innerText));
  const partial=/partial control|quyền kiểm soát một phần|contrôle partiel/i.test(clean(scope.innerText));
  const full=/full control|toàn quyền kiểm soát|contrôle total/i.test(clean(scope.innerText));
  const heading=clean([...document.querySelectorAll('h1,[role=heading][aria-level="1"]')].filter(visible).find(e=>!e.closest('[role=dialog]'))?.innerText);
  return {blocked,host,title,heading,code:codeStage,accepted,control:full?'full':partial?'partial':'unknown',actions,
   review:!!actions.review,accept:!!actions.accept,next:!!actions.next,continue:!!actions.continue,switch_now:!!actions.switch_now,
   code_error:/incorrect code|invalid code|mã.{0,20}(?:không đúng|không hợp lệ)|code.{0,20}(?:incorrect|invalide)/i.test(clean(scope.innerText)),
   resend:/send (?:a )?new code|gửi.{0,15}mã mới/i.test(clean(scope.innerText))};
 }
 if(mode==='setup'){
  const images=[...document.querySelectorAll('img')].filter(visible).map(e=>({alt:clean(e.alt),src:e.currentSrc||e.src,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}));
  return {buttons:buttons.map(e=>clean(e.getAttribute('aria-label')||e.innerText)).filter(Boolean),images};
 }
 return {};
}
