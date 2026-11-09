// Structural metadata only. Never return text, titles, form values or mail HTML.
() => {
  const visible=el=>!!(el&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'&&!el.closest('[aria-hidden="true"]'));
  const count=selector=>[...document.querySelectorAll(selector)].filter(visible).length;
  const roleNames=['listbox','grid','list','option','row','treeitem','document','main'];
  const roleCounts=root=>Object.fromEntries(roleNames.map(role=>[role,[...root.querySelectorAll('[role="'+role+'"]')].filter(visible).length]));
  const bodyTags=new Set('div main section article nav header aside ul ol li iframe form input button a span table tr td tbody svg script style'.split(' '));
  const childTags=doc=>{
    const tags={};
    for(const el of [...(doc.body?.children||[])].filter(visible)){
      const name=el.tagName.toLowerCase();const key=bodyTags.has(name)?name:'other';tags[key]=(tags[key]||0)+1;
    }
    return tags;
  };
  const shadowInfo=root=>{
    let open_shadow_root_count=0;
    const shadow_roles=Object.fromEntries(roleNames.map(role=>[role,0]));
    const roots=[root];
    for(let index=0;index<roots.length&&index<100;index++){
      for(const el of [...roots[index].querySelectorAll('*')].slice(0,5000).filter(visible)){
        if(!el.shadowRoot)continue;
        open_shadow_root_count++;
        const counts=roleCounts(el.shadowRoot);for(const role of roleNames)shadow_roles[role]+=counts[role];
        roots.push(el.shadowRoot);
      }
    }
    return {open_shadow_root_count,shadow_roles};
  };
  const roles={};
  for(const role of roleNames)roles[role]=count('[role="'+role+'"]');
  const selectors={
    reading_pane_id:'#ReadingPaneContainerId',
    reading_pane_testid:'[data-testid="reading-pane"]',
    message_reading_pane_testid:'[data-testid="message-reading-pane"]',
    mail_read_compose:'[data-app-section="MailReadCompose"]',
    reading_pane_aria:'[aria-label="Reading Pane" i]',
    message_pane_aria:'[aria-label="Message pane" i]',
    message_list_aria:'[aria-label="Message list" i]',
    message_list_testid:'[data-testid="message-list"]',
    message_body_aria:'[aria-label="Message body" i]',
    message_body_testid:'[data-testid="message-body"]',
    navigation:'[role="navigation"]',tree:'[role="tree"]',
    item_id:'[data-item-id]',conversation_id:'[data-convid]',iframe:'iframe',
  };
  const candidates=Object.fromEntries(Object.entries(selectors).map(([name,selector])=>[name,count(selector)]));
  const folderControls=new Set();
  for(const root of [...document.querySelectorAll('[role="navigation"],[role="tree"]')].filter(visible)){
    for(const el of [...root.querySelectorAll('[role="treeitem"],[role="link"],a,button,[role="button"]')].filter(visible)){
      if(!el.closest('[role="document"],[data-testid="message-body"],[aria-label="Message body" i]'))folderControls.add(el);
    }
  }
  const knownFolders={inbox:'inbox|hộp thư đến|boîte de réception|posteingang|bandeja de entrada',junk:'junk(?: email)?|thư rác|courrier indésirable|spam|correo no deseado'};
  for(const [kind,pattern] of Object.entries(knownFolders)){
    const starts=new RegExp('^(?:'+pattern+')','i');
    const matches=new RegExp('^(?:'+pattern+')(?:\\b|\\s|(?=[0-9]+(?:\\s|$))|$)','i');
    let containsCount=0,matchingCount=0;
    for(const el of folderControls){
      // Known label matching is reduced to counts in memory; no label survives.
      const value=(el.getAttribute('aria-label')||el.innerText||'').replace(/\s+/g,' ').trim();
      if(starts.test(value))containsCount++;
      if(matches.test(value))matchingCount++;
    }
    candidates['folder_'+kind+'_label_contains']=containsCount;
    candidates['folder_'+kind+'_label_match']=matchingCount;
  }
  const words=new Set('reading read pane container id mail message messages list folder folders main root view content body region panel scroll group section document row grid item items tree nav navigation wrapper header focus focused other junk search host app component div inbox layout slot surface details control display thread conversation threaded element area'.split(' '));
  const shape=value=>{
    const parts=(value||'').replace(/([A-Z]+)([A-Z][a-z])/g,'$1-$2').replace(/([a-z])([A-Z])/g,'$1-$2').match(/[A-Za-z]+|[0-9]+/g)||[];
    return parts.slice(0,20).map(part=>/^[0-9]+$/.test(part)?'#':words.has(part.toLowerCase())?part.toLowerCase():'[redacted]').join('-');
  };
  const ids=new Map();
  for(const el of [...document.querySelectorAll('[id]')].filter(visible)){
    if(!/pane|read|list|message|folder/i.test(el.id))continue;
    const key=shape(el.id);if(!key)continue;
    if(ids.has(key))ids.set(key,ids.get(key)+1);
    else if(ids.size<150)ids.set(key,1);
  }
  const iframes=[...document.querySelectorAll('iframe')].filter(visible).slice(0,20).map(frame=>{
    let origin_hostname='';
    try{
      const src=frame.getAttribute('src');
      const url=new URL(src||'about:blank',location.href);
      origin_hostname=url.protocol==='blob:'?new URL(url.pathname).hostname:url.hostname;
      if(!origin_hostname&&(url.protocol==='about:'||frame.hasAttribute('srcdoc')))origin_hostname=location.hostname;
    }catch(_){/* unknown origin remains empty */}
    let readable=false,frame_roles={},body_children={},frame_shadow={open_shadow_root_count:0,shadow_roles:{}};
    try{
      const doc=frame.contentDocument;
      if(doc?.body){readable=true;frame_roles=roleCounts(doc);body_children=childTags(doc);frame_shadow=shadowInfo(doc);}
    }catch(_){/* cross-origin frames expose no document metadata */}
    return {id_shape:shape(frame.id),origin_hostname,readable,roles:frame_roles,body_children,...frame_shadow};
  });
  return {version:1,roles,candidates,visible_id_shapes:[...ids].map(([id_shape,count])=>({id_shape,count})),iframes,body_children:childTags(document),...shadowInfo(document)};
}
