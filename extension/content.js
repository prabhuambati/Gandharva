(() => {
  const normalize = (url) => {
    try {
      const u = new URL(url, 'https://www.instagram.com');
      if (!['http:', 'https:'].includes(u.protocol) || !['instagram.com','www.instagram.com','m.instagram.com'].includes(u.hostname) || u.username || u.password) return '';
      const media = u.pathname.match(/^\/(?:[A-Za-z0-9_.]+\/)?(?:p|reel|reels)\/([A-Za-z0-9_-]+)\/?$/);
      if (media) return `https://www.instagram.com/reel/${media[1]}/`;
      return 'https://www.instagram.com' + u.pathname.replace(/\/+$/, '/') ;
    } catch { return ''; }
  };
  const profileFromLink = (a) => {
    const href = normalize(a?.href || '');
    if (!href || !href.includes('instagram.com')) return {};
    const parts = new URL(href).pathname.split('/').filter(Boolean);
    const excluded = new Set(['reel','reels','p','explore','accounts','direct','stories','about','web','emails']);
    const username = parts[0] && !excluded.has(parts[0].toLowerCase()) ? parts[0] : '';
    const img = a?.querySelector('img') || document.querySelector(`header img[alt*="${username}" i]`);
    return username ? { username, profile_url: `https://www.instagram.com/${username}/`, profile_photo_url: img?.src || '' } : {};
  };
  window.__publicReelCollector = () => {
    const urls = [...document.querySelectorAll('a[href*="/reel/"], a[href*="/reels/"]')];
    const output = new Map();
    for (const a of urls) {
      const reel_url = normalize(a.href); if (!/^https:\/\/www\.instagram\.com\/reel\/[A-Za-z0-9_-]+\/$/.test(reel_url)) continue;
      const container = a.closest('article') || a.closest('div');
      const profileAnchor = container?.querySelector('a[href^="/"]:not([href*="/reel/"]):not([href*="/reels/"])');
      const profile = profileFromLink(profileAnchor);
      const img = a.querySelector('img') || container?.querySelector('img');
      const text = container?.innerText || '';
      const audioLink = container?.querySelector('a[href*="/reels/audio/"], a[href*="/audio/"]');
      const audio_label = audioLink?.innerText?.trim() || (text.match(/original audio|original sound/i)?.[0] || '');
      output.set(reel_url, { reel_url, ...profile, caption: text.slice(0, 1000), audio_label, source_url: location.href, collected_at: new Date().toISOString() });
    }
    return [...output.values()];
  };
})();
