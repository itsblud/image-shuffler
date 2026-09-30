/* Configure these two public values after creating the Supabase project. */
window.SHARED_MODERATION_CONFIG = {
  url: '',
  publishableKey: ''
};

window.SharedModeration = (() => {
  const config = window.SHARED_MODERATION_CONFIG;
  const SESSION_KEY = 'shufflerSharedModerationSessionV1';
  let session = readSession();

  function configured() {
    return Boolean(config.url && config.publishableKey);
  }

  function readSession() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); }
    catch { return null; }
  }

  function saveSession(value) {
    session = value;
    try {
      if (value) localStorage.setItem(SESSION_KEY, JSON.stringify(value));
      else localStorage.removeItem(SESSION_KEY);
    } catch {}
  }

  function endpoint(path) {
    return `${config.url.replace(/\/$/, '')}${path}`;
  }

  async function request(path, options = {}, authenticated = true) {
    if (!configured()) throw new Error('Shared moderation is not configured.');
    const headers = new Headers(options.headers || {});
    headers.set('apikey', config.publishableKey);
    headers.delete('Authorization');
    if (authenticated && session?.access_token) headers.set('Authorization', `Bearer ${session.access_token}`);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await fetch(endpoint(path), { ...options, headers });
    if (!response.ok) {
      let message = `Shared moderation request failed (${response.status}).`;
      try {
        const body = await response.json();
        if (body.message) message = body.message;
      } catch {}
      throw new Error(message);
    }
    if (response.status === 204) return null;
    const type = response.headers.get('content-type') || '';
    return type.includes('application/json') ? response.json() : null;
  }

  async function currentSession() {
    if (!session) return null;
    if (session.expires_at && session.expires_at > Date.now() + 30000) return session;
    if (!session.refresh_token) { saveSession(null); return null; }
    try {
      const response = await fetch(endpoint('/auth/v1/token?grant_type=refresh_token'), {
        method: 'POST',
        headers: { apikey: config.publishableKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: session.refresh_token })
      });
      if (!response.ok) throw new Error('Session expired. Please sign in again.');
      const data = await response.json();
      saveSession({ ...data, expires_at: Date.now() + data.expires_in * 1000 });
      return session;
    } catch {
      saveSession(null);
      return null;
    }
  }

  async function signIn(email, password) {
    const response = await fetch(endpoint('/auth/v1/token?grant_type=password'), {
      method: 'POST',
      headers: { apikey: config.publishableKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    if (!response.ok) throw new Error('Sign-in failed. Check the email and password.');
    const data = await response.json();
    saveSession({ ...data, expires_at: Date.now() + data.expires_in * 1000 });
    return data.user;
  }

  async function signOut() {
    if (await currentSession()) {
      try { await request('/auth/v1/logout', { method: 'POST' }); } catch {}
    }
    saveSession(null);
  }

  async function user() {
    const active = await currentSession();
    return active ? active.user : null;
  }

  async function isModerator() {
    const active = await currentSession();
    if (!active?.user?.id) return false;
    return request('/rest/v1/rpc/is_shuffler_moderator', {
      method: 'POST',
      body: '{}'
    });
  }

  async function activeDecisions() {
    const rows = [];
    const pageSize = 1000;
    for (let offset = 0; ; offset += pageSize) {
      const page = await request(`/rest/v1/image_moderation?select=image_id,omitted,nsfw,archive_url&or=(omitted.eq.true,nsfw.eq.true)&order=image_id.asc&limit=${pageSize}&offset=${offset}`, {}, false);
      if (!Array.isArray(page)) throw new Error('Could not read shared moderation decisions.');
      rows.push(...page);
      if (page.length < pageSize) return rows;
    }
  }

  async function saveDecision(imageId, archiveUrl, omitted, nsfw) {
    await currentSession();
    if (!omitted && !nsfw) {
      return request(`/rest/v1/image_moderation?image_id=eq.${encodeURIComponent(imageId)}`, { method: 'DELETE' });
    }
    return request('/rest/v1/image_moderation?on_conflict=image_id', {
      method: 'POST',
      headers: { Prefer: 'resolution=merge-duplicates,return=minimal' },
      body: JSON.stringify({
        image_id: imageId,
        archive_url: archiveUrl || null,
        omitted: Boolean(omitted),
        nsfw: Boolean(nsfw),
        updated_at: new Date().toISOString()
      })
    });
  }

  async function clearAll() {
    await currentSession();
    return request('/rest/v1/image_moderation?image_id=not.is.null', { method: 'DELETE' });
  }

  return { configured, activeDecisions, clearAll, currentSession, isModerator, saveDecision, signIn, signOut, user };
})();
