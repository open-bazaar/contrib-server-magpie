const drawer = document.querySelector('[data-gateway-drawer]');
if (drawer) {
  drawer.showModal();
  drawer.addEventListener('cancel', () => location.assign(drawer.dataset.close));
  drawer.addEventListener('click', event => {
    if (event.target === drawer) location.assign(drawer.dataset.close);
  });
}

const confirmation = document.querySelector('[data-operation-confirm]');

function confirmOperation(form, submit) {
  const label = submit.textContent.trim();
  confirmation.querySelector('h3').textContent = label;
  confirmation.querySelector('p').textContent = form.dataset.confirm;
  const accept = confirmation.querySelector('[value="confirm"]');
  accept.textContent = label;
  accept.classList.toggle('danger', submit.classList.contains('danger'));
  confirmation.returnValue = '';
  return new Promise(resolve => {
    confirmation.addEventListener('close', () => resolve(confirmation.returnValue === 'confirm'), {once: true});
    confirmation.showModal();
  });
}

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-operation]');
  if (!form) return;
  event.preventDefault();
  const submit = event.submitter;
  if (form.dataset.confirm && !(await confirmOperation(form, submit))) return;
  const slot = form.querySelector('[data-operation-slot]');
  const payload = new URLSearchParams(new FormData(form, submit));
  if (form.classList.contains('model-tests')) {
    if (form.getAttribute('aria-busy') === 'true') return;
    form.setAttribute('aria-busy', 'true');
  }
  submit.disabled = true;
  slot.replaceChildren();
  try {
    const result = await fetch(form.action, {method: 'POST', body: payload});
    if (result.redirected) {
      location.assign(result.url);
      return;
    }
    const page = new DOMParser().parseFromString(await result.text(), 'text/html');
    const notice = page.querySelector('[data-operation-result]');
    if (notice) {
      slot.replaceChildren(notice);
      for (const probe of slot.querySelectorAll('[data-probe]')) {
        const chip = form.querySelector(`button[name="model"][value="${CSS.escape(probe.dataset.probe)}"]`);
        if (!chip) continue;
        chip.dataset.state = probe.dataset.state;
        chip.querySelector('[data-model-state]').textContent = probe.dataset.label;
        chip.setAttribute('aria-label', chip.dataset.tip + ' · ' + probe.dataset.label);
      }
    } else slot.textContent = form.dataset.unavailable;
    if (result.ok) form.reset();
  } catch {
    slot.textContent = form.dataset.unavailable;
  } finally {
    submit.disabled = false;
    form.removeAttribute('aria-busy');
  }
});

const routes = document.querySelector('[data-live-routes]');
if (routes) followRoutes(routes);

function followRoutes(routes) {
  const template = document.querySelector('[data-route-template]');
  const connection = document.querySelector('[data-live-connection]');
  const empty = document.querySelector('[data-empty-routes]');
  const buttons = document.querySelectorAll('[data-route-filter]');
  const states = new Map([...document.querySelectorAll('[data-live-state]')].map(item => [item.dataset.liveState, item]));
  const clients = new Map([...document.querySelectorAll('[data-live-client]')].map(item => [item.dataset.liveClient, item]));
  const providers = new Map([...document.querySelectorAll('[data-live-provider]')].map(item => [item.dataset.liveProvider, item]));
  const clock = new Intl.DateTimeFormat('en-GB', {hour: '2-digit', minute: '2-digit', timeZone: routes.dataset.timezone || undefined});
  const queued = ['slot', 'rpm'];
  let filter = 'all';
  let source;

  function showRows() {
    for (const row of routes.children) {
      const waiting = queued.includes(row.dataset.phase);
      row.hidden = filter !== 'all' && (filter === 'queue') !== waiting;
    }
    empty.hidden = [...routes.children].some(row => !row.hidden);
  }

  for (const button of buttons) button.addEventListener('click', () => {
    filter = button.dataset.routeFilter;
    for (const choice of buttons) {
      const selected = choice === button;
      choice.classList.toggle('on', selected);
      choice.setAttribute('aria-pressed', String(selected));
    }
    showRows();
  });
  if (!routes.dataset.events) return;

  function rowFor(route) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.dataset.route = route.id ?? '';
    paint(row, route);
    return row;
  }

  function paint(row, route) {
    const state = ['slot', 'rpm', 'redact'].includes(route.queue) ? route.queue : route.phase || 'started';
    const mark = states.get(state).content;
    const agent = clients.get(route.agent) || clients.get('');
    const provider = providers.get(route.provider) || providers.get('');
    const when = route.time || route.started;
    const time = row.querySelector('[data-live-time]');
    const elapsed = route.elapsed ?? route.ms;
    row.dataset.phase = state;
    if (when) time.dateTime = when;
    time.textContent = when ? clock.format(new Date(when)) : '—';
    row.querySelector('[data-client-icon]').replaceChildren(agent.content.cloneNode(true));
    row.querySelector('[data-source]').textContent = agent.dataset.name || route.agent || '—';
    row.querySelector('[data-provider-icon]').replaceChildren(provider.content.cloneNode(true));
    row.querySelector('[data-provider]').textContent = provider.dataset.name || route.provider || '—';
    row.querySelector('[data-model]').textContent = route.model ?? '';
    row.querySelector('[data-destination]').hidden = !route.provider;
    const noDestination = row.querySelector('[data-no-destination]');
    noDestination.hidden = Boolean(route.provider);
    noDestination.textContent = mark.textContent.trim();
    row.querySelector('[data-state]').replaceChildren(mark.cloneNode(true));
    row.querySelector('[data-elapsed]').textContent = elapsed == null ? '—' : formatElapsed(elapsed);
  }

  function formatElapsed(ms) {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 10) return `${Math.round(ms / 100) / 10}s`;
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const pad = value => String(value).padStart(2, '0');
    if (minutes < 60) return `${minutes}m${pad(seconds % 60)}s`;
    return `${Math.floor(minutes / 60)}h${pad(minutes % 60)}m`;
  }

  async function reset() {
    source.close();
    const response = await fetch(routes.dataset.snapshot);
    if (!response.ok) {
      connection.textContent = routes.dataset.unavailable;
      return;
    }
    const snapshot = await response.json();
    routes.replaceChildren(...snapshot.routes.map(rowFor), ...snapshot.unrouted.map(rowFor));
    showRows();
    follow(snapshot.cursor);
  }

  function follow(cursor) {
    const url = new URL(routes.dataset.events, location.origin);
    url.searchParams.set('after', cursor);
    source = new EventSource(url);
    source.addEventListener('open', () => { connection.textContent = ''; });
    source.addEventListener('error', () => { connection.textContent = routes.dataset.reconnect; });
    source.addEventListener('unavailable', () => {
      source.close();
      connection.textContent = routes.dataset.unavailable;
    });
    source.addEventListener('reset', () => reset().catch(() => { connection.textContent = routes.dataset.unavailable; }));
    source.addEventListener('route', event => {
      const {route} = JSON.parse(event.data);
      const row = routes.querySelector(`[data-route="${CSS.escape(route.id)}"]`);
      if (route.phase === 'done' || route.phase === 'dropped') row?.remove();
      else if (row) paint(row, route);
      else routes.prepend(rowFor(route));
      showRows();
    });
  }

  follow(routes.dataset.cursor);
  window.addEventListener('pagehide', () => source.close());
}
