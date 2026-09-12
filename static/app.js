'use strict';
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

// Text is always inserted with textContent, never innerHTML, so guest-supplied
// values such as names cannot inject markup.
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};
const money = n => '₹' + Number(n).toLocaleString('en-IN');

// A labelled identifier, e.g. "Booking ID  MGM-1A2B3C4D". The value always
// comes from the API, never generated in the browser.
function idField(label, value) {
  const wrap = el('span', 'idf');
  wrap.append(el('span', 'idf-label', label));
  wrap.append(el('span', 'idf-value', value));
  return wrap;
}

function idRow(pairs) {
  const row = el('div', 'id-row');
  pairs.filter(([, v]) => v !== undefined && v !== null && v !== '')
       .forEach(([label, value]) => row.append(idField(label, value)));
  return row;
}

let me = null;
let hotel = null;
let lastSearch = null;
let customerFilter = 'all';
// Selected organization and hotel persist across reloads for the customer flow.
let selection = { organization_id: null, hotel_id: null, hotel_name: '' };

function loadSelection() {
  try {
    const saved = JSON.parse(localStorage.getItem('meridian.selection') || '{}');
    if (saved && typeof saved === 'object') Object.assign(selection, saved);
  } catch (err) { /* a fresh selection is fine */ }
}

function saveSelection() {
  try { localStorage.setItem('meridian.selection', JSON.stringify(selection)); }
  catch (err) { /* private browsing: selection just will not persist */ }
}

function busy(box, text) {
  box.textContent = '';
  box.append(el('p', 'spinner', text || 'Loading…'));
}

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function toast(text, isError) {
  const box = $('#toast');
  box.textContent = text;
  box.className = 'toast' + (isError ? ' bad' : '');
  setTimeout(() => box.classList.add('hidden'), 3200);
}

/* ---------------- tabs, driven by role ---------------- */

const ROLE_LABELS = {
  PRODUCT_ADMIN: 'PRODUCT ADMIN',
  ORGANIZATION_ADMIN: 'ORGANIZATION ADMIN',
  ADMIN: 'HOTEL ADMIN',
  RECEPTIONIST: 'RECEPTIONIST',
  CUSTOMER: 'CUSTOMER',
};

const PORTALS = {
  CUSTOMER: ['Customer Portal', 'Search rooms and manage your stays'],
  ADMIN: ['Hotel Administration', 'Manage hotel operations and knowledge'],
  ORGANIZATION_ADMIN: ['Organization Administration', 'Manage hotels, rooms and staff'],
  PRODUCT_ADMIN: ['Platform Administration', 'Manage organizations across the platform'],
  RECEPTIONIST: ['Reception Desk', 'Manage assigned bookings and availability'],
};

// Plain Unicode only, so every glyph renders without a webfont.
const TAB_ICONS = {
  search: '\u25A6', mybookings: '\u2630', assistant: '\u2709', chat: '\u2139',
  'staff-bookings': '\u2630', cancellations: '\u21BA', 'manage-rooms': '\u25A6',
  'manage-hotel': '\u2302', 'manage-document': '\u2637', organizations: '\u25C8',
  'manage-hotels': '\u2302', assignments: '\u263A',
};

// Navigation is generated per role. A tab a role may not use is never rendered,
// and the server authorizes every request regardless of what the UI shows.
const TABS = {
  CUSTOMER: [['search', 'Find Room'], ['mybookings', 'My Bookings'],
             ['assistant', 'Booking Assistant'], ['chat', 'Hotel Information']],
  RECEPTIONIST: [['staff-bookings', 'Bookings'], ['cancellations', 'Cancellation Requests'],
                 ['manage-rooms', 'Rooms'], ['search', 'Availability']],
  ADMIN: [['staff-bookings', 'Bookings'], ['cancellations', 'Cancellation Requests'],
          ['manage-rooms', 'Rooms'], ['search', 'Availability'],
          ['manage-hotel', 'Hotel'], ['manage-document', 'Hotel PDF']],
  ORGANIZATION_ADMIN: [['organizations', 'Organization'], ['manage-hotels', 'Hotels'],
                       ['manage-rooms', 'Rooms'], ['assignments', 'Receptionist Assignments'],
                       ['staff-bookings', 'Bookings'], ['cancellations', 'Cancellation Requests'],
                       ['manage-document', 'Hotel PDF']],
  PRODUCT_ADMIN: [['organizations', 'Organizations'], ['manage-hotels', 'Hotels'],
                  ['assignments', 'Receptionist Assignments'], ['staff-bookings', 'Bookings']],
};

