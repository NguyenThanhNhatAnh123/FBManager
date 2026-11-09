// Rendered UI only. No cookies, tokens, React internals or password fields are read.
({mode, email, recoveryEmail, formAddress, passwordEntered}) => {
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[aria-hidden="true"]'));
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const body=clean(document.body?.innerText);
  const find=selectors=>selectors.find(selector=>[...document.querySelectorAll(selector)].some(visible));
  if(mode==='login'){
    const host=location.hostname.toLowerCase();
    if(['microsoft.com','www.microsoft.com'].includes(host)&&/^\/(?:[a-z]{2}-[a-z]{2}\/)?microsoft-365\/outlook(?:\/|$)/i.test(location.pathname)){
      for(const link of document.querySelectorAll('a[href]')){
        if(!visible(link)||!/^sign in(?: to outlook)?$|^đăng nhập(?:.*outlook)?$|^se connecter(?:.*outlook)?$|^anmelden$/i.test(clean(link.getAttribute('aria-label')||link.innerText)))continue;
        const url=new URL(link.href,location.href);
        if(url.protocol!=='https:'||url.username||url.password||(url.port&&url.port!=='443'))continue;
        const auth=['login.live.com','login.microsoftonline.com','account.live.com'].includes(url.hostname);
        const mailbox=url.hostname==='outlook.live.com'&&/^\/(mail|owa)(\/|$)/.test(url.pathname);
        const official=url.hostname==='go.microsoft.com'&&url.pathname.replace(/\/$/,'')==='/fwlink/p'&&url.searchParams.get('linkid')==='2125442';
        if(auth||mailbox||official)return {stage:'outlook_landing',signin_url:url.href};
      }
      return {stage:'outlook_landing',signin_url:''};
    }
    if(['outlook.live.com','outlook.office.com','outlook.office365.com'].includes(host)&&/\/mail\b/.test(location.pathname)
       &&/inbox|hộp thư|boîte de réception|posteingang|bandeja de entrada|mail folders/i.test(body)){
      const marker=document.querySelector('#mectrl_currentAccount_primary,#mectrl_currentAccount_email,[data-testid="account-email"]');
      return {stage:'signed_in',account_email:clean(marker?.innerText)};
    }
    if(!['login.live.com','login.microsoftonline.com','account.live.com'].includes(host))return {stage:'loading'};
    if([...document.querySelectorAll('iframe')].some(e=>visible(e)&&/captcha|arkose|funcaptcha/i.test(e.title+' '+e.src)))return {stage:'needs_user'};
    if(/incorrect password|password is incorrect|mot de passe.*incorrect|mật khẩu.*không đúng|account.*locked|compte.*bloqué|tài khoản.*bị khóa|too many requests|too many attempts|code.*incorrect|mã.*không đúng|that microsoft account doesn.t exist|ce compte microsoft n.existe pas|tài khoản microsoft này không tồn tại/i.test(body))return {stage:'rejected'};
    const pass=find(['input[name="passwd"]','#i0118','input[type="password"]']);
    if(pass)return {stage:'password',selector:pass};
    if(!passwordEntered){
      document.querySelectorAll('[data-cm-use-password]').forEach(el=>el.removeAttribute('data-cm-use-password'));
      for(const el of document.querySelectorAll('a,button,[role="button"],[role="link"]')){
        if(!visible(el)||el.matches(':disabled')||el.getAttribute('aria-disabled')==='true')continue;
        const label=clean(el.getAttribute('aria-label')||el.innerText);
        if(!/^(?:use (?:your )?password(?: instead)?|sign in with (?:your )?password|(?:sử dụng|dùng) mật khẩu(?: của bạn)?|đăng nhập bằng mật khẩu|utiliser votre mot de passe|kennwort verwenden|usar tu contraseña)$/i.test(label))continue;
        const href=el.getAttribute('href');
        if(href){
          const url=new URL(href,location.href);
          if(url.protocol!=='javascript:'&&!(url.protocol==='https:'&&['login.live.com','login.microsoftonline.com','account.live.com'].includes(url.hostname)))continue;
        }
        el.setAttribute('data-cm-use-password','1');
        return {stage:'use_password',selector:'[data-cm-use-password="1"]'};
      }
    }
    const code=find(['input[name="otc"]','input[autocomplete="one-time-code"]','#idTxtBx_SAOTCC_OTC','input[name="code"]']);
    if(code)return {stage:'code',selector:code};
    const proof=find(['input[name="ProofConfirmation"]','#idTxtBx_SAOTCS_ProofConfirmation','#iProofEmail']);
    if(proof)return {stage:'recovery',selector:proof};
    const user=find(['input[name="loginfmt"]','#i0116']);
    if(user)return {stage:'email',selector:user};
    if(/stay signed in|rester connecté|duy trì đăng nhập|angemeldet bleiben/i.test(body))return {stage:'stay_signed_in'};
    const parts=(recoveryEmail||'').toLowerCase().split('@'),domain=parts[1],prefix=parts[0]?.slice(0,2);
    let index=0;
    for(const el of document.querySelectorAll('button,[role="button"],label')){
      if(!visible(el))continue;
      const text=clean(el.innerText+' '+(el.getAttribute('aria-label')||'')).toLowerCase();
      if(domain&&text.includes(domain)&&prefix&&text.includes(prefix)&&text.length<350){
        el.setAttribute('data-cm-proof',String(index));
        return {stage:'choose_recovery',selector:'[data-cm-proof="'+index+'"]'};
      }
      index++;
    }
    if(/verify|vérifi|xác minh|protect your account|confirmer votre|confirm your|security info|thông tin bảo mật/i.test(body))return {stage:'needs_user'};
    return {stage:'loading'};
  }
  const address=(email||'').toLowerCase();
  const queryAddress=new URL(location.href).searchParams.get('email')?.toLowerCase();
  // Bind FVia's split address controls only after this reader submitted Get Email.
  const fvia=location.hostname==='fviainboxes.com';
  const username=document.querySelector('input#username[name="username"]');
  const selectedDomain=clean(document.querySelector('button[aria-haspopup="listbox"]')?.innerText).toLowerCase();
  const formMatches=fvia&&formAddress?.toLowerCase()===address&&username&&visible(username)
    &&(username.value+'@'+selectedDomain).toLowerCase()===address;
  // Inboxes' homepage contains a marketing inbox; only its current mailbox heading binds an address.
  const inboxes=location.hostname==='inboxes.com';
  const headingAddresses=clean(document.querySelector('h1')?.innerText).toLowerCase().match(/[^\s@<>]+@[a-z0-9.-]+\.[a-z]{2,63}/g)||[];
  const addressMatches=inboxes?headingAddresses.includes(address):(body.toLowerCase().includes(address)||queryAddress===address||formMatches);
  const isInbox=!!address&&addressMatches&&/inbox|hộp thư|mailbox|boîte|bandeja/i.test(body);
  if(!isInbox)return {inbox:false,items:[]};
  const microsoft=/microsoft|accountprotection\.microsoft\.com/i;
  const roots=[...document.querySelectorAll('article,[role="listitem"],tr,[data-message-id],button,a,[role="button"]'+(fvia?',div[class*="cursor-pointer"]':''))].filter(e=>{
    if(!visible(e))return false;
    if(e.tagName==='A'){
      const u=new URL(e.href,location.href);
      if(!['http:','https:'].includes(u.protocol)||u.origin!==location.origin)return false;
    }
    return true;
  });
  const items=[];
  for(const el of roots){
    const text=clean(el.innerText);
    if(!text||text.length>8000||!microsoft.test(text))continue;
    if(roots.some(other=>other!==el&&el.contains(other)&&microsoft.test(clean(other.innerText))))continue;
    const timestamp=el.querySelector('time[datetime]')?.getAttribute('datetime');
    const link=el.getAttribute('href')||el.getAttribute('data-message-id')||'';
    const stableId=el.getAttribute('data-message-id')||el.getAttribute('data-id')||el.id;
    const stableText=text
      .replace(/just now|vừa xong|à l['’]instant|maintenant|\b(?:\d+\s*(?:seconds?|secondes?|minutes?|hours?|heures?|days?|jours?|mins?|hrs?|s|m|h|d|j|phút|giờ|ngày)(?:\s+ago|\s+trước)?)\b/gi,'[relative time]')
      .replace(/\btoday\b|\byesterday\b|hôm nay|hôm qua|aujourd['’]hui|\bhier\b/gi,'[calendar day]');
    const key=stableId?'id:'+stableId:link+'|'+stableText;
    el.setAttribute('data-cm-mail',String(items.length));
    items.push({key,text,received_at:timestamp,selector:'[data-cm-mail="'+items.length+'"]'});
  }
  // Once a newly arrived Microsoft message is open, its body may be outside the list.
  const detail=[...document.querySelectorAll('[role="dialog"],article,main,[role="main"]')].filter(visible)
    .map(e=>clean(e.innerText)).find(text=>text.length<16000&&microsoft.test(text));
  return {inbox:true,loading:/\bCONNECTING\b|\bLoading\b|Updating…|Đang tải|something went wrong/i.test(body)||!!document.querySelector('[aria-busy="true"],[role="progressbar"]'),items,detail:detail||''};
}
