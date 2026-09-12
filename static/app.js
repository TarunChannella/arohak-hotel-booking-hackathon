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

let me = null;
let hotel = null;
let lastSearch = null;
let customerFilter = 'all';

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

const TABS = {
  CUSTOMER: [['search', 'Find a room'], ['mybookings', 'My bookings'],
             ['assistant', 'Booking assistant'], ['chat', 'Hotel info']],
  RECEPTIONIST: [['staff-bookings', 'Bookings'], ['cancellations', 'Cancellations'],
                 ['manage-rooms', 'Rooms'], ['search', 'Availability'], ['chat', 'Hotel info']],
  ADMIN: [['staff-bookings', 'Bookings'], ['cancellations', 'Cancellations'],
          ['manage-rooms', 'Rooms'], ['manage-hotel', 'Hotel'],
          ['manage-document', 'Hotel PDF'], ['search', 'Availability'],
          ['chat', 'Hotel info']],
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
  if (id === 'manage-document') loadDocument();
}

function buildTabs() {
  const nav = $('#tabs');
  nav.textContent = '';
  (TABS[me.role] || TABS.CUSTOMER).forEach(([id, label], i) => {
    const b = el('button', 'tab' + (i === 0 ? ' active' : ''), label);
    b.dataset.tab = id;
    b.onclick = () => showTab(id);
    nav.append(b);
  });
  // Only admins may create rooms; receptionists edit existing ones.
  $('#room-form').classList.toggle('hidden', me.role !== 'ADMIN');
  showTab((TABS[me.role] || TABS.CUSTOMER)[0][0]);
}

/* ---------------- session ---------------- */

async function refreshSession() {
  const data = await api('/api/auth/me');
  me = data.user;
  const signedIn = Boolean(me);
  $('#gate').classList.toggle('active', !signedIn);
  $('#app').classList.toggle('hidden', !signedIn);
  $('#who').classList.toggle('hidden', !signedIn);
  $('#logout').classList.toggle('hidden', !signedIn);
  if (signedIn) {
    $('#who').textContent = me.name + ' · ' + me.role;
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

async function loadHotel() {
  hotel = (await api('/api/hotel')).hotel;
  if (!hotel) return;
  $('#hotel-name').textContent = hotel.name;
  $('#hotel-city').textContent = hotel.city;
  $('#gate-hotel').textContent = hotel.name;
  $('#gate-address').textContent = hotel.address + ', ' + hotel.city;
  $('#gate-desc').textContent = hotel.description;
  $('#gate-phone').textContent = hotel.contact_number;
  $('#gate-email').textContent = hotel.email;
  $('#gate-status').textContent = hotel.status;
}

function fillHotelForm() {
  if (!hotel) return;
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
  body.append(el('h3', null, 'Room ' + room.room_number + ' · ' + room.room_type));
  body.append(el('p', 'muted', 'Sleeps ' + room.capacity + ' · ' + money(room.price_per_night) + ' per night'));
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
  lastSearch = { check_in: $('#check-in').value, check_out: $('#check-out').value, guests: +$('#guests').value };
  const box = $('#rooms');
  box.textContent = '';
  try {
    const data = await api('/api/availability?' + new URLSearchParams(lastSearch));
    if (!data.rooms.length) { box.append(el('p', 'notice', 'No rooms available for those dates and guest count.')); return; }
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
  showTab('mybookings');
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
  const meta = el('p');
  meta.append(el('span', 'mono', b.id));
  meta.append(document.createTextNode(' · '));
  meta.append(el('strong', null, money(b.total_amount)));
  body.append(meta);
  body.append(el('span', 'badge status-' + b.display_status, b.display_status.replace(/_/g, ' ')));
  card.append(body);

  const actions = el('div', 'actions');
  if (b.status === 'CONFIRMED' && !b.is_past) {
    const btn = el('button', null, b.can_cancel_directly ? 'Cancel booking' : 'Request cancellation');
    btn.title = b.can_cancel_directly
      ? 'Free cancellation until the day before check-in.'
      : 'Past the 24-hour deadline, so staff must review this request.';
    btn.onclick = () => cancelBooking(b, staff);
    actions.append(btn);
  }
  if (staff && b.status === 'CANCELLATION_REQUESTED') {
    const ok = el('button', 'primary', 'Approve');
    ok.onclick = () => review(b.id, true);
    const no = el('button', null, 'Reject');
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
    if (!rows.length) { box.append(el('p', 'notice', 'No bookings to show.')); return; }
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
    if (!data.bookings.length) { box.append(el('p', 'notice', 'No bookings match.')); return; }
    data.bookings.forEach(b => box.append(bookingCard(b, true)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

$('#filter-form').onsubmit = e => { e.preventDefault(); loadAllBookings(); };

async function loadCancellations() {
  const box = $('#cancel-queue');
  box.textContent = '';
  try {
    const data = await api('/api/cancellations');
    if (!data.bookings.length) { box.append(el('p', 'notice', 'No cancellation requests awaiting review.')); return; }
    data.bookings.forEach(b => box.append(bookingCard(b, true)));
  } catch (err) { box.append(el('p', 'notice', err.message)); }
}

/* ---------------- room administration ---------------- */

async function loadRoomsAdmin() {
  const box = $('#rooms-admin');
  box.textContent = '';
  try {
    const data = await api('/api/rooms');
    data.rooms.forEach(room => {
      const card = el('article', 'card');
      const body = el('div');
      body.append(el('h3', null, 'Room ' + room.room_number + ' · ' + room.room_type));
      body.append(el('p', 'muted', 'Sleeps ' + room.capacity + ' · ' + money(room.price_per_night) + ' per night'));
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
        const btn = el('button', null, active ? 'Deactivate' : 'Activate');
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
      description: $('#rm-desc').value, amenities: $('#rm-amenities').value }) });
    e.target.reset();
    toast('Room added.');
    loadRoomsAdmin();
  } catch (err) { toast(err.message, true); }
};

/* ---------------- hotel PDF (admin) ---------------- */

async function loadDocument() {
  const box = $('#doc-info');
  box.textContent = '';
  try {
    const doc = (await api('/api/hotel/document')).document;
    if (!doc) { box.append(el('p', 'notice', 'No document indexed yet.')); return; }
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
    const res = await fetch('/api/hotel/document', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/pdf', 'X-Filename': file.name },
      body: file,
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
    'Source: ' + c.section + (c.page ? ' (page ' + c.page + ')' : ''))));
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
    const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ question: q }) });
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
    const data = await api('/api/assistant', { method: 'POST', body: JSON.stringify({ message: text }) });
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

loadHotel().then(refreshSession).catch(() => {});