function showTab(id) {
  $$('.panel').forEach(p => p.classList.remove('active'));
  $('#' + id).classList.add('active');
  $$('#tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === id));
  if (id === 'mybookings') loadMyBookings();
  if (id === 'staff-bookings') loadAllBookings();
  if (id === 'cancellations') loadCancellations();
  if (id === 'manage-rooms') loadRoomsAdmin();
  if (id === 'manage-hotel') fillHotelForm();
  if (id === 'manage-document') loadDocumentPanel();
  if (id === 'organizations') loadOrganizations();
  if (id === 'manage-hotels') loadManagedHotels();
  if (id === 'assignments') loadAssignments();
  if (id === 'chat') updateChatHotelBadge();
}

function buildTabs() {
  const nav = $('#tabs');
  nav.textContent = '';
  (TABS[me.role] || TABS.CUSTOMER).forEach(([id, label], i) => {
    const b = el('button', 'tab' + (i === 0 ? ' active' : ''));
    if (TAB_ICONS[id]) b.append(el('span', 'ico', TAB_ICONS[id]));
    b.append(document.createTextNode(label));
    b.dataset.tab = id;
    b.onclick = () => showTab(id);
    nav.append(b);
  });
  // Only admins may create rooms; receptionists edit existing ones.
  const canCreateRooms = ['ADMIN', 'ORGANIZATION_ADMIN', 'PRODUCT_ADMIN'].includes(me.role);
  $('#room-form').classList.toggle('hidden', !canCreateRooms);
  // Only a product admin may create organizations.
  $('#org-form').classList.toggle('hidden', me.role !== 'PRODUCT_ADMIN');
  $('#new-hotel-form').classList.toggle('hidden',
    !['ORGANIZATION_ADMIN', 'PRODUCT_ADMIN'].includes(me.role));
  showTab((TABS[me.role] || TABS.CUSTOMER)[0][0]);
}

/* ---------------- session ---------------- */

// The identifiers each role must be able to read at a glance. Every value is
// the persistent one returned by the API for the signed-in user's own context.
function renderContextBar() {
  const bar = $('#context-bar');
  if (!bar || !me) return;
  bar.textContent = '';
  const pairs = [];
  if (me.role === 'CUSTOMER') {
    pairs.push(['Customer ID', me.id]);
    if (selection.organization_id) pairs.push(['Organization ID', selection.organization_id]);
    if (selection.hotel_id) pairs.push(['Hotel ID', selection.hotel_id]);
    if (hotel && hotel.contact_number && selection.hotel_id === hotel.id) {
      pairs.push(['Hotel contact', hotel.contact_number]);
    }
  } else {
    pairs.push(['Staff user ID', me.id]);
    pairs.push(['Organization ID', me.organization_id]);
    if (selection.hotel_id) pairs.push(['Hotel ID', selection.hotel_id]);
  }
  if (!pairs.length) { bar.classList.add('hidden'); return; }
  bar.append(idRow(pairs));
  bar.classList.remove('hidden');
}

function renderHeader() {
  if (!me) return;
  $('#avatar').textContent = (me.name || '?').trim().charAt(0).toUpperCase();
  $('#who-name').textContent = me.name;
  const badge = $('#role-badge');
  badge.textContent = ROLE_LABELS[me.role] || me.role;
  badge.className = 'role-badge role-' + me.role;
  const [title, subtitle] = PORTALS[me.role] || PORTALS.CUSTOMER;
  $('#portal-title').textContent = title;
  $('#portal-subtitle').textContent = subtitle;
  $('#portal').classList.remove('hidden');
  const parts = [];
  if (selection.organization_name) parts.push(selection.organization_name);
  else if (me.organization_id) parts.push(me.organization_id);
  if (selection.hotel_name) parts.push(selection.hotel_name);
  $('#who-context').textContent = parts.join(' · ');
  renderContextBar();
}

async function refreshSession() {
  const data = await api('/api/auth/me');
  me = data.user;
  const signedIn = Boolean(me);
  $('#gate').classList.toggle('active', !signedIn);
  $('#app').classList.toggle('hidden', !signedIn);
  $('#account').classList.toggle('hidden', !signedIn);
  $('#portal').classList.toggle('hidden', !signedIn);
  if (signedIn) {
    await loadOrganizationChoices();
    renderHeader();
    buildTabs();
  }
}

$('#logout').onclick = async () => {
  await api('/api/auth/logout', { method: 'POST', body: '{}' });
  me = null;
  location.reload();
};

$$('.swtab').forEach(b => b.onclick = () => {
  $$('.swtab').forEach(x => x.classList.toggle('active', x === b));
  $('#login-form').classList.toggle('hidden', b.dataset.form !== 'login');
  $('#register-form').classList.toggle('hidden', b.dataset.form !== 'register');
});

// Seeded demo accounts, documented in the README. The form is filled but never
// submitted automatically, so the judge sees exactly what is being signed in.
const DEMO_LOGINS = {
  admin: 'admin@meridiangrand.example',
  reception: 'reception@meridiangrand.example',
};
const DEMO_PASSWORD = 'Hackathon2026';

function useDemo(which) {
  $$('.swtab').forEach(x => x.classList.toggle('active', x.dataset.form === 'login'));
  $('#login-form').classList.remove('hidden');
  $('#register-form').classList.add('hidden');
  $('#li-email').value = DEMO_LOGINS[which];
  $('#li-password').value = DEMO_PASSWORD;
  $('#login-error').textContent = '';
  $('#li-email').scrollIntoView({ block: 'center' });
  $('#li-password').focus();
  toast('Demo credentials filled. Press Sign In to continue.');
}

$('#demo-admin').onclick = () => useDemo('admin');
$('#demo-reception').onclick = () => useDemo('reception');
$('#demo-customer').onclick = () => {
  $$('.swtab').forEach(x => x.classList.toggle('active', x.dataset.form === 'register'));
  $('#login-form').classList.add('hidden');
  $('#register-form').classList.remove('hidden');
  $('#register-error').textContent = '';
  $('#rg-name').scrollIntoView({ block: 'center' });
  $('#rg-name').focus();
};

$('#login-form').onsubmit = async e => {
  e.preventDefault();
  $('#login-error').textContent = '';
  try {
    await api('/api/auth/login', { method: 'POST', body: JSON.stringify({
      email: $('#li-email').value, password: $('#li-password').value }) });
    await refreshSession();
  } catch (err) { $('#login-error').textContent = err.message; }
};

$('#register-form').onsubmit = async e => {
  e.preventDefault();
  $('#register-error').textContent = '';
  try {
    await api('/api/auth/register', { method: 'POST', body: JSON.stringify({
      name: $('#rg-name').value, email: $('#rg-email').value,
      password: $('#rg-password').value, role: 'CUSTOMER' }) });
    await refreshSession();
  } catch (err) { $('#register-error').textContent = err.message; }
};

/* ---------------- hotel ---------------- */

/* Customer flow: organization -> hotel -> rooms -> availability -> booking. */

function fillSelect(select, rows, label, chosen) {
  select.textContent = '';
  rows.forEach(row => {
    const option = el('option', null, label(row));
    option.value = row.id;
    select.append(option);
  });
  if (chosen && rows.some(r => r.id === chosen)) select.value = chosen;
  return select.value;
}

async function loadOrganizationChoices() {
  try {
    const data = await api('/api/organizations');
    const rows = data.organizations || [];
    if (!rows.length) return;
    selection.organization_id = fillSelect(
      $('#org-select'), rows, o => o.name, selection.organization_id);
    const current = rows.find(o => o.id === selection.organization_id);
    selection.organization_name = current ? current.name : '';
    await loadHotelChoices();
  } catch (err) { /* the default hotel still works */ }
}

async function loadHotelChoices() {
  try {
    const query = selection.organization_id
      ? '?organization_id=' + encodeURIComponent(selection.organization_id) : '';
    const data = await api('/api/hotels' + query);
    const rows = data.hotels || [];
    const select = $('#hotel-select');
    if (!rows.length) {
      select.textContent = '';
      select.append(el('option', null, 'No hotels available'));
      selection.hotel_id = null;
      selection.hotel_name = '';
    } else {
      selection.hotel_id = fillSelect(
        select, rows, h => h.name + ' — ' + h.city, selection.hotel_id);
      const current = rows.find(h => h.id === selection.hotel_id);
      selection.hotel_name = current ? current.name : '';
      selection.hotel_contact = current ? current.contact_number : '';
    }
    saveSelection();
    renderHeader();
    updateChatHotelBadge();
    renderContextBar();
  } catch (err) { /* leave the previous selection in place */ }
}

$('#org-select').onchange = async () => {
  selection.organization_id = $('#org-select').value;
  const chosen = $('#org-select').selectedOptions[0];
  selection.organization_name = chosen ? chosen.textContent : '';
  selection.hotel_id = null;                 // force a fresh hotel choice
  await loadHotelChoices();
  $('#rooms').textContent = '';
};

$('#hotel-select').onchange = () => {
  selection.hotel_id = $('#hotel-select').value;
  const chosen = $('#hotel-select').selectedOptions[0];
  selection.hotel_name = chosen ? chosen.textContent.split(' — ')[0] : '';
  loadHotelChoices();   // refresh the cached contact number for the new hotel
  saveSelection();
  renderHeader();
  updateChatHotelBadge();
  renderContextBar();
  $('#rooms').textContent = '';
};

function updateChatHotelBadge() {
  const parts = [selection.hotel_name || 'Default hotel'];
  if (selection.hotel_id) parts.push(selection.hotel_id);
  if (selection.hotel_contact) parts.push(selection.hotel_contact);
  $('#chat-hotel').textContent = parts.join(' · ');
}

async function loadHotel() {
  hotel = (await api('/api/hotel')).hotel;
  if (!hotel) return;
  $('#hotel-name').textContent = hotel.name;
  $('#hotel-city').textContent = hotel.city;
  $('#gate-hotel').textContent = hotel.name;
  $('#gate-address').textContent = hotel.address + ', ' + hotel.city;
  $('#gate-desc').textContent = hotel.description || '';
  $('#gate-desc').textContent = hotel.description;
  $('#gate-phone').textContent = hotel.contact_number;
  $('#gate-email').textContent = hotel.email;
  $('#gate-status').textContent = hotel.status;
}

function fillHotelForm() {
  if (!hotel) return;
  $('#ht-id').value = hotel.id;              // business identifier, not editable
  $('#ht-name').value = hotel.name; $('#ht-city').value = hotel.city;
  $('#ht-address').value = hotel.address; $('#ht-desc').value = hotel.description;
  $('#ht-phone').value = hotel.contact_number; $('#ht-email').value = hotel.email;
  $('#ht-status').value = hotel.status;
}

$('#hotel-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const data = await api('/api/hotel', { method: 'PUT', body: JSON.stringify({
      name: $('#ht-name').value, city: $('#ht-city').value, address: $('#ht-address').value,
      description: $('#ht-desc').value, contact_number: $('#ht-phone').value,
      email: $('#ht-email').value, status: $('#ht-status').value }) });
    hotel = data.hotel;
    await loadHotel();
    toast('Hotel details saved.');
  } catch (err) { toast(err.message, true); }
};

