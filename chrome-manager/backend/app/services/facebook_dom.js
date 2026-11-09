// Read rendered UI only. No session secrets, React internals or private endpoints.
({mode, pageName, accountMenu = false}) => {
  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[aria-hidden="true"]'));
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const mains = [...document.querySelectorAll('[role="main"],main')].filter(visible);
  const main = mains[0];
  const bodyText = clean(document.body?.innerText).slice(0, 16000);
  const blocked = /\/checkpoint|\/login(?:[/?]|\.php)|\/two_step_verification|\/recover\//.test(location.pathname)
    || !!document.querySelector('input[name="email"],input[name="pass"]');
  const denied = /temporarily blocked|you're temporarily blocked|tạm thời bị chặn|we limit how often|chúng tôi giới hạn|confirm your identity|xác nhận danh tính/i.test(bodyText);
  if (blocked || denied) return {blocked: true};
  if (mode === 'identity_debug') {
    return {
      title: document.title,
      headings: [...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(visible).map(e => clean(e.innerText)).filter(Boolean).slice(0,20),
      buttons: [...document.querySelectorAll('button,[role="button"]')].filter(visible).map(e => clean(e.getAttribute('aria-label') || e.innerText)).filter(Boolean).slice(0,30),
      links: mains.flatMap(scope => [...scope.querySelectorAll('a[href]')]).filter(visible).slice(0,40).map(a => {
        const u = new URL(a.href,location.href);
        return {text:clean(a.innerText), url:u.origin+u.pathname+(u.searchParams.has('id')?'?id='+u.searchParams.get('id'):''),
          fonts:[a,...a.querySelectorAll('span,strong')].slice(0,8).map(e=>({size:getComputedStyle(e).fontSize,weight:getComputedStyle(e).fontWeight,text:clean(e.innerText).slice(0,80)}))};
      }),
      main_text: mains.map(e => clean(e.innerText)).join('\n').slice(0,3000),
    };
  }
  if (mode === 'account') {
    const interfaceHeadings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(e=>visible(e)&&!e.closest('[role="article"],[data-pagelet^="FeedUnit_"]'));
    if(interfaceHeadings.some(e=>/^(?:manage page|quản lý trang|gérer la page)$/i.test(clean(e.innerText))))return {name:'',url:location.href,is_page:true};
    const profileUrl = raw => {
      try {
        const url = new URL(raw, location.href);
        if (url.protocol !== 'https:' || !['facebook.com','www.facebook.com','m.facebook.com'].includes(url.hostname)) return null;
        const path = url.pathname.replace(/^\/|\/$/g,'');
        if (path === 'profile.php' && /^\d+$/.test(url.searchParams.get('id') || '')) return url.href;
        if (/^(?:people|pages)\/[^/]+\/\d+$/.test(path)) return url.href;
        const reserved = /^(?:me|home\.php|profile\.php|pages|login(?:\.php)?|checkpoint|recover|settings|friends|groups|watch|reels?|marketplace|notifications|messages|help|privacy|business|gaming|events|stories|search)$/i;
        return /^[\w.-]+$/.test(path) && !/\.php$/i.test(path) && !reserved.test(path) ? url.href : null;
      } catch { return null; }
    };
    const validName = value => {
      const name = clean(value).replace(/^\(\d+\)\s*/, '').replace(/\s*[|–—-]\s*Facebook\s*$/i,'').trim();
      if (!name || name.length > 160 || /^(?:facebook|log in|login|sign up|đăng nhập|đăng ký|content not found|this content isn't available(?: right now)?|nội dung này hiện không hiển thị|trang này hiện không hiển thị|see all profiles|xem tất cả trang cá nhân)$/i.test(name)) return '';
      return name;
    };
    const meta = property => document.querySelector('meta[property="' + property + '"]')?.content;
    const currentUrl = profileUrl(location.href);
    const canonicalUrl = profileUrl(document.querySelector('link[rel="canonical"]')?.href || meta('og:url') || '');
    const url = currentUrl || canonicalUrl || location.href;
    const headings = (mains.length ? mains : [document]).flatMap(scope => [...scope.querySelectorAll('h1,[role="heading"][aria-level="1"]')]).filter(visible);
    const diagnostics = {main_count:mains.length, heading_count:headings.length, profile_url:!!(currentUrl || canonicalUrl)};
    if (accountMenu) {
      for (const menu of [...document.querySelectorAll('[role="menu"]')].filter(visible)) {
        for (const link of menu.querySelectorAll('a[href]')) {
          const name = validName((link.innerText || link.getAttribute('aria-label') || '').split('\n')[0]);
          const href = profileUrl(link.href);
          if (visible(link) && name && href) return {name, url:href, source:'account_menu', diagnostics};
        }
      }
    }
    for (const heading of headings) {
      const name = validName(heading.innerText);
      if (name) return {name, url, source:'profile_heading', diagnostics};
    }
    // Current Facebook renders the profile display name as a large span/button,
    // with no heading role in some languages/layouts (observed on French UI).
    if (currentUrl || canonicalUrl || /^\/me\/?$/.test(location.pathname)) {
      const displayNames = mains.flatMap(scope => [...scope.querySelectorAll('span,[role="button"]')])
        .filter(e => visible(e) && !e.closest('[role="article"],[data-pagelet^="FeedUnit_"]'))
        .map(e => ({name:validName(e.innerText), size:parseFloat(getComputedStyle(e).fontSize), weight:parseInt(getComputedStyle(e).fontWeight,10)}))
        .filter(e => e.name && e.size >= 28 && e.weight >= 600)
        .sort((a,b) => b.size-a.size);
      if (displayNames.length) return {name:displayNames[0].name, url, source:'profile_display_name', diagnostics};
    }
    if (currentUrl || canonicalUrl) {
      const name = validName(meta('og:title')) || validName(document.title);
      if (name) return {name, url, source:'profile_title', diagnostics};
    }
    return {name:'', url, diagnostics};
  }
  if (mode === 'pages') {
    if (!main) return {pages: [], recognized: false, empty: false};
    const managed = /pages you manage|your pages(?: and profiles)?|pages and profiles|trang bạn quản lý|trang do bạn quản lý|trang của bạn|trang và trang cá nhân|pages que vous gérez|vos pages(?: et profils)?|pages et profils|pages gérées/i;
    const headings = [...main.querySelectorAll('h1,h2,h3,[role="heading"],span,div')].filter(e => {
      const text=clean(e.innerText);
      return visible(e) && text.length>0 && text.length<100 && ![...e.children].some(child=>clean(child.innerText)===text);
    });
    const heading = headings.find(h => managed.test(clean(h.innerText)));
    const recommendation = /^(?:suggested(?: pages| for you)?|discover(?: pages)?|recommended(?: pages)?|gợi ý|khám phá|đề xuất|suggestions|pages suggérées|pages recommandées|découvrir)$/i;
    let scope = heading?.parentElement;
    // Find the list owned by this heading, without harvesting the navigation rail.
    while (scope && scope !== main && ![...scope.querySelectorAll('a[href]')].some(a => a.querySelector('img'))) scope = scope.parentElement;
    scope = scope || main;
    const pages = [];
    for (const a of scope.querySelectorAll('a[href]')) {
      if (!visible(a)) continue;
      // Ignore recommendations following the managed Page section.
      if (headings.some(h => recommendation.test(clean(h.innerText)) && (h.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING))) continue;
      const name = clean(a.innerText) || clean(a.getAttribute('aria-label'));
      if (!name || name.length > 160 || /^(switch(?: now| into .*)?|manage(?: page)?|create(?: a)? page|see all(?: pages)?|learn more|view (?:page|profile)|edit|chuyển(?: ngay| sang .*)?|quản lý(?: trang)?|tạo trang|xem tất cả(?: trang)?|xem trang|xem trang cá nhân|chỉnh sửa|changer(?: de profil)?|gérer(?: la page)?|créer(?: une)? page|voir tout(?:es les pages)?|voir la page|modifier)$/i.test(name)) continue;
      const prominent = [a,...a.querySelectorAll('span,strong')].some(e=>parseFloat(getComputedStyle(e).fontSize)>=16 && parseInt(getComputedStyle(e).fontWeight,10)>=600);
      if (!prominent && !a.querySelector('img') && !a.querySelector('h2,h3,[role="heading"]') && !a.closest('h2,h3,[role="heading"],[role="listitem"]')) continue;
      pages.push({name, url: a.href});
    }
    return {pages, recognized: !!heading, empty: /you (?:don't|do not) (?:have|manage) any pages|no pages (?:yet|to show)|bạn (?:chưa có|không quản lý) trang|aucune page|vous ne gérez (?:pas|aucune) page|vous n’avez pas (?:encore )?de page/i.test(bodyText)};
  }
  const posts = [], containers = [...(main || document).querySelectorAll('[role="article"],[data-pagelet^="FeedUnit_"]')];
  for (const e of document.querySelectorAll('[data-cm-time]')) e.removeAttribute('data-cm-time');
  let index = 0;
  for (const article of containers) {
    if (!visible(article) || containers.some(other => other !== article && other.contains(article))) continue;
    const text = clean(article.innerText);
    const authors = [...article.querySelectorAll('h2 a,h3 a,[role="heading"] a')].filter(visible);
    const authorLink = authors.find(a => clean(a.innerText) || clean(a.getAttribute('aria-label')));
    const author = clean(authorLink?.innerText) || clean(authorLink?.getAttribute('aria-label'));
    const authorMatches = !!author && author.toLocaleLowerCase() === clean(pageName).toLocaleLowerCase();
    const links = [...article.querySelectorAll('a[href]')].filter(a => {
      if (!visible(a)) return false;
      const p = new URL(a.href, location.href);
      return /\/(?:posts|videos|reel)\/|\/(?:permalink|story|photo)\.php|\/photo\//.test(p.pathname) && !p.searchParams.has('comment_id');
    });
    const timeLink = links.find(a => clean(a.innerText).length < 120 && !a.querySelector('img'));
    if (!timeLink) continue;
    const times = [];
    for (const e of [timeLink, ...timeLink.querySelectorAll('[aria-label],[title],abbr,time,[data-utime]')]) {
      for (const attr of ['data-utime','datetime','title','aria-label']) if(e.getAttribute(attr)) times.push(e.getAttribute(attr));
    }
    times.push(clean(timeLink.innerText));
    timeLink.setAttribute('data-cm-time', String(index));
    posts.push({url: timeLink.href, times, hover: index++, author_matches: authorMatches,
      pinned: /pinned post|pinned by|bài viết đã ghim|đã ghim|featured|nổi bật|publication épinglée|épinglé|à la une/i.test(text.slice(0, 180))});
  }
  return {posts, empty: /no posts (?:yet|available)|hasn't posted|chưa có bài viết|chưa đăng bài viết|aucune publication|n’a pas encore publié/i.test(bodyText)};
}
