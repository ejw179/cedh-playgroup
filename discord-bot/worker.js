/**
 * cEDH Playgroup: Discord slash commands, as a Cloudflare Worker.
 *
 *   /start   plan a session (players, goal, games...) like the site's "Plan a session" form
 *   /report  report the current game of your pod: winner (or Draw) and your take
 *   /pods    show the seating of the game being played now
 *   /iam     link your Discord account to your roster name (tags in posts, replies count as your take)
 *
 * Discord POSTs each command here (the app's Interactions Endpoint URL). The Worker
 * checks Discord's signature, answers within Discord's 3 seconds with "thinking...",
 * then sends the same submission the site would to the Apps Script endpoint (with
 * the group PIN, which never leaves the Worker). The Apps Script starts the import
 * workflow, which plans or records it and posts to the channel as usual.
 * The Worker reads the group data from the public repo on GitHub.
 *
 * Settings (wrangler.toml vars, or `wrangler secret put NAME`):
 *   DISCORD_PUBLIC_KEY  the app's public key (Developer Portal > General Information)
 *   RESULTS_URL         the Apps Script web app URL (results_url in data/config.json)
 *   PIN                 the group PIN (secret)
 *   DISCORD_TAGS        optional: Name=DiscordUserID,... for anyone who hasn't run /iam (secret)
 *   GITHUB_REPO         default ejw179/cedh-playgroup
 *   GITHUB_TOKEN        optional (secret): read data through the GitHub API instead of raw files
 *   GUILD_ID, ALLOWED_ROLE  optional: only that server / members with that role
 */

const API = 'https://discord.com/api/v10';
const TZ = 'America/New_York';
const EPHEMERAL = 64;

export const GOALS = {
  worst_seat: 'Worst-seat practice',
  rehearsal: 'Tournament rehearsal',
  local: 'Face the local meta',
  big: 'Face big-event meta',
  improve: 'Improve one player',
  fix: 'Fix a matchup',
  random: 'Random',
};

export default {
  async fetch(request, env, ctx) {
    if (request.method !== 'POST') return new Response('cEDH playgroup Discord commands', { status: 200 });
    const body = await request.text();
    if (!(await verify(request, body, env.DISCORD_PUBLIC_KEY))) return new Response('bad signature', { status: 401 });
    const ix = JSON.parse(body);
    if (ix.type === 1) return json({ type: 1 }); // Discord's PING when the endpoint URL is saved
    if (env.GUILD_ID && ix.guild_id !== env.GUILD_ID) return reply('These commands only work in the group\'s server.');
    if (env.ALLOWED_ROLE && !(ix.member?.roles || []).includes(env.ALLOWED_ROLE))
      return reply('You need the playgroup role to use this.');
    const name = ix.data?.name;
    if (ix.type === 4) return json({ type: 8, data: { choices: await autocomplete(ix, env).catch(() => []) } });
    if (ix.type !== 2 || !COMMANDS[name]) return reply('Unknown command.');
    // answer now, finish in the background, then edit the "thinking..." message
    ctx.waitUntil(COMMANDS[name](ix, env)
      .catch((err) => `Something went wrong: ${err.message || err}`)
      .then((text) => edit(ix, text)));
    return json({ type: 5, data: { flags: EPHEMERAL } });
  },
};

const COMMANDS = { start, report, pods: showPods, iam };

// ---------- /start ----------