/* ---------------- room search and booking ---------------- */

function roomCard(room) {
  const card = el('article', 'card');
  const body = el('div');
  body.append(el('h3', null, room.room_type));
  body.append(el('p', 'muted', 'Sleeps ' + room.capacity + ' · ' + money(room.price_per_night) + ' per night'));
  body.append(idRow([['Room Number', room.room_number], ['Room ID', room.room_id]]));
  if (room.description) body.append(el('p', null, room.description));
  if (room.amenities) body.append(el('p', 'amenities', room.amenities));
  body.append(el('strong', null, money(room.total_amount) + ' for ' + room.nights +
    (room.nights > 1 ? ' nights' : ' night')));
  card.append(body);
  if (me && me.role === 'CUSTOMER') {
    const btn = el('button', 'primary', 'Book this room');
    btn.onclick = () => book(room);
    card.append(btn);
  } else {
    card.append(el('span', 'badge ok-badge', 'Available'));
  }
  return card;
}

$('#search-form').onsubmit = async e => {
  e.preventDefault();
  lastSearch = { check_in: $('#check-in').value, check_out: $('#check-out').value,
                 guests: +$('#guests').value };
  // Never silently fall back to the default hotel when one has been chosen.
  if (selection.hotel_id) lastSearch.hotel_id = selection.hotel_id;
  const box = $('#rooms');
  busy(box, 'Checking availability…');
  try {
    const data = await api('/api/availability?' + new URLSearchParams(lastSearch));
    if (!data.rooms.length) {
      box.append(el('p', 'notice',
        'No rooms match those dates and guest count at this hotel. Try different dates.'));
      return;
    }
    data.rooms.forEach(r => box.append(roomCard(r)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
};

async function book(room) {
  if (!confirm('Book room ' + room.room_number + ' for ' + money(room.total_amount) + '?')) return;
  try {
    const data = await api('/api/bookings', { method: 'POST', body: JSON.stringify({
      room_id: room.room_id, check_in: lastSearch.check_in,
      check_out: lastSearch.check_out, guests: lastSearch.guests }) });
    showConfirmation(data.booking);
    $('#search-form').requestSubmit();
  } catch (err) { toast(err.message, true); }
}

function showConfirmation(b) {
  // A confirmation the judge can read, not just a toast that disappears.
  const box = $('#confirmation');
  box.textContent = '';
  const card = el('article', 'card');
  const body = el('div');
  body.append(el('h3', null, 'Booking confirmed'));
  body.append(el('p', 'muted', b.hotel_name + ' · ' + b.room_type));
  body.append(idRow([
    ['Booking ID', b.id],
    ['Customer ID', b.customer_id],
    ['Organization ID', b.organization_id],
    ['Hotel ID', b.hotel_id],
    ['Room ID', b.room_id],
    ['Room Number', b.room_number],
  ]));
  body.append(el('p', null, b.check_in + ' → ' + b.check_out + ' · ' +
    b.guests + ' guest' + (b.guests > 1 ? 's' : '') + ' · ' + money(b.total_amount)));
  body.append(el('span', 'badge status-' + b.status, b.status));
  card.append(body);
  box.append(card);
  toast('Booking ' + b.id + ' confirmed · ' + money(b.total_amount));
}

/* ---------------- booking cards ---------------- */

function bookingCard(b, staff) {
  const card = el('article', 'card');
  const body = el('div');
  body.append(el('h3', null, b.hotel_name + ' · Room ' + b.room_number));
  body.append(el('p', 'muted', b.room_type + ' · ' + b.guests + ' guest' + (b.guests > 1 ? 's' : '')));
  body.append(el('p', null, b.check_in + ' → ' + b.check_out));
  if (staff) body.append(el('p', 'muted', b.customer_name + ' · ' + b.customer_email));
  // Staff see the customer's id in booking detail; a customer only ever sees
  // their own bookings, so the id shown there is their own.
  body.append(idRow([
    ['Booking ID', b.id],
    ['Customer ID', b.customer_id],
    ['Hotel ID', b.hotel_id],
    ['Room ID', b.room_id],
    ['Room Number', b.room_number],
  ]));
  const meta = el('p');
  meta.append(el('strong', null, money(b.total_amount)));
  body.append(meta);
  body.append(el('span', 'badge status-' + b.display_status, b.display_status.replace(/_/g, ' ')));
  card.append(body);

  const actions = el('div', 'actions');
  if (b.status === 'CONFIRMED' && !b.is_past) {
    const btn = el('button', 'danger', b.can_cancel_directly ? 'Cancel booking' : 'Request cancellation');
    btn.title = b.can_cancel_directly
      ? 'Free cancellation until the day before check-in.'
      : 'Past the 24-hour deadline, so staff must review this request.';
    btn.onclick = () => cancelBooking(b, staff);
    actions.append(btn);
  }
  if (staff && b.status === 'CANCELLATION_REQUESTED') {
    const ok = el('button', 'primary', 'Approve');
    ok.onclick = () => review(b.id, true);
    const no = el('button', 'danger', 'Reject');
    no.onclick = () => review(b.id, false);
    actions.append(ok, no);
  }
  if (actions.childNodes.length) card.append(actions);
  return card;
}

async function cancelBooking(b, staff) {
  const msg = b.can_cancel_directly
    ? 'Cancel booking ' + b.id + '?'
    : 'The free-cancellation deadline has passed. Submit a cancellation request for staff review?';
  if (!confirm(msg)) return;
  try {
    const data = await api('/api/bookings/' + b.id + '/cancel', { method: 'POST', body: '{}' });
    toast(data.booking.status === 'CANCELLED'
      ? 'Booking cancelled.'
      : 'Cancellation request submitted for staff review.');
    staff ? loadAllBookings() : loadMyBookings();
  } catch (err) { toast(err.message, true); }
}

async function review(id, approve) {
  try {
    await api('/api/bookings/' + id + (approve ? '/approve-cancellation' : '/reject-cancellation'),
      { method: 'POST', body: '{}' });
    toast(approve ? 'Cancellation approved, room released.' : 'Request rejected, booking stays confirmed.');
    loadCancellations();
  } catch (err) { toast(err.message, true); }
}

/* ---------------- lists ---------------- */

function matchesFilter(b) {
  if (customerFilter === 'all') return true;
  if (customerFilter === 'upcoming') return b.display_status === 'CONFIRMED' || b.display_status === 'CANCELLATION_REQUESTED';
  if (customerFilter === 'completed') return b.display_status === 'COMPLETED';
  return b.display_status === 'CANCELLED';
}

async function loadMyBookings() {
  const box = $('#bookings');
  box.textContent = '';
  try {
    const data = await api('/api/bookings');
    const rows = data.bookings.filter(matchesFilter);
    if (!rows.length) {
      box.append(el('p', 'notice',
        'No bookings in this view yet. Use Find Room to search availability and make one.'));
      return;
    }
    rows.forEach(b => box.append(bookingCard(b, false)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

$$('#cust-filters .chip').forEach(c => c.onclick = () => {
  customerFilter = c.dataset.filter;
  $$('#cust-filters .chip').forEach(x => x.classList.toggle('active', x === c));
  loadMyBookings();
});

async function loadAllBookings() {
  const box = $('#all-bookings');
  box.textContent = '';
  const params = new URLSearchParams();
  if ($('#bk-query').value.trim()) params.set('q', $('#bk-query').value.trim());
  if ($('#bk-status').value) params.set('status', $('#bk-status').value);
  try {
    const data = await api('/api/bookings?' + params);
    if (!data.bookings.length) {
      box.append(el('p', 'notice', 'No bookings match this search or filter.'));
      return;
    }
    data.bookings.forEach(b => box.append(bookingCard(b, true)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

$('#filter-form').onsubmit = e => { e.preventDefault(); loadAllBookings(); };

async function loadCancellations() {
  const box = $('#cancel-queue');
  box.textContent = '';
  try {
    const data = await api('/api/cancellations');
    if (!data.bookings.length) {
      box.append(el('p', 'notice',
        'Nothing awaiting review. Late cancellation requests appear here for a decision.'));
      return;
    }
    data.bookings.forEach(b => box.append(bookingCard(b, true)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

/* ---------------- room administration ---------------- */

async function loadRoomsAdmin() {
  const box = $('#rooms-admin');
  busy(box, 'Loading rooms…');
  try {
    const path = selection.hotel_id
      ? '/api/hotels/' + encodeURIComponent(selection.hotel_id) + '/rooms' : '/api/rooms';
    const data = await api(path);
    if (!data.rooms.length) { box.textContent = ''; box.append(el('p', 'notice', 'No rooms yet.')); return; }
    box.textContent = '';
    data.rooms.forEach(room => {
      const card = el('article', 'card');
      const body = el('div');
      body.append(el('h3', null, room.room_type));
      body.append(el('p', 'muted', 'Sleeps ' + room.capacity + ' · ' + money(room.price_per_night) + ' per night'));
      body.append(idRow([['Room Number', room.room_number], ['Room ID', room.id],
                         ['Hotel ID', room.hotel_id]]));
      if (room.description) body.append(el('p', null, room.description));
      if (room.amenities) body.append(el('p', 'amenities', room.amenities));
      body.append(el('span', 'badge status-' + room.availability_status, room.availability_status));
      card.append(body);

      const actions = el('div', 'actions');
      const price = el('button', null, 'Edit price');
      price.onclick = () => editPrice(room);
      actions.append(price);
      if (me.role === 'ADMIN') {
        const active = room.availability_status === 'ACTIVE';
        const btn = el('button', active ? 'danger' : null, active ? 'Deactivate' : 'Activate');
        btn.onclick = () => setRoomStatus(room.id, active ? 'deactivate' : 'activate');
        actions.append(btn);
      }
      card.append(actions);
      box.append(card);
    });
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

async function editPrice(room) {
  const value = prompt('New price per night for room ' + room.room_number, room.price_per_night);
  if (value === null) return;
  try {
    await api('/api/rooms/' + room.id, { method: 'PUT', body: JSON.stringify({ price_per_night: value }) });
    toast('Room updated.');
    loadRoomsAdmin();
  } catch (err) { toast(err.message, true); }
}

async function setRoomStatus(id, action) {
  try {
    await api('/api/rooms/' + id + '/' + action, { method: 'POST', body: '{}' });
    toast('Room ' + (action === 'activate' ? 'activated.' : 'deactivated.'));
    loadRoomsAdmin();
  } catch (err) { toast(err.message, true); }
}

$('#room-form').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/rooms', { method: 'POST', body: JSON.stringify({
      room_number: $('#rm-number').value, room_type: $('#rm-type').value,
      capacity: $('#rm-capacity').value, price_per_night: $('#rm-price').value,
      description: $('#rm-desc').value, amenities: $('#rm-amenities').value,
      hotel_id: selection.hotel_id || undefined }) });
    e.target.reset();
    toast('Room added.');
    loadRoomsAdmin();
  } catch (err) { toast(err.message, true); }
};

/* ---------------- organizations, hotels, assignments ---------------- */

async function loadOrganizations() {
  const box = $('#org-list');
  busy(box, 'Loading organizations…');
  try {
    const data = await api('/api/organizations');
    const rows = data.organizations || [];
    box.textContent = '';
    if (!rows.length) { box.append(el('p', 'notice', 'No organizations yet.')); return; }
    for (const org of rows) {
      const card = el('article', 'card');
      const body = el('div');
      body.append(el('h3', null, org.name));
      body.append(idRow([['Organization ID', org.id]]));
      if (org.status) body.append(el('span', 'badge status-' + org.status, org.status));
      card.append(body);
      if (me.role === 'PRODUCT_ADMIN' && org.status) {
        const actions = el('div', 'actions');
        const active = org.status === 'ACTIVE';
        const toggle = el('button', null, active ? 'Deactivate' : 'Activate');
        toggle.onclick = () => setOrgStatus(org.id, active ? 'INACTIVE' : 'ACTIVE');
        const view = el('button', null, 'View hotels');
        view.onclick = () => { selection.organization_id = org.id; saveSelection();
                               showTab('manage-hotels'); };
        actions.append(toggle, view);
        card.append(actions);
      }
      box.append(card);
    }
  } catch (err) { box.textContent = ''; box.append(el('p', 'notice', err.message)); }
}

async function setOrgStatus(id, status) {
  try {
    await api('/api/organizations/' + id, { method: 'PUT', body: JSON.stringify({ status }) });
    toast('Organization ' + (status === 'ACTIVE' ? 'activated.' : 'deactivated.'));
    loadOrganizations();
  } catch (err) { toast(err.message, true); }
}

$('#org-form').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/organizations', { method: 'POST',
      body: JSON.stringify({ name: $('#org-name').value }) });
    e.target.reset();
    toast('Organization created.');
    loadOrganizations();
    loadOrganizationChoices();
  } catch (err) { toast(err.message, true); }
};

async function loadManagedHotels() {
  const box = $('#hotel-list');
  busy(box, 'Loading hotels…');
  try {
    const query = (me.role === 'PRODUCT_ADMIN' && selection.organization_id)
      ? '?organization_id=' + encodeURIComponent(selection.organization_id) : '';
    const data = await api('/api/hotels' + query);
    const rows = data.hotels || [];
    box.textContent = '';
    if (!rows.length) { box.append(el('p', 'notice', 'No hotels in this organization yet.')); return; }
    rows.forEach(h => {
      const card = el('article', 'card');
      const body = el('div');
      body.append(el('h3', null, h.name));
      body.append(el('p', 'muted', h.address + ', ' + h.city));
      body.append(idRow([['Hotel ID', h.id], ['Organization ID', h.organization_id]]));
      if (h.contact_number) body.append(el('p', 'muted', 'Contact: ' + h.contact_number));
      body.append(el('span', 'badge status-' + h.status, h.status));
      card.append(body);
      const actions = el('div', 'actions');
      const manage = el('button', null, 'Manage rooms');
      manage.onclick = () => {
        selection.hotel_id = h.id; selection.hotel_name = h.name;
        saveSelection(); renderHeader(); showTab('manage-rooms');
      };
      actions.append(manage);
      card.append(actions);
      box.append(card);
    });
  } catch (err) { box.textContent = ''; box.append(el('p', 'notice', err.message)); }
}

$('#new-hotel-form').onsubmit = async e => {
  e.preventDefault();
  try {
    await api('/api/hotels', { method: 'POST', body: JSON.stringify({
      name: $('#nh-name').value, city: $('#nh-city').value, address: $('#nh-address').value,
      contact_number: $('#nh-phone').value, email: $('#nh-email').value,
      organization_id: me.role === 'PRODUCT_ADMIN' ? selection.organization_id : undefined }) });
    e.target.reset();
    toast('Hotel added.');
    loadManagedHotels();
    loadOrganizationChoices();
  } catch (err) { toast(err.message, true); }
};

async function loadAssignments() {
  const box = $('#assignment-list');
  busy(box, 'Loading receptionists…');
  try {
    const [people, hotelData] = await Promise.all([
      api('/api/users?role=RECEPTIONIST'),
      api('/api/hotels'),
    ]);
    const rows = people.users || [];
    const allHotels = hotelData.hotels || [];
    box.textContent = '';
    if (!rows.length) { box.append(el('p', 'notice', 'No receptionists in this organization.')); return; }
    rows.forEach(person => {
      const card = el('article', 'card');
      const body = el('div');
      body.append(el('h3', null, person.name));
      body.append(el('p', 'muted', person.email));
      const assigned = person.hotel_ids || [];
      if (!assigned.length) {
        body.append(el('p', 'muted', 'No hotels assigned — this receptionist sees nothing.'));
      } else {
        assigned.forEach(id => {
          const match = allHotels.find(h => h.id === id);
          const chip = el('span', 'badge status-ACTIVE', match ? match.name : id);
          const remove = el('button', null, 'Unassign');
          remove.onclick = () => changeAssignment('/api/assignments/remove', person.id, id);
          body.append(chip, remove);
        });
      }
      const row = el('div', 'assign-row');
      const picker = el('select');
      allHotels.filter(h => !assigned.includes(h.id)).forEach(h => {
        const option = el('option', null, h.name);
        option.value = h.id;
        picker.append(option);
      });
      const add = el('button', 'primary', 'Assign');
      add.onclick = () => {
        if (!picker.value) { toast('No hotel left to assign.', true); return; }
        changeAssignment('/api/assignments', person.id, picker.value);
      };
      row.append(picker, add);
      body.append(row);
      card.append(body);
      box.append(card);
    });
  } catch (err) { box.textContent = ''; box.append(el('p', 'notice', err.message)); }
}

async function changeAssignment(path, userId, hotelId) {
  try {
    await api(path, { method: 'POST', body: JSON.stringify({ user_id: userId, hotel_id: hotelId }) });
    toast('Assignment updated.');
    loadAssignments();
  } catch (err) { toast(err.message, true); }
}

/* ---------------- hotel PDF (admin) ---------------- */

async function loadDocumentPanel() {
  // The admin picks which managed hotel's PDF to inspect or replace.
  try {
    const data = await api('/api/hotels');
    const rows = data.hotels || [];
    if (rows.length) {
      fillSelect($('#doc-hotel'), rows, h => h.name, selection.hotel_id || rows[0].id);
    }
  } catch (err) { /* fall back to the default hotel */ }
  loadDocument();
}

$('#doc-hotel').onchange = () => loadDocument();

async function loadDocument() {
  const box = $('#doc-info');
  busy(box, 'Loading document…');
  const target = $('#doc-hotel').value || selection.hotel_id;
  try {
    const query = target ? '?hotel_id=' + encodeURIComponent(target) : '';
    const payload = await api('/api/hotel/document' + query);
    const doc = payload.document;
    box.textContent = '';
    if (!doc) {
      box.append(el('p', 'notice',
        'No document uploaded for ' + (payload.hotel ? payload.hotel.name : 'this hotel') +
        '. The chatbot cannot answer questions about it until one is added.'));
      return;
    }
    const card = el('article', 'card');
    const body = el('div');
    body.append(el('h3', null, doc.original_name));
    body.append(el('p', 'muted', doc.pages + ' page(s) · ' + doc.chunk_count + ' indexed sections'));
    body.append(el('p', 'muted', 'Indexed ' + doc.uploaded_at));
    body.append(el('span', 'badge status-ACTIVE', 'Source of truth for the chatbot'));
    card.append(body);
    box.append(card);
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

$('#doc-form').onsubmit = async e => {
  e.preventDefault();
  $('#doc-error').textContent = '';
  const file = $('#doc-file').files[0];
  if (!file) return;
  try {
    // Raw PDF body keeps the server free of a multipart parser.
    const headers = { 'Content-Type': 'application/pdf', 'X-Filename': file.name };
    const target = $('#doc-hotel').value || selection.hotel_id;
    if (target) headers['X-Hotel-Id'] = target;
    const res = await fetch('/api/hotel/document', {
      method: 'POST', credentials: 'same-origin', headers, body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Upload failed');
    toast('Indexed ' + data.document.pages + ' page(s) into ' + data.indexed_chunks + ' sections.');
    e.target.reset();
    loadDocument();
  } catch (err) { $('#doc-error').textContent = err.message; }
};

/* ---------------- grounded hotel chat ---------------- */

function message(text, type, citations) {
  const art = el('article', type);
  if (type === 'assistant') art.append(el('span', 'avatar', 'M'));
  const body = el('div');
  body.append(el('p', null, text));
  (citations || []).forEach(c => body.append(el('span', 'citation',
    'Source: ' + (c.hotel ? c.hotel + ' · ' : '') + c.section +
    (c.page ? ' (page ' + c.page + ')' : ''))));
  art.append(body);
  $('#messages').append(art);
  $('#messages').scrollTop = $('#messages').scrollHeight;
}

$('#chat-form').onsubmit = async e => {
  e.preventDefault();
  const q = $('#question').value.trim();
  if (!q) return;
  message(q, 'user');
  $('#question').value = '';
  try {
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify(
      { question: q, hotel_id: selection.hotel_id || undefined }) });
    message(data.answer, 'assistant', data.citations);
  } catch (err) { message(err.message, 'assistant'); }
};

$$('#chat .suggestions button').forEach(b => b.onclick = () => {
  $('#question').value = b.textContent;
  $('#chat-form').requestSubmit();
});

/* ---------------- controlled booking assistant ---------------- */

function assistantMessage(text, type, pending) {
  const art = el('article', type);
  if (type === 'assistant') art.append(el('span', 'avatar', 'M'));
  const body = el('div');
  // Replies are multi-line lists; render each line as its own paragraph so no
  // markup is ever injected.
  String(text).split(/\r?\n/).forEach(line => body.append(el('p', null, line)));
  if (pending) body.append(el('span', 'citation', 'Awaiting your confirmation — reply yes or no'));
  art.append(body);
  $('#asst-messages').append(art);
  $('#asst-messages').scrollTop = $('#asst-messages').scrollHeight;
}

$('#asst-form').onsubmit = async e => {
  e.preventDefault();
  const text = $('#asst-input').value.trim();
  if (!text) return;
  assistantMessage(text, 'user');
  $('#asst-input').value = '';
  try {
    const data = await api('/api/assistant', { method: 'POST', body: JSON.stringify(
      { message: text, hotel_id: selection.hotel_id || undefined }) });
    assistantMessage(data.reply, 'assistant', data.requires_confirmation);
    if (data.action === 'created' || data.action === 'cancelled') loadMyBookings();
  } catch (err) { assistantMessage(err.message, 'assistant'); }
};

$$('#assistant .suggestions button').forEach(b => b.onclick = () => {
  $('#asst-input').value = b.textContent;
  $('#asst-form').requestSubmit();
});

/* ---------------- start ---------------- */

// Local calendar dates, not UTC, so the defaults are never yesterday.
function localISO(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
(function seedDates() {
  const today = new Date();
  const inDays = n => { const d = new Date(today); d.setDate(today.getDate() + n); return localISO(d); };
  $('#check-in').min = localISO(today);
  $('#check-in').value = inDays(1);
  $('#check-out').min = inDays(1);
  $('#check-out').value = inDays(3);
  $('#check-in').onchange = () => {
    const next = new Date($('#check-in').value + 'T00:00:00');
    next.setDate(next.getDate() + 1);
    $('#check-out').min = localISO(next);
    if ($('#check-out').value <= $('#check-in').value) $('#check-out').value = localISO(next);
  };
})();

loadSelection();
loadHotel().then(refreshSession).catch(() => {});
