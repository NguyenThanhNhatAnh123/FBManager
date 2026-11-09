// Rendered Outlook UI only: no mailbox API, tokens, cookies or framework state.
({email}) => {
  const visible=el=>!!(el&&el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'&&!el.closest('[aria-hidden="true"]'));
  const clean=value=>(value||'').replace(/\s+/g,' ').trim();
  const emails=value=>[...new Set((value||'').match(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}/gi)||[])];
  const label=el=>clean(el.getAttribute('aria-label')||el.innerText||el.getAttribute('title'));
  const query=(root,selector)=>[...root.querySelectorAll(selector)].filter(visible);
  const marker=(el,kind,index)=>{el.setAttribute('data-cm-outlook-'+kind,String(index));return '[data-cm-outlook-'+kind+'="'+index+'"]';};
  for(const kind of ['account','folder','tab','message','details'])document.querySelectorAll('[data-cm-outlook-'+kind+']').forEach(el=>el.removeAttribute('data-cm-outlook-'+kind));
  const host=location.hostname.toLowerCase();
  if(['login.live.com','login.microsoftonline.com','account.live.com'].includes(host))return {state:'login'};
  if(!['outlook.live.com','outlook.office.com','outlook.office365.com'].includes(host)||!/^\/(?:mail|owa)(?:\/|$)/i.test(location.pathname))return {state:'loading'};
  const bodyText=clean(document.body?.innerText);
  const pane=query(document,'#ReadingPaneContainerId,[data-testid="reading-pane"],[data-testid="message-reading-pane"],[data-app-section="MailReadCompose"],[aria-label="Reading Pane" i],[aria-label="Message pane" i]')[0];
  const accounts=query(document,'#mectrl_currentAccount_primary,#mectrl_currentAccount_secondary,#mectrl_currentAccount_email,[data-testid="account-email"]').filter(el=>!pane?.contains(el));
  let accountAddresses=[...new Set(accounts.flatMap(el=>emails(el.innerText)))];
  const outsideMessage=el=>!pane?.contains(el);
  const controlText=el=>clean((el.getAttribute('aria-label')||'')+' '+(el.getAttribute('title')||'')+' '+el.innerText);
  const accountManager=/account manager|trình quản lý tài khoản|gestionnaire (?:de |des )?comptes?|kontomanager|administrador de cuentas/i;
  const knownAccount=query(document,'#O365_MainLink_Me,#mectrl_main_trigger').find(outsideMessage);
  const explicitAccounts=query(document,'button,[role="button"]').filter(el=>outsideMessage(el)&&!el.closest('[role="menu"],[role="dialog"]')&&accountManager.test(controlText(el)));
  const headerAccounts=[];
  for(const banner of query(document,'[role="banner"],header')){
    for(const el of query(banner,'button,[role="button"]')){
      if(!outsideMessage(el)||el.closest('[role="menu"],[role="dialog"]'))continue;
      if(/^(?:account|your (?:account|profile)|profile(?: picture)?|manage account|tài khoản(?: của bạn)?|ảnh đại diện(?: của bạn)?|hồ sơ|mon compte|votre compte|profil|mein konto|konto|mi cuenta|tu cuenta|perfil)(?:\s*[:,–-].*|\s+for\s+.+)?$/i.test(label(el))&&!headerAccounts.includes(el))headerAccounts.push(el);
    }
  }
  const accountButton=knownAccount||(explicitAccounts.length===1?explicitAccounts[0]:null)||(headerAccounts.length===1?headerAccounts[0]:null);
  if(!accountAddresses.length&&accountButton)accountAddresses=emails(accountButton.getAttribute('aria-label')||'');
  if(!accountAddresses.length){
    const controlledId=accountButton?.getAttribute('aria-controls');
    const menus=query(document,'[role="menu"],[role="dialog"],[data-testid="account-menu"]').filter(el=>{
      if(!outsideMessage(el))return false;
      const actions=query(el,'a,button,[role="button"],[role="menuitem"],[role="link"]').map(label);
      const signOut=actions.some(text=>/^(?:sign out|log out|đăng xuất|se déconnecter|abmelden|cerrar sesión)$/i.test(text));
      const accountAction=actions.some(text=>/^(?:my (?:microsoft )?account|view (?:my )?account|manage (?:my )?account|my profile|view (?:my )?profile|tài khoản (?:microsoft )?của tôi|quản lý tài khoản|xem (?:tài khoản|hồ sơ)|mon compte|afficher le compte|mein konto|konto anzeigen|mi cuenta|ver cuenta)$/i.test(text));
      return signOut&&(accountManager.test(el.getAttribute('aria-label')||'')||accountAction||!!(controlledId&&el.id===controlledId));
    });
    const uniqueMenus=menus.filter(el=>!menus.some(other=>other!==el&&el.contains(other)));
    if(uniqueMenus.length===1)accountAddresses=emails(uniqueMenus[0].innerText);
  }
  const account_email=accountAddresses.length===1?accountAddresses[0]:'';
  const account_selector=accountButton?marker(accountButton,'account',0):null;
  const navigation=query(document,'[role="navigation"],[role="tree"],[aria-label="Folders" i],[aria-label="Mail folders" i]');
  const folderPattern={inbox:/^(?:inbox|hộp thư đến|boîte de réception|posteingang|bandeja de entrada)(?:\b|\s|(?=[0-9]+(?:\s|$))|$)/i,junk:/^(?:junk(?: email)?|thư rác|courrier indésirable|spam|correo no deseado)(?:\b|\s|(?=[0-9]+(?:\s|$))|$)/i};
  const folders=[];
  for(const root of navigation){
    for(const el of query(root,'[role="treeitem"],[role="link"],a,button,[role="button"]')){
      if(pane?.contains(el))continue;
      const kind=Object.keys(folderPattern).find(key=>folderPattern[key].test(label(el)));
      if(!kind||folders.some(row=>row.kind===kind))continue;
      folders.push({kind,selected:el.getAttribute('aria-selected')==='true'||el.getAttribute('aria-current')==='page',selector:marker(el,'folder',folders.length)});
    }
  }
  const tabs=[];
  for(const el of query(document,'[role="tab"]')){
    if(pane?.contains(el))continue;
    const value=label(el);
    const kind=/^(?:Focused|Tập trung|Prioritaire)(?:\b|\s|$)/i.test(value)?'focused':/^(?:Other|Khác|Autres)(?:\b|\s|$)/i.test(value)?'other':null;
    if(kind&&!tabs.some(row=>row.kind===kind))tabs.push({kind,selected:el.getAttribute('aria-selected')==='true',selector:marker(el,'tab',tabs.length)});
  }
  const listRoots=query(document,'[role="listbox"],[aria-label="Message list" i],[data-testid="message-list"]');
  const rowNodes=[];
  for(const root of listRoots){
    for(const el of query(root,'[role="option"],[data-convid],[data-item-id]')){
      if(pane?.contains(el)||rowNodes.some(other=>other.contains(el)))continue;
      if(rowNodes.length<25)rowNodes.push(el);
    }
  }
  const dateText=value=>{
    const raw=clean(value);
    // Minute-only labels are not sufficient to establish fresh mail. Convert
    // full rendered dates using this browser's displayed local timezone.
    if(!/(?:\b20\d{2}\b|^\d{4}-\d{2}-\d{2}T)/.test(raw)||!/\d{1,2}:\d{2}/.test(raw))return null;
    const stamp=Date.parse(raw.replace(/^(?:Received|Sent|Date|Đã nhận|Ngày gửi|Ngày)\s*[:：]\s*/i,''));
    return Number.isFinite(stamp)?{
      received_at:new Date(stamp).toISOString(),
      received_precision:/\d{1,2}:\d{2}:\d{2}/.test(raw)?'second':'minute',
      received_timezone_offset_minutes:new Date(stamp).getTimezoneOffset(),
    }:null;
  };
  const timeIn=root=>{
    const nodes=query(root,'time[datetime],[title],[aria-label],[data-testid="received-time"]');
    for(const node of nodes){
      const value=node.getAttribute('datetime')||node.getAttribute('title')||node.getAttribute('aria-label')||node.innerText;
      const parsed=dateText(value);if(parsed)return parsed;
    }
    return {};
  };
  const rowSubject=el=>{
    const explicit=clean(query(el,'[data-testid="subject"],[data-testid="message-subject"],[data-automationid="subject"]')[0]?.innerText);
    if(explicit)return explicit;
    const text=el.innerText||'';
    if(!/facebook/i.test(text+' '+(el.getAttribute('aria-label')||'')))return '';
    // Outlook often puts the Facebook sender and subject on separate lines.
    // This hint identifies a row only; the opened message still has to pass
    // exact rendered sender, recipient, body and freshness validation.
    return clean(text.split('\n').find(line=>/\bcode\b|mã (?:bảo mật|xác nhận|xác minh|đăng nhập)/i.test(line)));
  };
  const rows=rowNodes.map((el,index)=>({
    key:el.getAttribute('data-item-id')||el.getAttribute('data-convid')||el.id||clean(el.innerText).slice(0,1000),
    hint:clean(el.innerText+' '+(el.getAttribute('aria-label')||'')).slice(0,12000),
    subject:rowSubject(el),
    ...timeIn(el),selected:el.getAttribute('aria-selected')==='true',selector:marker(el,'message',index),
  }));
  let detail=null;
  if(pane){
    const bodyNodes=query(pane,'[aria-label="Message body" i],[role="document"],[data-testid="message-body"]');
    const leafBodies=bodyNodes.filter(el=>!bodyNodes.some(other=>other!==el&&el.contains(other)));
    let mailBody=leafBodies.map(el=>el.innerText||'').join('\n');
    let iframeBodyRead=false;
    if(!mailBody){
      for(const frame of query(pane,'iframe')){
        try{
          const text=frame.contentDocument?.body?.innerText;
          if(clean(text)){mailBody+=text+'\n';iframeBodyRead=true;}
        }catch(_){/* cross-origin frame is not inspected */}
      }
    }
    const outsideBody=el=>!bodyNodes.some(body=>body===el||body.contains(el));
    const headerNodes=query(pane,'[data-testid="message-header"],[data-testid="sender"],[data-testid="recipients"],[aria-label^="From:" i],[aria-label^="To:" i]');
    let header=headerNodes.filter(outsideBody).map(el=>el.innerText+' '+(el.getAttribute('aria-label')||'')+' '+(el.getAttribute('title')||'')).join('\n');
    if(!header){
      const raw=pane.innerText||'';
      const bodyRaw=leafBodies[0]?.innerText;
      // Only the message chrome before its body may supply header addresses.
      header=bodyRaw&&raw.includes(bodyRaw)?raw.slice(0,raw.indexOf(bodyRaw)):iframeBodyRead&&!leafBodies.some(el=>el.tagName!=='IFRAME')?raw:'';
    }
    const detailDialogs=query(document,'[role="dialog"]').filter(el=>!bodyNodes.some(body=>body.contains(el))&&/\bfrom\s*:|\btừ\s*:/i.test(el.innerText||'')&&/\bto\s*:|\bđến\s*:/i.test(el.innerText||''));
    for(const dialog of detailDialogs)header+='\n'+dialog.innerText;
    const fromLine=header.match(/(?:^|\n)\s*(?:From|Từ|De)\s*[:：]\s*([^\n]+)/i)?.[1];
    let sender=fromLine&&emails(fromLine).length===1?emails(fromLine)[0]:'';
    const senderNodes=query(pane,'[data-testid="sender"],[emailaddress],[aria-label^="From:" i]').filter(outsideBody);
    if(!sender){
      const addresses=[...new Set(senderNodes.flatMap(el=>emails(el.getAttribute('emailaddress')||el.getAttribute('title')||el.getAttribute('aria-label')||el.innerText)))];
      if(addresses.length===1)sender=addresses[0];
    }
    if(!sender){
      // Outlook can render "Facebook<address>" as its own header line without
      // a From label. Never search the email body for this fallback.
      const lines=header.split('\n').map(clean).filter(line=>/^Facebook\s*<[^<>]+>\s*$/i.test(line));
      const tooltips=query(pane,'[title],[aria-label]').filter(outsideBody)
        .flatMap(el=>[el.getAttribute('title')||'',el.getAttribute('aria-label')||''])
        .map(clean).filter(line=>/^Facebook\s*<[^<>]+>\s*$/i.test(line));
      const addresses=[...new Set([...lines,...tooltips].flatMap(emails).map(value=>value.toLowerCase()))];
      if(addresses.length===1)sender=addresses[0];
    }
    const toLines=[...header.matchAll(/(?:^|\n)\s*(?:To|Đến|Tới|À)\s*[:：]\s*([^\n]+)/gi)].flatMap(match=>emails(match[1]));
    const recipientNodes=query(pane,'[data-testid="recipients"],[aria-label^="To:" i]').filter(outsideBody);
    const recipients=[...new Set([...toLines,...recipientNodes.flatMap(el=>emails(el.getAttribute('title')||el.getAttribute('aria-label')||el.innerText))])];
    const subjectNode=query(pane,'[data-testid="message-subject"],h1,h2,[role="heading"]').filter(outsideBody)[0];
    const selected=rows.find(row=>row.selected);
    const expandedTime=detailDialogs.map(timeIn).find(value=>value.received_at);
    const headerTimeNodes=query(pane,'time[datetime],[title],[aria-label],[data-testid="received-time"]').filter(outsideBody);
    const received=expandedTime||headerTimeNodes.map(node=>dateText(node.getAttribute('datetime')||node.getAttribute('title')||node.getAttribute('aria-label')||node.innerText)).find(Boolean)||(selected?.received_at?{received_at:selected.received_at,received_precision:selected.received_precision,received_timezone_offset_minutes:selected.received_timezone_offset_minutes}:{});
    const expand=query(pane,'button,[role="button"]').filter(outsideBody).find(el=>/^(?:show (?:message )?details|show more (?:recipients|details)|more (?:message )?details|hiển thị (?:chi tiết|thêm người nhận)|chi tiết thư)$/i.test(label(el)));
    const subject=clean(subjectNode?.innerText);
    detail={key:selected?.key||'',sender,recipients,...received,subject,body:mailBody.slice(0,30000),selection_matches:!selected?.subject||selected.subject===subject,
      unverified:/unverified sender|couldn.t verify (?:the|this) sender|sender.*failed.*authentication|không thể xác minh.*người gửi/i.test(header),
      expand_selector:expand?marker(expand,'details',0):null};
  }
  const folder=folders.find(row=>row.selected)?.kind||null;
  const mailbox=!!(folders.length||listRoots.length||pane||/mail folders|hộp thư đến|boîte de réception|\binbox\b/i.test(bodyText));
  return {state:mailbox?'mailbox':'loading',account_email,account_selector,folders,folder,tabs,items:rows,detail,
    loading:!!document.querySelector('[aria-busy="true"],[role="progressbar"]')};
}
