const API = (() => {
  const BASE = '/api/v1';
  let _token = localStorage.getItem('sh_token') || '';
  let _refreshToken = localStorage.getItem('sh_refresh') || '';
  let _user = null;
  let _ws = null;
  let _wsHandlers = {};

  function headers() {
    const h = { 'Content-Type': 'application/json' };
    if (_token) h['Authorization'] = 'Bearer ' + _token;
    return h;
  }

  async function request(method, path, body) {
    const opts = { method, headers: headers() };
    if (body !== undefined) opts.body = JSON.stringify(body);
    let res = await fetch(BASE + path, opts);
    if (res.status === 401 && _refreshToken) {
      const ok = await _refresh();
      if (ok) {
        opts.headers = headers();
        res = await fetch(BASE + path, opts);
      }
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      const msg = typeof detail === 'string' ? detail : (detail && detail.message_vi) || res.statusText;
      const e = new Error(msg);
      e.status = res.status;
      e.detail = detail;
      throw e;
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async function _refresh() {
    try {
      const res = await fetch(BASE + '/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: _refreshToken }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      _token = data.access_token;
      _refreshToken = data.refresh_token;
      localStorage.setItem('sh_token', _token);
      localStorage.setItem('sh_refresh', _refreshToken);
      return true;
    } catch { return false; }
  }

  async function login(username, password) {
    const res = await fetch(BASE + '/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Đăng nhập thất bại');
    }
    const data = await res.json();
    _token = data.access_token;
    _refreshToken = data.refresh_token;
    _user = data.user;
    localStorage.setItem('sh_token', _token);
    localStorage.setItem('sh_refresh', _refreshToken);
    return data.user;
  }

  async function init() {
    if (_token) {
      try {
        _user = await request('GET', '/auth/me');
        connectWS();
        return _user;
      } catch { /* token invalid */ }
    }
    throw new Error('Not authenticated');
  }

  function logout() {
    localStorage.removeItem('sh_token');
    localStorage.removeItem('sh_refresh');
    _token = ''; _refreshToken = ''; _user = null;
    if (_ws) { _ws.onclose = null; _ws.close(); _ws = null; }
  }

  function connectWS() {
    if (_ws) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    _ws = new WebSocket(proto + '//' + location.host + '/ws?token=' + _token);
    _ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        const fn = _wsHandlers[msg.type];
        if (fn) fn(msg);
      } catch {}
    };
    _ws.onclose = () => { _ws = null; setTimeout(connectWS, 3000); };
    _ws.onerror = () => { _ws?.close(); };
  }

  function onWS(type, handler) { _wsHandlers[type] = handler; }

  return {
    init, login, logout, onWS,
    get user() { return _user; },
    get token() { return _token; },

    dashboard:       ()                         => request('GET', '/dashboard'),
    rooms:           ()                         => request('GET', '/rooms'),
    controlDevice:   (slug, action, params)     => request('POST', '/devices/' + slug + '/control', { action, params: params || {} }),
    agentCommand:    (message, conversationId)  => request('POST', '/agent/command', { message, conversation_id: conversationId || '' }),
    historySummary:  ()                         => request('GET', '/history/summary'),
    history:         (opts)                     => {
      const p = new URLSearchParams();
      if (opts?.limit) p.set('limit', opts.limit);
      if (opts?.offset) p.set('offset', opts.offset);
      if (opts?.source) p.set('source', opts.source);
      return request('GET', '/history?' + p.toString());
    },
    notifications:   ()                         => request('GET', '/notifications'),
    approvals:       ()                         => request('GET', '/agent/approvals'),
    decideApproval:  (id, approved, note)       => request('POST', '/agent/approvals/' + id, { approved, note: note || '' }),
    members:         ()                         => request('GET', '/members'),
    habits:          (memberId)                 => {
      const p = new URLSearchParams({ include_disabled: 'true' });
      if (memberId) p.set('member_id', memberId);
      return request('GET', '/habits?' + p.toString());
    },
    habitOptions:    ()                         => request('GET', '/habits/options'),
    habitPreview:    (habits)                   => request('POST', '/habits/preview', { habits }),
    habitCommit:     (habits, memberId)         => request('POST', '/habits/commit' + (memberId ? '?member_id=' + memberId : ''), { habits }),
    energySeries:    (granularityOrOpts, start, end)  => {
      if (typeof granularityOrOpts === 'object' && granularityOrOpts !== null) {
        const { granularity = 'day', start: s = '', end: e = '' } = granularityOrOpts;
        return request('GET', `/energy/series?granularity=${granularity}&start=${s}&end=${e}`);
      }
      return request('GET', `/energy/series?granularity=${granularityOrOpts || 'day'}&start=${start || ''}&end=${end || ''}`);
    },
    childLock:       ()                         => request('GET', '/household/child-lock'),
    setChildLock:    (enabled)                  => request('PUT', '/household/child-lock', { enabled }),
    markRead:        (id)                       => request('POST', `/notifications/${id}/read`, {}),
    markAllRead:     ()                         => request('POST', '/notifications/read-all', {}),
    suggestions:     ()                         => request('GET', '/agent/suggestions'),
    decideSuggestion:(id, accepted, snoozeMinutes) => request('POST', `/agent/suggestions/${id}`, { accepted: !!accepted, snooze_minutes: snoozeMinutes || 0 }),
    myAccess:        ()                         => request('GET', '/members/me/access'),
    myProfile:       ()                         => request('GET', '/members/me/profile'),
    memberProfile:   (id)                       => request('GET', '/members/' + id + '/profile'),
    energyUsage:     ()                         => request('GET', '/energy/usage'),
    updateHabit:     (id, payload)              => request('PATCH', '/habits/' + id, payload),
    deleteHabit:     (id)                       => request('DELETE', '/habits/' + id),
    updateMember:    (id, data)                 => request('PATCH', '/members/' + id, data),
    addMember:       (data)                     => request('POST', '/members', data),
    deleteMember:    (id)                       => request('DELETE', '/members/' + id),
    memberAccess:    (id)                       => request('GET', '/members/' + id + '/access'),
    setMemberAccess: (id, rules, confirmSecurity) => request('PUT', '/members/' + id + '/access', { rules, confirm_security: !!confirmSecurity }),
    setRoomAccess:   (id, roomId, effect, confirmSecurity) => request('POST', '/members/' + id + '/access/room/' + roomId, { effect, confirm_security: !!confirmSecurity }),
  };
})();
