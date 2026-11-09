// Rendered Reel composer only: no cookies, framework stores or private APIs.
({pageUrl,pageName,caption,pageActorVerified=false,verificationOnly=false,discoverPublishedReel=false})=>{
 const clean=value=>(value||'').replace(/\s+/g,' ').trim();
 const norm=value=>clean(value).toLocaleLowerCase();
 const visible=e=>{
  if(!e?.getClientRects().length||e.closest('[hidden],[inert],[aria-hidden="true"]'))return false;
  for(let node=e;node;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0)return false}
  return true;
 };
 const excluded=e=>!!e.closest('[role="article"],[data-pagelet^="FeedUnit_"]');
 const labels=e=>{
  const accessible=clean((e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' '))||clean(e.getAttribute('aria-label'));
  return [...new Set([accessible,clean(e.innerText),clean(e.getAttribute('title'))].filter(Boolean))];
 };
 const label=e=>labels(e)[0]||'';
 const named=(e,pattern)=>labels(e).some(value=>pattern.test(value));
 const createLink=e=>{
  try{const u=new URL(e.getAttribute('href'),location.href);return u.protocol==='https:'&&['www.facebook.com','facebook.com','m.facebook.com'].includes(u.hostname)&&!u.username&&!u.password&&(!u.port||u.port==='443')&&/^\/reels\/create\/?$/.test(u.pathname)}catch{return false}
 };
 const profileUrl=raw=>{
  if(!raw)return '';
  try{const u=new URL(raw,location.href);if(u.protocol!=='https:'||!['www.facebook.com','facebook.com','m.facebook.com'].includes(u.hostname)||u.username||u.password||u.port&&u.port!=='443')return '';
   const path=u.pathname.replace(/^\/|\/$/g,'');
   if(path==='profile.php'&&/^\d+$/.test(u.searchParams.get('id')||''))return 'https://www.facebook.com/profile.php?id='+u.searchParams.get('id');
   const person=/^(?:people|pages)\/[^/]+\/(\d+)$/.exec(path);if(person)return 'https://www.facebook.com/profile.php?id='+person[1];
   if(/^[\w.-]+$/.test(path)&&!/^(?:me|reels?|watch|pages|settings|login|checkpoint|professional_dashboard|home\.php|profile\.php)$/i.test(path))return 'https://www.facebook.com/'+path;
  }catch{}return '';
 };
 const reelUrl=raw=>{
  try{const u=new URL(raw,location.href);if(u.protocol!=='https:'||!['www.facebook.com','facebook.com','m.facebook.com'].includes(u.hostname)||u.username||u.password||u.port&&u.port!=='443')return '';
   const match=/^\/reels?\/(\d+)\/?$/.exec(u.pathname);return match?'https://www.facebook.com/reel/'+match[1]:'';
  }catch{return ''}
 };
 const successPattern=/^(?:your reel has been published[.!]?|reel published[.!]?|your reel was published[.!]?|your reel has been shared[.!]?|thước phim (?:của bạn )?đã được đăng[.!]?|đã đăng thước phim[.!]?|votre reel a été publié[.!]?|reel publié[.!]?)$/i;
 const checkpoints=/\/(?:login(?:\.php)?|checkpoint|recover|two_step_verification)(?:[/?]|$)/.test(location.pathname);
 const login=[...document.querySelectorAll('input[name="email"],input[name="pass"]')].some(visible);
 if(checkpoints||login)return {blocked:true};
 const headings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(e=>visible(e)&&!excluded(e));
 if(headings.some(e=>/confirm your identity|xác nhận danh tính|confirmez votre identité/i.test(label(e))))return {blocked:true};
 if(verificationOnly===true){
  const currentReelUrl=reelUrl(location.href);
  const videos=currentReelUrl?[...document.querySelectorAll('video')].filter(e=>{
   if(!visible(e))return false;
   const box=e.getBoundingClientRect();
   return box.width>0&&box.height>0&&box.right>0&&box.bottom>0&&box.left<innerWidth&&box.top<innerHeight;
  }):[];
  // Reconciliation only: report the user's chosen visible-video criterion.
  // Return before composer markers, menu, author or caption inspection.
  const result={blocked:false,current_reel_url:currentReelUrl,reel_video_visible:videos.length===1,reel_video_count:videos.length};
  if(discoverPublishedReel===true&&!currentReelUrl){
   const candidates=new Set(),viewReel=/^(?:view(?: your)? reel|see(?: your)? reel|watch(?: your)? reel|voir (?:le|votre) r[eé]el|afficher (?:le|votre) r[eé]el|xem thước phim)$/i;
   const notices=[...document.querySelectorAll('h1,h2,h3,[role="heading"],[role="alert"],[role="status"]')].filter(e=>visible(e)&&!excluded(e)&&successPattern.test(label(e)));
   for(const notice of notices){
    const regions=new Set([notice.closest('[role="alert"],[role="status"]'),notice.closest('[role="dialog"],dialog')].filter(Boolean));
    for(const region of regions){
     const box=region.getBoundingClientRect();
     if(!visible(region)||excluded(region)||box.width<=0||box.height<=0||box.width>720||box.height>480||box.right<=0||box.bottom<=0||box.left>=innerWidth||box.top>=innerHeight)continue;
     if(region.querySelector('article,[role="article"],[data-pagelet^="FeedUnit_"],main,[role="main"],video,textarea,[contenteditable]:not([contenteditable="false"]),input[type="file"]'))continue;
     for(const link of region.querySelectorAll('a[href]')){
      if(!visible(link)||excluded(link)||!named(link,viewReel))continue;
      const rect=link.getBoundingClientRect();
      if(rect.width<=0||rect.height<=0||rect.right<=0||rect.bottom<=0||rect.left>=innerWidth||rect.top>=innerHeight||rect.right<box.left||rect.left>box.right||rect.bottom<box.top||rect.top>box.bottom)continue;
      const url=reelUrl(link.getAttribute('href'));if(url)candidates.add(url);
     }
    }
   }
   result.published_reel_candidate_url=candidates.size===1?[...candidates][0]:'';
   result.published_reel_candidate_count=candidates.size;
  }
  return result;
 }
 const composerTitle=/^(?:create (?:a )?reel|new reel|edit reel|share reel|reel settings|tạo thước phim|chỉnh sửa thước phim|chia sẻ thước phim|créer un reel|créer un réel|modifier le reel|partager le reel|paramètres du reel)$/i;
 const dialogs=[...document.querySelectorAll('[role="dialog"],dialog')].filter(visible);
 const composer=dialogs.slice().reverse().find(e=>[...e.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(visible).some(h=>composerTitle.test(label(h))));
 const topDialog=dialogs.at(-1);
 const reelContext=topDialog?topDialog===composer:headings.some(e=>composerTitle.test(label(e)))||/^\/reels?\/create\/?$/.test(location.pathname);
 const main=document.querySelector('main,[role="main"]');
 const scope=topDialog||main||document;
 const dialogTitle=topDialog&&[...topDialog.querySelectorAll('h1,h2,h3,[role="heading"]')].find(visible);
 const menuDialog=!topDialog||dialogTitle&&named(dialogTitle,/^(?:menu|trình đơn)$/i)&&!topDialog.querySelector('textarea,[contenteditable]:not([contenteditable="false"]),input[type="file"],input[type="password"],input[type="email"]');
 // Facebook's app-grid Menu is outside the Page main. Restrict this fallback
 // to its exact Menu -> Create column, after the caller verifies the actor.
 let appMenu=null,createColumn=null;
 if(pageActorVerified&&!reelContext&&menuDialog){
  // The production menu also uses styled text spans for these titles. Bind
  // exact text leaves to the same bounded overlay instead of requiring h tags.
  const titles=new Set(headings);
  const titleWalker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  for(let node=titleWalker.nextNode();node;node=titleWalker.nextNode()){
   const element=node.parentElement;
   if(/^(?:menu|trình đơn|create|créer|tạo)$/i.test(clean(node.textContent))&&visible(element)&&!excluded(element)&&!element.closest('button,[role="button"],a[href],[role="menuitem"]'))titles.add(element);
  }
  const menus=[...titles].filter(e=>named(e,/^(?:menu|trình đơn)$/i));
  const creates=[...titles].filter(e=>named(e,/^(?:create|créer|tạo)$/i));
  for(const title of creates){
   let column=title.parentElement;
   for(let depth=0;column&&depth<24;depth++,column=column.parentElement){
    if(column.matches('html,body,main,[role="main"],[role="banner"]')||excluded(column))break;
    if(menus.some(menu=>column.contains(menu)))break;
    if(column.querySelector('input,textarea,[contenteditable]:not([contenteditable="false"])'))break;
    if(column.querySelectorAll('*').length>5000||clean(column.innerText).length>6000)break;
    const entries=[...column.querySelectorAll('button,[role="button"],[role="menuitem"],[role="link"],a[href]')].filter(e=>visible(e)&&!excluded(e)&&named(e,/^reel$/i)&&(!e.matches('a[href]')||createLink(e)));
    if(!entries.length)continue;
    let root=column.parentElement;
    for(let up=0;root&&up<24;up++,root=root.parentElement){
     if(root.matches('html,body,main,[role="main"],[role="banner"]')||excluded(root))break;
     if(root.querySelector('main,[role="main"],[role="banner"],header,textarea,[contenteditable]:not([contenteditable="false"]),input[type="file"],input[type="password"],input[type="email"]'))break;
     if(root.querySelectorAll('*').length>12000||clean(root.innerText).length>16000)break;
     if(!menus.some(menu=>root.contains(menu)))continue;
     if(topDialog&&topDialog!==root&&!root.contains(topDialog)&&!topDialog.contains(root))break;
     // Do not accept a background menu covered by an unrelated modal.
     if(topDialog&&!menus.some(menu=>topDialog.contains(menu)))break;
     appMenu=root;createColumn=column;break;
    }
    if(appMenu)break;
   }
   if(appMenu)break;
  }
 }
 for(const e of document.querySelectorAll('[data-cm-reel]'))e.removeAttribute('data-cm-reel');
 let sequence=0;const mark=e=>{if(!e.hasAttribute('data-cm-reel'))e.setAttribute('data-cm-reel',String(sequence++));return e.getAttribute('data-cm-reel')};
 const publishLabel=/^(?:publish(?: reel)?|share(?: now| reel)?|đăng(?: thước phim)?|chia sẻ(?: ngay| thước phim)?|publier(?: le reel)?)$/i;
 const buttons=[];
 for(const e of (appMenu?createColumn:scope).querySelectorAll('button,[role="button"],[role="menuitem"],a[href]')){
  if(!visible(e)||excluded(e))continue;
  if(e.matches('a[href]')&&(reelContext||!createLink(e)))continue;
  const name=label(e),enabled=!e.disabled&&e.getAttribute('aria-disabled')!=='true';let kind='';
  if(pageActorVerified&&!reelContext&&!topDialog&&(named(e,/^(?:create (?:a )?reel|tạo thước phim|créer un reel)$/i)||main?.contains(e)&&named(e,/^(?:reel|thước phim)$/i)))kind='create';
  else if(reelContext&&named(e,/^(?:add video|upload video|select video|choose video|thêm video|tải video lên|chọn video|(?:ajouter|importer) une vidéo(?: ou faire un glisser[-‑]déposer)?)$/i))kind='upload';
  else if(reelContext&&named(e,/^(?:next|tiếp|tiếp tục|suivant)$/i))kind='next';
  else if(reelContext&&named(e,publishLabel))kind='publish';
  if(kind)buttons.push({id:mark(e),kind,label:name,enabled});
 }
 if(createColumn){
  for(const e of createColumn.querySelectorAll('button,[role="button"],[role="menuitem"],[role="link"],a[href]')){
   if(!visible(e)||excluded(e)||!named(e,/^reel$/i)||e.matches('a[href]')&&!createLink(e))continue;
   if(buttons.some(item=>item.id===e.getAttribute('data-cm-reel')))continue;
   buttons.push({id:mark(e),kind:'create',label:label(e),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'});
  }
 }
 if(pageActorVerified&&!reelContext&&!topDialog&&!appMenu){
  for(const e of document.querySelectorAll('[role="banner"] button,[role="banner"] [role="button"],header button,header [role="button"]')){
   if(visible(e)&&!excluded(e)&&named(e,/^(?:menu|facebook menu|menu facebook|trình đơn)$/i))buttons.push({id:mark(e),kind:'app_menu',label:label(e),enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'});
  }
 }
 const captions=[];
 if(reelContext)for(const e of scope.querySelectorAll('textarea,input,[contenteditable]:not([contenteditable="false"])')){
  if(!visible(e)||e.disabled||e.readOnly||excluded(e)||e.type==='file')continue;
  const labels=[label(e),e.placeholder,...[...(e.labels||[])].map(x=>clean(x.innerText))].filter(Boolean).join(' ');
  if(!/caption|description|describe your reel|say something about (?:this|your) reel|mô tả|chú thích|décrivez|légende/i.test(labels))continue;
  const value=e.isContentEditable?e.innerText:e.value;
  captions.push({id:mark(e),matches:(value||'').replace(/\r\n/g,'\n')===(caption||'').replace(/\r\n/g,'\n')});
 }
 const files=[];
 if(reelContext)for(const e of scope.querySelectorAll('input[type="file"]')){
  if(e.disabled||e.closest('[inert],[aria-hidden="true"]')||excluded(e))continue;
  const accept=(e.accept||'').toLowerCase();if(accept&&!/video|\.mp4|\.mov|\.webm|\.m4v/.test(accept))continue;
  files.push({id:mark(e)});
 }
 // Facebook can keep multiple hidden video inputs in one Reel composer.
 // A single recognized Upload button supplies its exact native file chooser;
 // do not guess which hidden input belongs to that visible control.
 const useUploadChooser=files.length>1&&buttons.filter(item=>item.kind==='upload'&&item.enabled).length===1;
 const directFiles=useUploadChooser?[]:files;
 const processing=[...scope.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].some(e=>visible(e)&&!excluded(e))
  ||headings.some(e=>/^(?:uploading|processing(?: video)?|đang tải lên|đang xử lý|traitement|importation)(?:[.\s\d%…]|$)/i.test(label(e)));
 const videoReady=[...scope.querySelectorAll('video')].some(e=>visible(e)&&!excluded(e)&&e.readyState>=2);
 const uploadReady=!processing&&(videoReady||buttons.some(e=>e.enabled&&e.kind==='next')||captions.length>0&&buttons.some(e=>e.enabled&&e.kind==='publish'));
 const authors=[];
 const target=profileUrl(pageUrl),targetName=norm(pageName);
 const currentReelUrl=reelUrl(location.href);
 const viewVideos=currentReelUrl?[...scope.querySelectorAll('video')].filter(e=>{
  if(!visible(e))return false;
  const rect=e.getBoundingClientRect();
  return rect.width>0&&rect.height>0&&rect.right>0&&rect.bottom>0&&rect.left<innerWidth&&rect.top<innerHeight;
 }):[];
 const reelBoundary='article,[role="article"],[role="dialog"],dialog,main,[role="main"]';
 const reelExcludedRegion='aside,nav,[role="complementary"],[role="navigation"],[role="menu"],[role="tooltip"]';
 const reelViewCandidate=viewVideos.length===1?viewVideos[0].closest(reelBoundary):null;
 const reelView=reelViewCandidate&&scope.contains(reelViewCandidate)?reelViewCandidate:null;
 // Permalink evidence belongs to the video's own bounded viewer, never a
 // sibling recommendation, nested comment article or unrelated overlay.
 const reelBaseEvidence=e=>!!reelView&&visible(e)&&!e.closest(reelExcludedRegion)&&e.closest(reelBoundary)===reelView;
 const commentComposers=[],commentPrompts=new Set(),commentAttributePrompts=new Set(),commentTextPrompts=new Set(),commentRejectedLabels=new Set(),commentRejectedGeometry=new Set();
 const commentField='textarea,input:not([type="hidden"]):not([type="file"]):not([type="button"]):not([type="submit"]),[role="textbox"],[contenteditable]:not([contenteditable="false"])';
 // Lexical's visible placeholder may be aria-hidden for accessibility only.
 // This exception classifies the comment editor; author/caption visibility is
 // still governed by visible() and never accepts aria-hidden evidence.
 const commentRendered=e=>{
  if(!e?.getClientRects().length||e.closest('[hidden],[inert]'))return false;
  const box=e.getBoundingClientRect();
  if(box.width<=0||box.height<=0||box.right<=0||box.bottom<=0||box.left>=innerWidth||box.top>=innerHeight)return false;
  for(let node=e;node;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0)return false}
  return true;
 };
 const commentBaseEvidence=e=>!!reelView&&commentRendered(e)&&!e.closest(reelExcludedRegion)&&e.closest(reelBoundary)===reelView;
 let commentRenderedFieldCount=0;
 const commentNames=new Set(targetName?['commenter en tant que '+targetName,'comment as '+targetName,'bình luận dưới tên '+targetName,'bình luận với tư cách '+targetName]:[]);
 const commentPrompt=value=>commentNames.has(norm(value));
 const commentAvatarName=value=>{
  const name=norm(value);
  return !name||name===targetName||commentNames.has(name)||/^(?:profile (?:picture|photo)|page (?:picture|photo)|photo de profil|image de profil|ảnh đại diện|switch (?:profile|account)|change (?:profile|account)|changer de profil|changer de compte|changer d['’]identité|chuyển trang cá nhân|chuyển tài khoản)$/.test(name)
   ||['photo de profil de '+targetName,'ảnh đại diện của '+targetName,targetName+"'s profile picture",targetName+'’s profile picture'].includes(name);
 };
 if(currentReelUrl&&reelView&&targetName){
  for(const field of reelView.querySelectorAll(commentField)){
   if(!commentBaseEvidence(field))continue;
   commentRenderedFieldCount++;
   // A populated comment editor remains a comment editor. Attribute values
   // identify its purpose without treating the typed content as a prompt.
   if(['placeholder','data-placeholder','aria-placeholder','aria-label'].some(attribute=>commentPrompt(field.getAttribute(attribute)))){commentPrompts.add(field);commentAttributePrompts.add(field)}
  }
  for(const placeholder of reelView.querySelectorAll('[placeholder],[data-placeholder],[aria-placeholder]')){
   if(commentBaseEvidence(placeholder)&&['placeholder','data-placeholder','aria-placeholder'].some(attribute=>commentPrompt(placeholder.getAttribute(attribute)))){commentPrompts.add(placeholder);commentAttributePrompts.add(placeholder)}
  }
  const walker=document.createTreeWalker(reelView,NodeFilter.SHOW_TEXT);
  for(let text=walker.nextNode();text;text=walker.nextNode()){
   const element=text.parentElement;
   if(commentPrompt(text.textContent)&&commentBaseEvidence(element)&&!element.closest('textarea,input,[role="textbox"],[contenteditable]:not([contenteditable="false"])')){commentPrompts.add(element);commentTextPrompts.add(element)}
  }
  for(const prompt of commentPrompts){
   for(let node=prompt.parentElement,depth=0;node&&node!==reelView&&depth<24;node=node.parentElement,depth++){
    if(node.matches(reelBoundary+',html,body')||node.querySelector('video,h1,h2,h3,h4,h5,h6,[role="heading"],'+reelBoundary))break;
    const box=node.getBoundingClientRect();
    if(!commentBaseEvidence(node)||box.width<=0||box.height<=0||box.width>640||box.height>220||box.right<=0||box.bottom<=0||box.left>=innerWidth||box.top>=innerHeight){commentRejectedGeometry.add(node);continue}
    const fields=[...node.querySelectorAll(commentField)].filter(field=>commentBaseEvidence(field)&&field.getBoundingClientRect().width>=80&&field.getBoundingClientRect().height>=20);
    if(!fields.length)continue;
    const avatars=[...node.querySelectorAll('button,[role="button"],a[href]')].filter(control=>reelBaseEvidence(control)&&[...control.querySelectorAll('img,svg[role="img"] image')].some(visible));
    if(!avatars.length)continue;
    if([...node.querySelectorAll('a[href]')].some(link=>reelBaseEvidence(link)&&profileUrl(link.getAttribute('href'))&&profileUrl(link.getAttribute('href'))!==target))break;
    const conflicting=avatars.filter(control=>!commentAvatarName(clean(control.innerText)||label(control))||[...control.querySelectorAll('img')].filter(visible).some(img=>!commentAvatarName(img.getAttribute('alt'))));
    if(conflicting.length){for(const control of conflicting)commentRejectedLabels.add(control);break}
    const avatarPictures=[...new Set(avatars.flatMap(control=>[...control.querySelectorAll('img,svg[role="img"] image')].filter(visible)))];
    // A second standalone image identity must not be hidden merely because
    // one neighboring avatar belongs to the comment editor.
    if(avatarPictures.length!==1){commentRejectedGeometry.add(node);break}
    const adjacent=avatarPictures.some(picture=>{
     const avatar=picture.getBoundingClientRect();
     return avatar.width>0&&avatar.height>0&&avatar.width<=96&&avatar.height<=96&&fields.some(field=>{
      const entry=field.getBoundingClientRect(),gap=Math.max(0,entry.left-avatar.right,avatar.left-entry.right);
      return avatar.left<=entry.left&&gap<=96&&Math.abs((avatar.top+avatar.bottom-entry.top-entry.bottom)/2)<=64;
     });
    });
    if(!adjacent){commentRejectedGeometry.add(node);continue}
    // Keep the smallest shared row; never discard a whole Reel/right panel.
    if(!commentComposers.some(region=>node.contains(region))){
     for(let index=commentComposers.length-1;index>=0;index--)if(commentComposers[index].contains(node))commentComposers.splice(index,1);
     commentComposers.push(node);
    }
    break;
   }
  }
 }
 const reelEvidence=e=>reelBaseEvidence(e)&&!commentComposers.some(region=>region.contains(e));
 const commentExcludedAvatars=new Set(commentComposers.flatMap(region=>[...region.querySelectorAll('button,[role="button"],a[href]')].filter(control=>reelBaseEvidence(control)&&[...control.querySelectorAll('img,svg[role="img"] image')].some(visible))));
 const permalinkName=e=>{
  const text=clean(e.innerText);
  // A verbose accessibility label must not override a rendered Page name.
  // Only an image-only author anchor may supply its name through that label.
  return text||e.matches('a[href]')&&[...e.querySelectorAll('img,svg[role="img"]')].some(visible)&&label(e)||'';
 };
 const reelActionName=/^(?:like|comment|share|send|post comment|reply|close|play|pause|mute|unmute|more|more options|options|save|save reel|full ?screen|exit full ?screen|next|previous|add emoji|choose an emoji|insert emoji|attach (?:a )?photo or video|send a gif|attach a gif|add a sticker|j['’]aime|commenter|partager|envoyer|publier un commentaire|répondre|fermer|lire|lecture|mettre en pause|couper le son|activer le son|plus|plus d['’]options|enregistrer|enregistrer le reel|plein écran|quitter le plein écran|suivant|précédent|ajouter un emoji|choisir un emoji|insérer un emoji|joindre une photo ou une vidéo|ajouter une photo ou une vidéo|envoyer un gif|ajouter un sticker)$/i;
 const reelProfileIdentities=new Set();
 let reelUnlinkedAvatarCount=0,reelConflictingAvatarCount=0;
 let reelImageButtonCount=0,reelSvgButtonCount=0,reelSvgImageButtonCount=0,reelSvgIconButtonCount=0;
 let reelKnownActionButtonCount=0,reelExcludedActionButtonCount=0,reelAvatarInFormCount=0,reelAvatarNearTextboxCount=0;
 let reelNestedProfileLinkCount=0,reelNestedTargetLinkCount=0,reelImageAltTargetCount=0,reelImageAltOtherCount=0,reelImageEmptyAltCount=0,reelWrapperExactTargetNameCount=0;
 const actorScope=composer||reelView||scope;
 const composerIdentityConflicts=new Set();
 const composerIdentityContent='textarea,input,[contenteditable]:not([contenteditable="false"]),[role="textbox"],article,[role="article"],nav,[role="navigation"],[role="menu"],[role="tooltip"]';
 const composerProfileLinks=!currentReelUrl&&reelContext?[...actorScope.querySelectorAll('a[href]')].filter(link=>
  visible(link)&&!excluded(link)&&!link.closest(composerIdentityContent)&&profileUrl(link.getAttribute('href'))&&profileUrl(link.getAttribute('href'))!==target):[];
 for(const link of composerProfileLinks){
  // A small named profile-image anchor is explicit composer identity even
  // when this step has no target-name avatar. Ordinary tagged body links are
  // not author rows and cannot contradict the independently verified Page.
  if(link.closest('p,figure,[role="listitem"]'))continue;
  const box=link.getBoundingClientRect(),name=label(link);
  if(!name||name.length>160||box.width<=0||box.height<=0||box.width>640||box.height>160)continue;
  const avatar=[...link.querySelectorAll('img,svg[role="img"]')].some(image=>{
   if(!visible(image))return false;
   const rect=image.getBoundingClientRect();
   return rect.width>=16&&rect.height>=16&&rect.width<=96&&rect.height<=96&&rect.width/rect.height>=.67&&rect.width/rect.height<=1.5;
  });
  if(avatar)composerIdentityConflicts.add(link);
 }
 const sharedActorRow=(avatar,link)=>{
  const a=avatar.getBoundingClientRect(),b=link.getBoundingClientRect();
  if(a.width<=0||a.height<=0||b.width<=0||b.height<=0||Math.abs((a.top+a.bottom-b.top-b.bottom)/2)>48||Math.max(a.left,b.left)-Math.min(a.right,b.right)>96||Math.max(a.right,b.right)-Math.min(a.left,b.left)>640||Math.max(a.bottom,b.bottom)-Math.min(a.top,b.top)>160)return false;
  if(avatar.nextElementSibling===link||avatar.previousElementSibling===link)return true;
  for(let row=avatar.parentElement,depth=0;row&&row!==actorScope&&depth<24;row=row.parentElement,depth++){
   if(!row.contains(link))continue;
   const box=row.getBoundingClientRect();
   if(row.matches('main,[role="main"],[role="dialog"],dialog')||box.width<=0||box.height<=0||box.width>640||box.height>160)return false;
   if(row.querySelector(composerIdentityContent+',video,h1,h2,h3,[role="heading"],input[type="file"]'))return false;
   if([...row.querySelectorAll('button,[role="button"]')].some(control=>named(control,publishLabel)||named(control,/^(?:next|tiếp|tiếp tục|suivant)$/i)))return false;
   return true;
  }
  return false;
 };
 const bareComposerActor=(avatar)=>{
  const ancestor=avatar.closest('a[href]');
  if(ancestor&&profileUrl(ancestor.getAttribute('href'))!==target){composerIdentityConflicts.add(ancestor);return false}
  for(const link of composerProfileLinks){
   // A tagged profile in the caption or a separate body link is not an actor.
   // An adjacent avatar/name pair or compact shared author row is.
   if(sharedActorRow(avatar,link))composerIdentityConflicts.add(link);
  }
  return composerIdentityConflicts.size===0;
 };
 for(const e of actorScope.querySelectorAll('a[href],button,[role="button"]')){
  if(!visible(e)||(currentReelUrl?!reelEvidence(e):excluded(e)))continue;
  if(!currentReelUrl&&reelContext&&e.closest(composerIdentityContent))continue;
  const name=label(e),url=profileUrl(e.getAttribute('href')||'');
  if(currentReelUrl&&url)reelProfileIdentities.add(url);
  if(currentReelUrl&&!e.hasAttribute('href')&&e.matches('button,[role="button"]')){
   const images=[...e.querySelectorAll('img')].filter(visible),svgs=[...e.querySelectorAll('svg[role="img"]')].filter(visible);
   if(images.length||svgs.length){
    const svgImage=svgs.some(svg=>[...svg.querySelectorAll('image')].some(visible));
    const text=clean(e.innerText),knownAction=reelActionName.test(name)&&(!text||reelActionName.test(text));
    const ancestorLink=e.closest('a[href]'),ancestorUrl=ancestorLink&&reelView.contains(ancestorLink)&&reelEvidence(ancestorLink)?profileUrl(ancestorLink.getAttribute('href')):'';
    const form=e.closest('form,[role="form"]');
    let nearTextbox=false,wrapperExactName=false;
    for(let node=e.parentElement,depth=0;node&&node!==reelView&&depth<3;node=node.parentElement,depth++){
     if(node.matches(reelBoundary)||node.querySelector('video'))break;
     if(targetName&&norm(node.innerText)===targetName)wrapperExactName=true;
     if([...node.querySelectorAll('textarea,input:not([type="hidden"]),[contenteditable]:not([contenteditable="false"]),[role="textbox"]')].some(visible))nearTextbox=true;
    }
    if(images.length)reelImageButtonCount++;
    if(svgs.length)reelSvgButtonCount++;
    if(svgImage)reelSvgImageButtonCount++;
    if(svgs.length&&!svgImage)reelSvgIconButtonCount++;
    if(knownAction)reelKnownActionButtonCount++;
    if(form&&reelView.contains(form)&&!form.querySelector('video'))reelAvatarInFormCount++;
    if(nearTextbox)reelAvatarNearTextboxCount++;
    if(ancestorUrl)reelNestedProfileLinkCount++;
    if(ancestorUrl===target&&ancestorUrl)reelNestedTargetLinkCount++;
    for(const image of images){const alt=norm(image.getAttribute('alt'));if(!alt)reelImageEmptyAltCount++;else if(targetName&&alt===targetName)reelImageAltTargetCount++;else reelImageAltOtherCount++}
    if(wrapperExactName)reelWrapperExactTargetNameCount++;
    // A button inside an eligible public profile anchor is the same linked
    // identity. Its parent still must pass exact-name and conflict checks.
    if(ancestorUrl)continue;
    // An exact UI action backed only by a vector icon is not an identity.
    // Raster/embedded-image buttons and unknown SVG identities stay ambiguous.
    if(!images.length&&!svgImage&&svgs.length&&knownAction)reelExcludedActionButtonCount++;
    else{
     reelUnlinkedAvatarCount++;
     if(name&&norm(name)!==targetName)reelConflictingAvatarCount++;
    }
   }
  }
  if(url===target&&url&&(currentReelUrl?!!targetName&&norm(permalinkName(e))===targetName:!targetName||norm(name)===targetName))authors.push({url,name});
  else if(!currentReelUrl&&targetName&&norm(name)===targetName&&e.matches('button,[role="button"]')&&e.querySelector('img,svg[role="img"]')&&!e.hasAttribute('href')&&bareComposerActor(e))authors.push({url:'',name});
 }
 // Plain-div comments have no reliable semantic boundary. Another eligible
 // profile or an unlinked avatar can be the real author, while an exact-target
 // href belongs to a comment. Leave that ownership ambiguity unconfirmed.
 const reelIdentityConflict=reelProfileIdentities.size>1||reelUnlinkedAvatarCount>0;
 // The final French settings modal omits an author row. Its backdrop still
 // renders the acting Page sidebar, aria-hidden/inert only for modality.
 // Bind that narrow rendered sidebar to the already-verified actor and the
 // unchanged exact Page URL; never infer identity from a viewed Page alone.
 const rendered=e=>{
  if(!e?.getClientRects().length||e.closest('[hidden]'))return false;
  const rect=e.getBoundingClientRect();
  if(rect.right<=0||rect.bottom<=0||rect.left>=innerWidth||rect.top>=innerHeight)return false;
  for(let node=e;node;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0)return false}
  return true;
 };
 let pageContextActor=false;
 let actorManagedContexts=0,actorSidebarNames=0,actorLinkedConflicts=0;
 if(pageActorVerified&&reelContext&&composer&&profileUrl(location.href)===target&&targetName&&!currentReelUrl){
  const manage=/^(?:manage page|quản lý trang|gérer la page)$/i;
  for(const heading of document.querySelectorAll('h1,h2,h3,[role="heading"]')){
   if(composer.contains(heading)||excluded(heading)||!rendered(heading)||!manage.test(clean(heading.innerText)))continue;
   if(heading.closest('[role="dialog"],[role="menu"],[role="tooltip"]'))continue;
   const headingBox=heading.getBoundingClientRect();let sidebar=heading.parentElement;
   for(let depth=0;sidebar&&depth<24;depth++,sidebar=sidebar.parentElement){
    if(sidebar.matches('html,body,main,[role="main"]'))break;
    const box=sidebar.getBoundingClientRect(),semantic=sidebar.matches('aside,nav,[role="navigation"]');
    if(box.left>Math.max(64,innerWidth*.08)||box.width>Math.min(560,innerWidth*(semantic ? 0.8 : 0.55)))continue;
    const links=[...sidebar.querySelectorAll('a[href]')].filter(link=>{
     if(!rendered(link)||excluded(link)||link.closest('[role="dialog"],[role="menu"],[role="tooltip"]'))return false;
     const rect=link.getBoundingClientRect();
     return rect.top>=headingBox.bottom-4&&rect.top<=headingBox.bottom+200&&profileUrl(link.href)===target;
    });
    const names=[...new Set(links.map(link=>norm(link.innerText)||norm(label(link))).filter(Boolean))];
    if(names.length===1&&names[0]===targetName){pageContextActor=true;break}
   }
   if(pageContextActor)break;
  }
  // Some Page sidebars render their identity as plain text, not a link.
  // The exact profile header supplies the name only when a distinct left
  // Manage Page heading also proves acting-Page UI on the unchanged Page URL.
  if(!pageContextActor){
   const managedContexts=[];
   const managed=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].some(heading=>{
    if(composer.contains(heading)||excluded(heading)||!rendered(heading)||!manage.test(clean(heading.innerText)))return false;
    if(heading.closest('[role="dialog"],[role="menu"],[role="tooltip"]'))return false;
    const box=heading.getBoundingClientRect();
    if(box.left>Math.max(64,innerWidth*.08)||box.width>Math.min(560,innerWidth*.55))return false;
    // Use the header alternative only for a linkless identity card. Do not
    // override a conflicting, misplaced, or hidden linked Page identity.
    let sidebar=heading.parentElement;
    for(let depth=0;sidebar&&depth<24;depth++,sidebar=sidebar.parentElement){
     if(sidebar.matches('html,body,main,[role="main"]'))break;
     const bounds=sidebar.getBoundingClientRect(),semantic=sidebar.matches('aside,nav,[role="navigation"]');
     if(bounds.left>Math.max(64,innerWidth*.08)||bounds.width>Math.min(560,innerWidth*(semantic ? 0.8 : 0.55)))continue;
     const linkedIdentity=[...sidebar.querySelectorAll('a[href]')].some(link=>{
      if(excluded(link)||link.closest('[role="dialog"],[role="menu"],[role="tooltip"]'))return false;
      const url=profileUrl(link.href);if(!url)return false;
      const rect=link.getBoundingClientRect(),near=rect.top>=box.bottom-4&&rect.top<=box.bottom+96;
      const navigation=named(link,/^(?:professional dashboard|business dashboard|tableau de bord professionnel|bảng điều khiển chuyên nghiệp)$/i);
      return /\/profile\.php\?id=\d+$/.test(url)||url===target||norm(link.innerText)===targetName||near&&!navigation;
     });
     if(linkedIdentity){actorLinkedConflicts++;return false}
    }
    managedContexts.push(heading);
    actorManagedContexts++;
    return true;
   });
   const profileHeadings=[...document.querySelectorAll('main h1,main h2,main h3,[role="main"] h1,[role="main"] h2,[role="main"] h3,main [role="heading"],[role="main"] [role="heading"]')].filter(heading=>
    !composer.contains(heading)&&!excluded(heading)&&!heading.closest('[role="dialog"],[role="menu"],[role="tooltip"]')&&rendered(heading));
   const sidebarNameHeadings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(heading=>{
    if(composer.contains(heading)||excluded(heading)||!rendered(heading)||norm(heading.innerText)!==targetName)return false;
    if(heading.closest('[role="dialog"],[role="menu"],[role="tooltip"]'))return false;
    const box=heading.getBoundingClientRect();
    return managedContexts.some(manageHeading=>{
     const anchor=manageHeading.getBoundingClientRect();
     if(box.top<anchor.bottom-4||box.top>anchor.bottom+200||box.left>Math.min(560,innerWidth*.55))return false;
     let sidebar=manageHeading.parentElement;
     for(let depth=0;sidebar&&depth<24;depth++,sidebar=sidebar.parentElement){
      if(sidebar.matches('html,body,main,[role="main"]'))break;
      const rect=sidebar.getBoundingClientRect(),semantic=sidebar.matches('aside,nav,[role="navigation"]');
      if(rect.left>Math.max(64,innerWidth*.08)||rect.width>Math.min(560,innerWidth*(semantic ? 0.8 : 0.55)))continue;
      if(sidebar.contains(heading))return true;
     }
     return false;
    });
   });
   actorSidebarNames=sidebarNameHeadings.length;
   pageContextActor=managed&&(profileHeadings.filter(heading=>norm(heading.innerText)===targetName).length===1||sidebarNameHeadings.length===1);
  }
 }
 const actorMatches=(authors.length>0||pageContextActor)&&!composerIdentityConflicts.size&&(!currentReelUrl||!!reelView&&!reelIdentityConflict);
 const successNodes=[...document.querySelectorAll('h1,h2,h3,[role="heading"],[role="alert"],[role="status"]')].filter(e=>visible(e)&&!excluded(e)&&successPattern.test(label(e)));
 const published=successNodes.length>0;
 const urls=[];
 for(const node of successNodes){
  const container=node.closest('[role="dialog"],[role="alert"],[role="status"]')||node.parentElement;
  for(const link of container?.querySelectorAll('a[href]')||[]){if(visible(link)){const url=reelUrl(link.href);if(url&&!urls.includes(url))urls.push(url)}}
 }
 const publishedCaptionMatches=!!caption&&!!reelView&&!reelIdentityConflict&&[...reelView.querySelectorAll('p,span,div')].some(e=>
  reelEvidence(e)&&clean(e.innerText)===clean(caption)&&!e.closest('[contenteditable],textarea')
  // Aggregate wrapper text must not re-import an excluded comment's caption.
  &&!e.querySelector(reelBoundary+','+reelExcludedRegion+',[contenteditable],textarea')&&!commentComposers.some(region=>e.contains(region)));
 // Numeric developer diagnostics only; never return raw page/body/field data.
 const allPublish=[...document.querySelectorAll('button,[role="button"],[role="menuitem"],a[href]')].filter(e=>visible(e)&&!excluded(e)&&named(e,publishLabel));
 const uiDiagnostics={scope_comment_exact_prompt_count:commentPrompts.size,scope_comment_rendered_field_count:commentRenderedFieldCount,
  scope_comment_attribute_prompt_count:commentAttributePrompts.size,scope_comment_rendered_text_prompt_count:commentTextPrompts.size,
  scope_comment_a11y_hidden_prompt_count:[...commentPrompts].filter(e=>e.closest('[aria-hidden="true"]')).length,scope_comment_composer_count:commentComposers.length,
  scope_comment_excluded_avatar_count:commentExcludedAvatars.size,scope_comment_rejected_label_count:commentRejectedLabels.size,scope_comment_rejected_geometry_count:commentRejectedGeometry.size,
  scope_comment_rejected_profile_word_count:[...commentRejectedLabels].filter(e=>/profile|picture|photo|profil|image|avatar|ảnh đại diện/i.test(labels(e).join(' '))).length,
  scope_comment_rejected_switch_word_count:[...commentRejectedLabels].filter(e=>/switch|change|choose|select|changer|choisir|sélectionner|chuyển|chọn/i.test(labels(e).join(' '))).length,
  scope_comment_rejected_comment_word_count:[...commentRejectedLabels].filter(e=>/comment|bình luận/i.test(labels(e).join(' '))).length,
  scope_comment_rejected_target_text_count:[...commentRejectedLabels].filter(e=>!!targetName&&norm(e.innerText)===targetName).length,
  dialog_count:dialogs.length,scope_is_dialog:!!topDialog,
  all_publish_count:allPublish.length,outside_publish_count:allPublish.filter(e=>!scope.contains(e)).length,
  enabled_publish_count:allPublish.filter(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true').length,
  scope_current_reel:!!currentReelUrl,scope_reel_video_count:viewVideos.length,scope_reel_view_found:!!reelView,
  scope_reel_author_count:authors.length,scope_reel_caption_matches:publishedCaptionMatches,
  scope_reel_video_in_dialog_count:viewVideos.filter(e=>e.closest('[role="dialog"],dialog')).length,
  scope_reel_video_in_main_count:viewVideos.filter(e=>e.closest('main,[role="main"]')).length};
 const viewerProfileLinks=reelView?[...reelView.querySelectorAll('a[href]')].filter(e=>visible(e)&&profileUrl(e.getAttribute('href'))):[];
 const eligibleProfileLinks=viewerProfileLinks.filter(reelEvidence);
 Object.assign(uiDiagnostics,{
  scope_reel_eligible_profile_link_count:eligibleProfileLinks.length,
  scope_reel_target_href_count:eligibleProfileLinks.filter(e=>profileUrl(e.getAttribute('href'))===target).length,
  scope_reel_target_name_count:eligibleProfileLinks.filter(e=>!!targetName&&norm(permalinkName(e))===targetName).length,
  scope_reel_target_text_name_count:eligibleProfileLinks.filter(e=>!!targetName&&norm(e.innerText)===targetName).length,
  scope_reel_target_labelled_count:eligibleProfileLinks.filter(e=>profileUrl(e.getAttribute('href'))===target&&(e.hasAttribute('aria-label')||e.hasAttribute('aria-labelledby'))).length,
  scope_reel_target_href_in_viewer_count:viewerProfileLinks.filter(e=>profileUrl(e.getAttribute('href'))===target).length,
  scope_reel_link_label_mismatch_count:eligibleProfileLinks.filter(e=>profileUrl(e.getAttribute('href'))===target&&(!targetName||norm(label(e))!==targetName)).length,
  scope_reel_profile_identity_count:reelProfileIdentities.size,
  scope_reel_unlinked_avatar_count:reelUnlinkedAvatarCount,
  scope_reel_conflicting_avatar_count:reelConflictingAvatarCount,
  scope_reel_image_button_count:reelImageButtonCount,
  scope_reel_svg_button_count:reelSvgButtonCount,
  scope_reel_svg_image_button_count:reelSvgImageButtonCount,
  scope_reel_svg_icon_button_count:reelSvgIconButtonCount,
  scope_reel_known_action_button_count:reelKnownActionButtonCount,
  scope_reel_excluded_action_button_count:reelExcludedActionButtonCount,
  scope_reel_avatar_in_form_count:reelAvatarInFormCount,
  scope_reel_avatar_near_textbox_count:reelAvatarNearTextboxCount,
  scope_reel_nested_profile_link_count:reelNestedProfileLinkCount,
  scope_reel_nested_target_link_count:reelNestedTargetLinkCount,
  scope_reel_image_alt_target_count:reelImageAltTargetCount,
  scope_reel_image_alt_other_count:reelImageAltOtherCount,
  scope_reel_image_empty_alt_count:reelImageEmptyAltCount,
  scope_reel_wrapper_exact_target_name_count:reelWrapperExactTargetNameCount
 });
 const menuReels=[...document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="link"],a[href]')].filter(e=>visible(e)&&!excluded(e)&&named(e,/^reel$/i));
 Object.assign(uiDiagnostics,{menu_context_found:!!appMenu,menu_create_column_found:!!createColumn,
  menu_reel_action_count:menuReels.length,menu_reel_role_link_count:menuReels.filter(e=>e.matches('[role="link"]:not(a[href])')).length,
  menu_reel_known_href_count:menuReels.filter(e=>e.matches('a[href]')&&createLink(e)).length,
  menu_reel_button_count:menuReels.filter(e=>e.matches('button,[role="button"],[role="menuitem"]')).length});
 const actorHeadings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(e=>rendered(e)&&!excluded(e));
 const management=actorHeadings.filter(e=>/^(?:manage page|quản lý trang|gérer la page)$/i.test(clean(e.innerText)));
 const profileHeaders=actorHeadings.filter(e=>e.matches('h1,[role="heading"][aria-level="1"]'));
 Object.assign(uiDiagnostics,{
  actor_latch_verified:pageActorVerified===true,actor_route_matches:profileUrl(location.href)===target,
  actor_page_route:/^\/profile\.php$/.test(location.pathname),actor_reel_create_route:/^\/reels?\/create\/?$/.test(location.pathname),
  actor_composer_found:!!composer,actor_composer_conflicting_link_count:composerIdentityConflicts.size,actor_manage_rendered_count:management.length,
  actor_managed_context_count:actorManagedContexts,actor_sidebar_name_count:actorSidebarNames,actor_linked_conflict_count:actorLinkedConflicts,
  actor_manage_a11y_count:management.filter(visible).length,
  actor_manage_in_composer_count:management.filter(e=>composer?.contains(e)).length,
  actor_exact_header_rendered_count:profileHeaders.filter(e=>norm(e.innerText)===targetName).length,
  actor_exact_header_in_main_count:profileHeaders.filter(e=>e.closest('main,[role="main"]')&&norm(e.innerText)===targetName).length,
  actor_exact_heading_in_main_count:actorHeadings.filter(e=>e.closest('main,[role="main"]')&&norm(e.innerText)===targetName).length,
  actor_exact_heading_rendered_count:actorHeadings.filter(e=>norm(e.innerText)===targetName).length,
  actor_bound_links_rendered_count:[...document.querySelectorAll('a[href]')].filter(e=>rendered(e)&&!excluded(e)&&profileUrl(e.href)===target&&norm(e.innerText)===targetName).length
 });
 const exactNames=actorHeadings.filter(e=>norm(e.innerText)===targetName);
 Object.assign(uiDiagnostics,{
  actor_exact_name_left_count:exactNames.filter(e=>e.getBoundingClientRect().left<=Math.min(560,innerWidth*.55)).length,
  actor_exact_name_in_menu_count:exactNames.filter(e=>e.closest('[role="menu"],[role="tooltip"]')).length,
  actor_exact_name_in_dialog_count:exactNames.filter(e=>e.closest('[role="dialog"]')).length,
  actor_exact_name_in_composer_count:exactNames.filter(e=>composer?.contains(e)).length,
  actor_exact_name_near_manage_count:exactNames.filter(e=>management.some(h=>{const r=e.getBoundingClientRect(),a=h.getBoundingClientRect();return r.top>=a.bottom-4&&r.top<=a.bottom+200&&r.left<=Math.min(560,innerWidth*.55)})).length,
  actor_exact_name_link_target_count:exactNames.filter(e=>profileUrl(e.closest('a[href]')?.href)===target).length,
  actor_exact_name_link_conflict_count:exactNames.filter(e=>{const a=e.closest('a[href]');return a&&profileUrl(a.href)!==target}).length,
  actor_exact_name_link_zero_rect_count:exactNames.filter(e=>{const a=e.closest('a[href]');return a&&!a.getClientRects().length}).length,
  actor_manage_allowed_scope_count:management.filter(e=>!composer?.contains(e)&&!e.closest('[role="dialog"],[role="menu"],[role="tooltip"]')).length
 });
 return {blocked:false,reel_context:reelContext,heading:headings.map(label).find(value=>composerTitle.test(value))||'',buttons,captions,files:directFiles,file_input_count:files.length,use_upload_chooser:useUploadChooser,processing,upload_ready:uploadReady,actor_matches:actorMatches,page_context_actor_matches:pageContextActor,published,published_urls:urls,current_reel_url:currentReelUrl,published_caption_matches:publishedCaptionMatches,ui_diagnostics:uiDiagnostics};
}