async function start(ix, env) {
  const o = options(ix);
  const goal = o.goal;
  if (!GOALS[goal]) return `Pick a goal: ${Object.keys(GOALS).join(', ')}.`;
  const day = o.date ? parseDate(o.date) : today();
  if (!day) return `I can't read the date "${o.date}". Use 2026-11-22 or 11/22.`;
  const [roster, saved] = await Promise.all([data(env, 'roster.json'), data(env, 'pods.json')]);
  const ids = tagIds(env, roster);
  const names = [], unknown = [];
  for (const word of splitPlayers(o.players || '')) {
    const n = resolve(word, roster, ids);
    if (!n) unknown.push(word);
    else if (!names.includes(n)) names.push(n);
  }
  if (unknown.length) return `Not on the roster: ${unknown.join(', ')}. Randoms need a hand-built pod on the site.`;
  if (names.length < 3) return 'Name at least 3 players, e.g. `Eric, Darison, Alex, Jeff`.';
  let player = '';
  if (goal === 'improve' || goal === 'fix') {
    player = resolve(o.player || '', roster, ids);
    if (!player) return `"${GOALS[goal]}" needs \`player\`: who it's for.`;
    if (!names.includes(player)) return `${player} isn't in the players list.`;
    if (goal === 'fix' && !o.target) return '"Fix a matchup" needs `target`: the commander they need to face.';
  }
  const old = saved.sessions?.[day];
  if (old && !old.done && old.pods?.length && !o.replace)
    return `${day} already has a session (game ${old.game || 1} of ${old.games || 1}). Run /start again with \`replace: True\` to start over.`;
  const games = Math.min(Math.max(o.games || 1, 1), 10);
  const label = GOALS[goal] + (o.best_decks && goal !== 'worst_seat' ? ', best decks' : '');
  const payload = {
    type: 'session', via: 'discord', scenario: goal, date: day, players: names,
    games, goal: o.objective ? `${label}. ${o.objective}` : label,
    ...(o.time_limit ? { time_limit: o.time_limit } : {}), ...(o.rules ? { rules: o.rules } : {}),
    ...(o.best_decks ? { first_deck: true } : {}),
    ...(player ? { player } : {}), ...(o.target ? { target: o.target } : {}),
    reporter: who(ix, ids),
  };
  const res = await submit(env, payload);
  return `Planning ${games > 1 ? `${games} games` : 'a game'} for ${day} (${label}): ${names.join(', ')}. ` +
    `The pods post here ${res.started ? 'in a minute or two' : 'within about 10 minutes'}.` +
    (games > 1 ? ' Each next game is planned once the one before is reported.' : '');
}

// ---------- /report ----------

async function report(ix, env) {
  const o = options(ix);
  const [saved, games, roster] = await Promise.all([data(env, 'pods.json'),
    data(env, 'games.json').catch(() => ({ games: [] })), data(env, 'roster.json')]);
  const ids = tagIds(env, roster);
  const live = current(saved);
  if (!live) return 'No session is being played today. Start one with /start.';
  const { day, s } = live;
  const k = s.game || 1;
  const me = ids.get(ix.member?.user?.id || ix.user?.id);
  const draw = /^draw$/i.test((o.winner || '').trim());
  const winner = draw ? null : seatedName(s, o.winner || '', ids);
  if (!draw && !winner) return `${o.winner} isn't seated in game ${k}. Pick a seated player or Draw.`;
  const inPod = (pod, n) => pod.seats.some((x) => x.player.toLowerCase() === (n || '').toLowerCase());
  let pod;
  if (o.pod) pod = s.pods.find((p) => p.pod === o.pod);
  else if (winner) pod = s.pods.find((p) => inPod(p, winner));
  else pod = s.pods.length === 1 ? s.pods[0] : s.pods.find((p) => inPod(p, me));
  if (!pod) return o.pod ? `There's no pod ${o.pod} in game ${k}.` : 'Which pod? Add `pod`.';
  if (winner && !inPod(pod, winner)) return `${winner} isn't in pod ${pod.pod}.`;
  const done = (games.games || []).some((g) => g.date === day && (g.game || 1) === k && (g.pod || 1) === pod.pod);
  if (done) return `Pod ${pod.pod} of game ${k} is already reported. Reply to its post to add your take.`;
  const note = (o.note || '').trim().slice(0, 600);
  const seats = pod.seats.map((x) => ({
    seat: x.seat, player: x.player, guest: !!x.guest, commander: x.commander, decklist: '',
    note: note && me && x.player.toLowerCase() === me.toLowerCase() ? note : '',
  }));
  const res = await submit(env, {
    type: 'result', via: 'discord', date: day, pod: pod.pod, game: k,
    winner: winner || 'draw', seats, reporter: who(ix, ids),
  });
  const lost = note && !seats.some((x) => x.note) ? ' (Your note wasn\'t saved: you\'re not seated in that pod.)' : '';
  return `Reported game ${k}${s.pods.length > 1 ? `, pod ${pod.pod}` : ''}: ${winner ? `${winner} won` : 'draw'}.${lost} ` +
    `The result posts here ${res.started ? 'in a minute or two' : 'within about 10 minutes'}; ` +
    'the others can reply to the pod\'s post with their take.';
}

// ---------- /pods ----------

