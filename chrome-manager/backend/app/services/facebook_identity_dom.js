// Rendered identity controls only; never cookies, React internals or session data.
() => {
  const visible=e=>{
    if(!e?.getClientRects().length||e.closest('[aria-hidden="true"],[hidden],[inert]'))return false;
    for(let node=e;node;node=node.parentElement){
      const style=getComputedStyle(node);
      if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0)return false;
    }
    return true;
  };
  const clean=s=>(s||'').replace(/\s+/g,' ').trim();
  const excluded=e=>!!e.closest('[role="article"],[data-pagelet^="FeedUnit_"]');
  const profileUrl=raw=>{
    if(!raw)return '';
    try{
      const u=new URL(raw,location.href);
      if(u.protocol!=='https:'||!['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname)||u.username||u.password)return '';
      const path=u.pathname.replace(/^\/|\/$/g,'');
      if(path==='profile.php'&&/^\d+$/.test(u.searchParams.get('id')||''))return 'https://www.facebook.com/profile.php?id='+u.searchParams.get('id');
      const numeric=/^(?:people|pages)\/[^/]+\/(\d+)$/.exec(path);
      if(numeric)return 'https://www.facebook.com/profile.php?id='+numeric[1];
      if(!/^[\w.-]+$/.test(path)||/^(?:me|home\.php|profile\.php|pages|settings|groups|friends|watch|reels?|notifications|messages|login(?:\.php)?|checkpoint|recover|help|privacy|business|marketplace|gaming|events|stories|search)$/i.test(path)||/\.php$/i.test(path))return '';
      return 'https://www.facebook.com/'+path;
    }catch{return ''}
  };
  const text=[...document.querySelectorAll('h1,h2,[role="heading"],[role="dialog"]')].filter(e=>visible(e)&&!excluded(e)).map(e=>clean(e.innerText)).join(' ').slice(0,16000);
  const blocked=/\/(?:login(?:\.php)?|checkpoint|two_step_verification|recover)(?:[/?]|$)/.test(location.pathname)
    ||[...document.querySelectorAll('input[name="email"],input[name="pass"]')].some(visible)
    ||/temporarily blocked|tạm thời bị chặn|confirm your identity|xác nhận danh tính/i.test(text);
  if(blocked)return {blocked:true};
  for(const e of document.querySelectorAll('[data-cm-identity]'))e.removeAttribute('data-cm-identity');
  let sequence=0;
  const mark=e=>{if(!e.hasAttribute('data-cm-identity'))e.setAttribute('data-cm-identity',String(sequence++));return e.getAttribute('data-cm-identity')};
  const labels=e=>{
    const labelledby=clean((e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' '));
    return [...new Set([labelledby||clean(e.getAttribute('aria-label')),clean(e.innerText),clean(e.getAttribute('title'))].filter(Boolean))];
  };
  const label=e=>labels(e)[0]||'';
  const named=(e,pattern)=>labels(e).some(value=>pattern.test(value));
  const buttons=[...document.querySelectorAll('button,[role="button"]')].filter(e=>visible(e)&&!excluded(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true');
  const headings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(e=>visible(e)&&!excluded(e));
  const actingPage=headings.some(e=>/^(?:manage page|quản lý trang|gérer la page)$/i.test(clean(e.innerText)))
    ||buttons.some(e=>named(e,/^(?:switch (?:back )?to your profile|chuyển (?:về|sang) trang cá nhân của bạn|revenir à votre profil)$/i))
    ||(buttons.some(e=>named(e,/^(?:edit page|chỉnh sửa trang|modifier la page)$/i))&&buttons.some(e=>named(e,/^(?:professional dashboard|business dashboard|bảng điều khiển chuyên nghiệp|tableau de bord professionnel)$/i)));
  const manageHeading=/^(?:manage page|quản lý trang|gérer la page)$/i;
  const currentHeading=headings.find(e=>e.matches('h1,[aria-level="1"]')&&clean(e.innerText)&&!/^(?:manage page|quản lý trang|gérer la page|menu|facebook)$/i.test(clean(e.innerText)));
  const currentUrl=profileUrl(location.href)||profileUrl(document.querySelector('link[rel="canonical"]')?.href||'');
  let currentName=clean(currentHeading?.innerText);
  if(!currentName&&actingPage&&currentUrl){
    // A loaded Manage Page sidebar can precede the main profile h1. Bind its
    // nearby Page identity link to this URL; never read the open app-grid menu.
    for(const heading of headings.filter(e=>manageHeading.test(clean(e.innerText)))){
      if(heading.closest('[role="menu"],[role="dialog"],[role="tooltip"]'))continue;
      const headingBox=heading.getBoundingClientRect();let container=heading.parentElement;
      for(let depth=0;container&&depth<5;depth++,container=container.parentElement){
        if(container.matches('html,body,main,[role="main"]'))break;
        const box=container.getBoundingClientRect();
        const semantic=container.matches('aside,nav,[role="navigation"]');
        if(box.left>Math.max(64,innerWidth*.08)||box.width>Math.min(560,innerWidth*(semantic ? 0.8 : 0.55)))continue;
        const names=[...container.querySelectorAll('a[href]')].filter(e=>{
          if(!visible(e)||excluded(e)||e.closest('[role="menu"],[role="dialog"],[role="tooltip"]'))return false;
          const rect=e.getBoundingClientRect();
          return rect.top>=headingBox.bottom-4&&rect.top<=headingBox.bottom+200&&profileUrl(e.href)===currentUrl;
        }).map(e=>clean(e.innerText)||label(e)).filter(value=>value&&value.length<=160&&!manageHeading.test(value));
        const unique=[...new Set(names)];
        if(unique.length===1){currentName=unique[0];break}
      }
      if(currentName)break;
    }
  }
  const controls=[];
  const mobileContext=location.hostname==='m.facebook.com'||innerWidth<=640;
  for(const e of buttons){
    const value=label(e);let kind='';
    if(named(e,/^(?:your profile(?: picture)?|account|trang cá nhân của bạn|ảnh đại diện của bạn|tài khoản|votre profil|compte)(?:[:,].*)?$/i))kind='menu';
    else if(mobileContext&&named(e,/^(?:facebook menu|menu facebook)$/i))kind='menu';
    else if(named(e,/^(?:see all profiles|switch profiles?|switch account|xem tất cả trang cá nhân|chuyển (?:trang cá nhân|hồ sơ|tài khoản)|voir tous les profils|changer de profil|changer de compte)$/i))kind='picker';
    else if(named(e,/^(?:switch now|switch into .+|switch to page|chuyển ngay|chuyển sang trang|passer maintenant|basculer maintenant|changer maintenant)$/i))kind='switch_page';
    else if(named(e,/^(?:back|quay lại|trở lại|retour)$/i))kind='back';
    else if(named(e,/^(?:close|đóng|fermer)$/i))kind='close';
    if(kind)controls.push({id:mark(e),kind,label:value});
  }
  // This known Page onboarding coachmark intercepts the account menu. Its
  // Close action is safe only inside a small, exact guide. The observed French
  // translation can have only its Close icon; English still needs Advertise.
  // Never infer dismissal from a generic modal or a lone Close icon.
  const guideSentence=/(?:^|\s)Go to Ads Manager to customi[sz]e your ads with more detailed features and settings\.?(?:$|\s)/i;
  const frenchGuideSentence=/^Accédez au Gestionnaire de publicités pour personnaliser vos publicités avec des fonctionnalités et des paramètres plus détaillés\.?$/i;
  const guideAnchor=/^Go to Ads Manager(?: to customi[sz]e your ads with more detailed features and settings\.?)?$/i;
  const guideRoots=new Set(),dismissedControls=new Set();
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  for(let node=walker.nextNode();node;node=walker.nextNode())if(guideAnchor.test(clean(node.textContent))||frenchGuideSentence.test(clean(node.textContent))||/^Accédez au Gestionnaire de publicités$/i.test(clean(node.textContent)))guideRoots.add(node.parentElement);
  for(const root of guideRoots){
    let scope=root;
    for(let depth=0;scope&&depth<6;depth++,scope=scope.parentElement){
      if(scope.matches('html,body,main,[role="main"]'))break;
      if(!visible(scope)||excluded(scope))continue;
      const text=clean(scope.innerText);
      if(text.length>600||scope.querySelectorAll('*').length>80)break;
      const english=guideSentence.test(text),french=/Accédez au Gestionnaire de publicités pour personnaliser vos publicités avec des fonctionnalités et des paramètres plus détaillés\.?/i.test(text);
      if(!english&&!french)continue;
      if(scope.querySelector('input,textarea,[contenteditable]')||/invit(?:e|ation)|check your email|enter (?:the )?code|partial control|full control|xem.{0,15}lời mời|kiểm tra email|nhập mã|vérifiez.{0,20}(?:e-?mail)|(?:entrez|saisissez).{0,20}code|contrôle (?:partiel|total)/i.test(text))continue;
      const close=buttons.filter(e=>scope.contains(e)&&named(e,/^(?:close|đóng|fermer)$/i));
      const advertise=buttons.filter(e=>scope.contains(e)&&named(e,/^Advertise$/i));
      if(close.length!==1)continue;
      if(english&&advertise.length!==1)continue;
      if(french){
        const actions=buttons.filter(e=>scope.contains(e));
        if(!actingPage||actions.some(e=>e!==close[0]&&!advertise.includes(e))||advertise.length>1)continue;
        let content=text;
        for(const action of actions){const value=clean(action.innerText);if(value)content=clean(content.replace(value,''))}
        if(!frenchGuideSentence.test(content))continue;
      }
      const id=mark(close[0]);
      if(!dismissedControls.has(id)){
        dismissedControls.add(id);
        const previous=controls.findIndex(item=>item.id===id);
        if(previous!==-1)controls.splice(previous,1);
        controls.unshift({id,kind:'dismiss_tip',label:label(close[0])});
      }
      break;
    }
  }
  const mobileMenu=mobileContext&&headings.some(e=>/^(?:menu|trình đơn)$/i.test(clean(e.innerText)));
  let scopes=[...document.querySelectorAll('[role="menu"],[role="dialog"]')].filter(scope=>{
    if(!visible(scope))return false;
    const desktopGrid=!mobileContext&&headings.some(e=>scope.contains(e)&&/^(?:menu|trình đơn)$/i.test(clean(e.innerText)));
    const profilePicker=controls.some(item=>item.kind==='picker'&&scope.querySelector('[data-cm-identity="'+item.id+'"]'));
    return !desktopGrid||profilePicker;
  });
  if(mobileMenu)scopes=[...scopes,document.querySelector('main,[role="main"]')||document.body];
  const candidates=[],seen=new Set();
  const navigation=/^(?:see all|xem tất cả|voir tout|switch profiles?|switch account|chuyển (?:trang cá nhân|hồ sơ|tài khoản)|changer de profil|changer de compte|settings|settings & privacy|cài đặt|paramètres|log out|đăng xuất|déconnexion|help|trợ giúp|aide|create|tạo|créer|add account|thêm tài khoản|ajouter|close|đóng|fermer|back|quay lại|retour|pages|friends|groups|reels|messages|notifications|search|tìm kiếm|menu|manage profiles|quản lý trang cá nhân)/i;
  for(const scope of scopes){
    for(const e of scope.querySelectorAll('a[href],button,[role="button"],[role="radio"],[role="menuitemradio"],[role="option"]')){
      if(!visible(e)||excluded(e)||e.disabled||e.getAttribute('aria-disabled')==='true')continue;
      const value=label(e);if(!value||value.length>240||labels(e).some(value=>navigation.test(value)))continue;
      const rawHref=e.getAttribute('href')||e.querySelector('a[href]')?.href||'';
      const href=profileUrl(rawHref);
      if(rawHref&&!href)continue;
      const explicitPersonal=/^(?:switch (?:back )?to (?:your|personal) profile|your (?:personal )?profile|personal profile|chuyển (?:về|sang) trang cá nhân của bạn|trang cá nhân của bạn|tài khoản cá nhân|votre profil|profil personnel|revenir à votre profil)$/i.test(value)
        ||/(?:\n|,|·|\s)\s*(?:personal profile|trang cá nhân|profil personnel)\s*$/i.test(e.innerText||value);
      const explicitPage=/switch into your page|switch to your page|chuyển sang trang của bạn|passer à votre page/i.test(value);
      const selected=e.getAttribute('aria-checked')==='true'||e.getAttribute('aria-selected')==='true';
      const hasIdentityImage=!!e.querySelector('img,svg[role="img"]');
      if(!href&&!explicitPersonal&&!explicitPage&&!hasIdentityImage&&!e.matches('[role="radio"],[role="menuitemradio"],[role="option"]'))continue;
      let name=clean((e.innerText||value).split('\n')[0]||value)
        .replace(/^(?:switch (?:back )?(?:to|into)|chuyển (?:sang|về)|passer à|basculer vers)\s+/i,'')
        .replace(/,?\s*(?:view your profile|xem trang cá nhân(?: của bạn)?|voir votre profil|switch into your page|switch to your page|chuyển sang trang của bạn|personal profile|profil personnel)\s*$/i,'').trim();
      if(!name||name.length>160)continue;
      const key=href+'\n'+name.toLocaleLowerCase();if(seen.has(key))continue;seen.add(key);
      candidates.push({id:mark(e),name,url:href,kind:explicitPersonal?'personal':explicitPage?'page':'unknown',selected});
    }
  }
  return {blocked:false,ready:!!(currentName||controls.some(item=>['menu','picker','switch_page'].includes(item.kind))||candidates.length),acting_page:actingPage,current_name:currentName,current_url:currentUrl,controls,candidates};
}