async function showPods(ix, env) {
  const live = current(await data(env, 'pods.json'));
  if (!live) return 'No session is being played today.';
  const { day, s } = live;
  const head = `**${day}${s.games > 1 ? `, game ${s.game || 1} of ${s.games}` : ''}**${s.goal ? ` · ${s.goal}` : ''}`;
  return [head, ...s.pods.map((p) => `Pod ${p.pod}: ` +
    p.seats.map((x) => `${x.seat}. ${x.player} (${short(x.commander)})`).join(' · '))].join('\n');
}

// ---------- /iam ----------

async function iam(ix, env) {
  const o = options(ix);
  const uid = ix.member?.user?.id || ix.user?.id;
  const roster = await data(env, 'roster.json');
  const name = resolve(o.player || '', roster, new Map());
  if (!name) return `"${o.player}" isn't on the roster. Pick your name from the list.`;
  const mine = (roster.players || []).find((p) => p.discord_id === uid);
  if (mine?.name === name) return `You're already linked as ${name}.`;
  const taken = (roster.players || []).find((p) => p.name === name)?.discord_id;
  await submit(env, { type: 'tag', via: 'discord', player: name, discord_id: uid, reporter: who(ix, new Map()) });
  return `Linked you as ${name}. From the next post on, pods tag you and your replies count as your take.` +
    (mine ? ` (You were linked as ${mine.name}; that's removed.)` : '') +
    (taken ? ` ${name} was linked to <@${taken}>; that's replaced. If that was wrong, they can run /iam again.` : '') +
    ' It takes a minute or two to save.';
}

// ---------- autocomplete: /report winner, /start player, /iam player ----------

async function autocomplete(ix, env) {
  const focused = (ix.data.options || []).find((x) => x.focused);
  if (!focused) return [];
  const typed = String(focused.value || '').toLowerCase();
  let list = [];
  if (ix.data.name === 'report' && focused.name === 'winner') {
    const live = current(await data(env, 'pods.json'));
    if (live) for (const p of live.s.pods) for (const x of p.seats)
      list.push({ name: `${x.player} (pod ${p.pod}, ${short(x.commander)})`.slice(0, 100), value: x.player });
    list.push({ name: 'Draw', value: 'Draw' });
  } else if ((ix.data.name === 'start' || ix.data.name === 'iam') && focused.name === 'player') {
    list = (await data(env, 'roster.json')).players.map((p) => ({ name: p.name, value: p.name }));
  }
  return list.filter((c) => c.name.toLowerCase().includes(typed)).slice(0, 25);
}

// ---------- helpers ----------

/** The session being played: today's, else yesterday's (a late night), if not finished. */
export function current(saved, now = new Date()) {
  for (const day of [today(now), today(new Date(now - 864e5))]) {
    const s = saved.sessions?.[day];
    if (s && !s.done && s.pods?.length) return { day, s };
  }
  return null;
}

export function today(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}

/** "2026-11-22", "11/22" or "11/22/26" -> "2026-11-22", or '' if it isn't a date near today. */
export function parseDate(text, now = new Date()) {
  const t = text.trim();
  let y, m, d;
  let x = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (x) [y, m, d] = [+x[1], +x[2], +x[3]];
  else if ((x = t.match(/^(\d{1,2})\/(\d{1,2})(?:\/(\d{2}|\d{4}))?$/))) {
    [m, d] = [+x[1], +x[2]];
    y = x[3] ? (x[3].length === 2 ? 2000 + +x[3] : +x[3]) : +today(now).slice(0, 4);
  } else return '';
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return '';
  const days = (dt - new Date(today(now) + 'T00:00:00Z')) / 864e5;
  if (days < -60 || days > 366) return ''; // plan_sessions.py refuses these too
  return dt.toISOString().slice(0, 10);
}

/** Discord user ID -> player name: the roster's /iam links, then DISCORD_TAGS ("Eric=123,Alex=456" or JSON). */
export function tagIds(env, roster) {
  const raw = (env.DISCORD_TAGS || '').trim();
  let pairs = [];
  try {
    pairs = raw.startsWith('{') ? Object.entries(JSON.parse(raw))
      : raw.split(/[,;\n]/).filter((x) => x.includes('=')).map((x) => x.split('=', 2));
  } catch (e) { pairs = []; }
  const ids = new Map(pairs.filter(([, v]) => /^\d+$/.test(String(v).trim())).map(([k, v]) => [String(v).trim(), k.trim()]));
  for (const p of roster?.players || []) {
    if (!/^\d+$/.test(String(p.discord_id || ''))) continue;
    for (const [id, n] of ids) if (n.toLowerCase() === p.name.toLowerCase()) ids.delete(id); // /iam wins
    ids.set(String(p.discord_id), p.name);
  }
  return ids;
}

export function splitPlayers(text) {
  return text.replace(/<@!?(\d+)>/g, ' <@$1> ').split(/[,\s]+/).filter(Boolean);
}

/** A typed name or an @mention -> the roster name, or ''. Exact name first, then a unique start. */
export function resolve(word, roster, ids) {
  const m = word.match(/^<@!?(\d+)>$/);
  const n = (m ? ids.get(m[1]) || '' : word).trim().toLowerCase();
  if (!n) return '';
  const players = roster.players || [];
  const exact = players.find((p) => [p.name, p.topdeck_handle, p.topdeck_id].some((v) => (v || '').toLowerCase() === n));
  if (exact) return exact.name;
  const start = players.filter((p) => p.name.toLowerCase().startsWith(n));
  return start.length === 1 ? start[0].name : '';
}

/** A typed name or an @mention -> that player's name as seated now, or ''. */
function seatedName(s, typed, ids) {
  const m = typed.trim().match(/^<@!?(\d+)>$/);
  const n = (m ? ids.get(m[1]) || '' : typed).trim().toLowerCase();
  for (const p of s.pods) for (const x of p.seats) if (n && x.player.toLowerCase() === n) return x.player;
  return '';
}

function who(ix, ids) {
  const u = ix.member?.user || ix.user || {};
  return (ids.get(u.id) || ix.member?.nick || u.global_name || u.username || 'Discord').slice(0, 60);
}

const short = (c) => (c || '').split(' / ').map((p) => p.split(',')[0].split(' the ')[0].trim()).join('/');

function options(ix) {
  return Object.fromEntries((ix.data.options || []).map((x) => [x.name, x.value]));
}

/** A file from data/ on main, fresh (raw files are cached for minutes otherwise). */
async function data(env, file) {
  const repo = env.GITHUB_REPO || 'ejw179/cedh-playgroup';
  const headers = { 'User-Agent': 'cedh-playgroup-discord' };
  let url = `https://raw.githubusercontent.com/${repo}/main/data/${file}?t=${Date.now()}`;
  if (env.GITHUB_TOKEN) {
    url = `https://api.github.com/repos/${repo}/contents/data/${file}?ref=main`;
    Object.assign(headers, { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github.raw' });
  }
  const r = await fetch(url, { headers, cf: { cacheTtl: 0 } });
  if (!r.ok) throw new Error(`couldn't read ${file} from GitHub (HTTP ${r.status})`);
  return r.json();
}

/** Send a submission to the Apps Script, exactly like the site does (JSON as text/plain). */
async function submit(env, payload) {
  if (!env.RESULTS_URL || !env.PIN) throw new Error('the Worker is missing RESULTS_URL or PIN');
  const r = await fetch(env.RESULTS_URL, { method: 'POST', body: JSON.stringify({ ...payload, pin: env.PIN }) });
  const res = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
  if (!res.ok) throw new Error(`not saved: ${res.error || 'unknown error'}`);
  return res;
}

async function edit(ix, content) {
  await fetch(`${API}/webhooks/${ix.application_id}/${ix.token}/messages/@original`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: content.slice(0, 1900), allowed_mentions: { parse: [] } }),
  });
}

async function verify(request, body, publicKey) {
  const sig = request.headers.get('X-Signature-Ed25519');
  const ts = request.headers.get('X-Signature-Timestamp');
  if (!sig || !ts || !publicKey) return false;
  try {
    const key = await crypto.subtle.importKey('raw', hex(publicKey), { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify('Ed25519', key, hex(sig), new TextEncoder().encode(ts + body));
  } catch (e) {
    return false;
  }
}

const hex = (h) => new Uint8Array(h.match(/../g).map((b) => parseInt(b, 16)));
const json = (obj) => new Response(JSON.stringify(obj), { headers: { 'Content-Type': 'application/json' } });
const reply = (content) => json({ type: 4, data: { content, flags: EPHEMERAL } });
