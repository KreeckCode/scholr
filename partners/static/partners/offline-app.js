/*
 * METP offline register: take registers, record exam scores and add participants
 * without signal. Every change goes to the IndexedDB outbox first and is sent when
 * the device is back online.
 */
(function () {
    'use strict';

    var store = window.MetpOffline;
    var config = JSON.parse(document.getElementById('offline-config').textContent);
    var app = document.getElementById('app');
    var statusBar = document.getElementById('status');
    var state = { me: null, signInNeeded: false, lockedBy: null, flash: null, registration: null };

    var MARKS = [
        ['present', 'Present', 'border-teal-600 bg-teal-50 text-teal-800'],
        ['late', 'Late', 'border-amber-500 bg-amber-50 text-amber-900'],
        ['absent', 'Absent', 'border-red-500 bg-red-50 text-red-700'],
        ['excused', 'Excused', 'border-slate-400 bg-slate-100 text-slate-700']
    ];

    // ==================== DOM HELPERS ====================

    function h(tag, attrs, children) {
        var element = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (name) {
            var value = attrs[name];
            if (value === null || value === undefined || value === false) { return; }
            if (name === 'onclick' || name === 'onchange' || name === 'onsubmit' || name === 'oninput') {
                element.addEventListener(name.slice(2), value);
            } else if (name === 'text') {
                element.textContent = value;
            } else {
                element.setAttribute(name, value === true ? '' : value);
            }
        });
        (Array.isArray(children) ? children : [children]).forEach(function (child) {
            if (child === null || child === undefined || child === false) { return; }
            element.appendChild(typeof child === 'string' || typeof child === 'number' ? document.createTextNode(String(child)) : child);
        });
        return element;
    }

    function button(label, onclick, variant, extra) {
        var styles = {
            primary: 'rounded-full bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-50',
            ghost: 'rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-50',
            danger: 'rounded-full border border-red-200 bg-white px-4 py-2.5 text-sm font-semibold text-red-700 hover:bg-red-50'
        };
        return h('button', Object.assign({ type: 'button', class: styles[variant || 'ghost'], onclick: onclick }, extra || {}), label);
    }

    function card(children, extraClass) {
        return h('section', { class: 'rounded-2xl border border-slate-200 bg-white ' + (extraClass || '') }, children);
    }

    function formatDate(iso) {
        if (!iso) { return ''; }
        var date = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
        return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    }

    function formatTime(iso) {
        if (!iso) { return 'never'; }
        return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }

    function packUrl(cohortId) {
        return config.packUrlTemplate.replace('00000000-0000-0000-0000-000000000000', encodeURIComponent(cohortId));
    }

    function isExpired(pack) {
        return pack.expires_at && new Date(pack.expires_at) < new Date();
    }

    function setFlash(text, tone) {
        state.flash = { text: text, tone: tone || 'info', until: Date.now() + 6000 };
        window.setTimeout(function () {
            if (state.flash && Date.now() >= state.flash.until) { state.flash = null; render(); }
        }, 6100);
    }

    // ==================== DATA ====================

    function loadPack(cohortId) { return store.get('packs', cohortId); }
    function savePack(pack) { return store.put('packs', pack, pack.cohort.id); }

    function downloadPack(cohortId) {
        return fetch(packUrl(cohortId), { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (response) {
                if (response.status === 401 || response.status === 403) { state.signInNeeded = true; throw new Error('Sign in again to download this cohort.'); }
                if (response.status === 404) { throw new Error('That cohort is not available to you.'); }
                return response.json().then(function (body) {
                    if (!response.ok) { throw new Error(body.message || 'The cohort could not be downloaded.'); }
                    return body;
                });
            })
            .then(function (pack) {
                return mergeLocalChanges(pack).then(savePack).then(function () { return pack; });
            });
    }

    // I'm reapplying unsent changes on top of a fresh pack so nothing seems to vanish
    function mergeLocalChanges(pack) {
        return store.getAll('outbox').then(function (ops) {
            ops.sort(function (a, b) { return a.seq - b.seq; });
            ops.forEach(function (op) { applyToPack(pack, op.kind, op.payload, true); });
            return pack;
        });
    }

    function applyToPack(pack, kind, payload, pending) {
        if (kind === 'attendance.set') {
            var mark = (pack.attendance || []).find(function (row) { return row.session === payload.session && row.membership === payload.membership; });
            if (mark) { mark.status = payload.status; mark.pending = pending; } else {
                pack.attendance.push({ session: payload.session, membership: payload.membership, status: payload.status, pending: pending });
            }
        } else if (kind === 'result.set') {
            var result = (pack.results || []).find(function (row) { return row.membership === payload.membership && row.course === payload.course; });
            var score = payload.exam_score === null ? null : String(payload.exam_score);
            if (result) { result.exam_score = score; result.pending = pending; } else {
                pack.results.push({ membership: payload.membership, course: payload.course, exam_score: score, status: 'in_progress', version: null, signed_off: false, pending: pending });
            }
        } else if (kind === 'participant.create') {
            var id = 'client:' + payload.client_uuid;
            if (!pack.memberships.some(function (row) { return row.id === id; })) {
                pack.memberships.push({ id: id, name: payload.first_name + ' ' + payload.last_name, status: 'enrolled', pending: true });
                pack.memberships.sort(function (a, b) { return a.name.localeCompare(b.name); });
            }
        }
    }

    function record(pack, kind, payload) {
        return store.queue(kind, payload).then(function () {
            applyToPack(pack, kind, payload, true);
            return savePack(pack);
        }).then(function () {
            requestBackgroundSync();
            if (navigator.onLine) { syncNow(true); } else { renderStatus(); }
        });
    }

    function requestBackgroundSync() {
        if (state.registration && 'sync' in state.registration) {
            state.registration.sync.register('metp-outbox').catch(function () {});
        }
    }

    // ==================== SYNC ====================

    var syncing = false;

    function syncNow(quiet) {
        if (syncing || !navigator.onLine || state.lockedBy) { return Promise.resolve(); }
        syncing = true;
        renderStatus();
        return store.flush(config).then(function (summary) {
            syncing = false;
            if (summary.needsSignIn) {
                state.signInNeeded = true;
                setFlash('Your session has ended. Sign in again so your changes can be sent.', 'warn');
            } else if (summary.problems) {
                setFlash(summary.problems + ' change(s) could not be applied. See Problems.', 'warn');
            } else if (!quiet && summary.applied) {
                setFlash(summary.applied + ' change(s) sent.', 'good');
            }
            render();
        }, function () {
            syncing = false;
            renderStatus();
        });
    }

    function refreshPacks() {
        return store.getAll('packs').then(function (packs) {
            return Promise.all(packs.map(function (pack) {
                return downloadPack(pack.cohort.id).catch(function () { return null; });
            }));
        });
    }

    // ==================== STATUS BAR ====================

    function renderStatus() {
        Promise.all([store.pendingCount(), store.getMeta()]).then(function (values) {
            var pending = values[0];
            var meta = values[1];
            var online = navigator.onLine;
            statusBar.replaceChildren(
                h('div', { class: 'flex items-center gap-2' }, [
                    h('span', { class: 'inline-block h-2.5 w-2.5 rounded-full ' + (online ? 'bg-teal-500' : 'bg-slate-400'), 'aria-hidden': 'true' }),
                    h('span', { class: 'font-medium' }, online ? 'Online' : 'Offline'),
                    h('span', { class: 'text-slate-500' }, syncing ? 'Sending...' : (pending ? pending + ' waiting to send' : 'All changes sent')),
                ]),
                h('div', { class: 'flex items-center gap-3' }, [
                    h('span', { class: 'hidden text-slate-500 sm:inline' }, 'Last sent ' + formatTime(meta.lastSyncAt)),
                    online && pending && !syncing ? button('Send now', function () { syncNow(false); }, 'primary', { class: 'rounded-full bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white' }) : null
                ])
            );
        });
    }

    // ==================== VIEWS ====================

    function flashBanner() {
        if (!state.flash || Date.now() >= state.flash.until) { return null; }
        var tones = {
            info: 'border-sky-200 bg-sky-50 text-sky-900',
            good: 'border-teal-200 bg-teal-50 text-teal-900',
            warn: 'border-amber-200 bg-amber-50 text-amber-900',
            bad: 'border-red-200 bg-red-50 text-red-800'
        };
        return h('div', { class: 'mb-4 rounded-xl border px-4 py-3 text-sm ' + tones[state.flash.tone], role: 'status' }, state.flash.text);
    }

    function signInBanner() {
        if (!state.signInNeeded) { return null; }
        var next = encodeURIComponent(config.appUrl);
        return h('div', { class: 'mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900' }, [
            h('span', {}, 'You are signed out. Your changes are safe on this device.'),
            h('a', { class: 'font-semibold underline', href: config.loginUrl + '?next=' + next }, 'Sign in')
        ]);
    }

    function header(title, back) {
        return h('div', { class: 'mb-5 flex items-center gap-3' }, [
            back ? h('a', { href: back, class: 'rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700', 'aria-label': 'Back' }, 'Back') : null,
            h('h1', { class: 'font-display text-xl font-semibold text-slate-900' }, title)
        ]);
    }

    function viewHome() {
        return Promise.all([store.getAll('packs'), store.getAll('problems')]).then(function (values) {
            var packs = values[0];
            var problems = values[1];
            var list = packs.length ? packs.map(function (pack) {
                return h('a', { href: '#/cohort/' + pack.cohort.id, class: 'block rounded-2xl border border-slate-200 bg-white p-4 hover:border-teal-300' }, [
                    h('p', { class: 'font-semibold text-slate-900' }, pack.cohort.name),
                    h('p', { class: 'text-sm text-slate-500' }, pack.cohort.location.name),
                    h('p', { class: 'mt-2 text-xs ' + (isExpired(pack) ? 'text-amber-700' : 'text-slate-500') },
                        isExpired(pack) ? 'Copy expired. Update it when you have signal.' : 'Downloaded ' + formatTime(pack.generated_at))
                ]);
            }) : [card(h('div', { class: 'p-5 text-sm text-slate-600' }, [
                h('p', {}, 'No cohorts on this device yet.'),
                h('p', { class: 'mt-2' }, 'While online, open a cohort in the portal and press Prepare for offline.')
            ]))];

            return h('div', {}, [
                header('Offline registers'),
                state.me ? h('p', { class: 'mb-4 text-sm text-slate-500' }, 'Signed in as ' + state.me.name) : null,
                h('div', { class: 'grid gap-3 sm:grid-cols-2' }, list),
                h('div', { class: 'mt-6 flex flex-wrap gap-3' }, [
                    navigator.onLine && packs.length ? button('Update all cohorts', function () {
                        setFlash('Updating...', 'info');
                        refreshPacks().then(function () { setFlash('Cohorts updated.', 'good'); render(); });
                    }, 'ghost') : null,
                    problems.length ? h('a', { href: '#/problems', class: 'rounded-full border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm font-semibold text-amber-900' }, 'Problems (' + problems.length + ')') : null,
                    h('a', { href: config.portalUrl, class: 'rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800' }, 'Open the portal'),
                    button('Clear this device', clearDevice, 'danger')
                ])
            ]);
        });
    }

    function viewCohort(cohortId) {
        return loadPack(cohortId).then(function (pack) {
            if (!pack) { return notFound(); }
            var today = new Date().toISOString().slice(0, 10);
            var sessions = pack.sessions.slice().sort(function (a, b) { return a.date < b.date ? -1 : 1; });
            var upcoming = sessions.filter(function (session) { return session.date >= today; });
            var past = sessions.filter(function (session) { return session.date < today; }).reverse();
            var expired = isExpired(pack);

            function sessionRow(session) {
                var marked = pack.attendance.filter(function (row) { return row.session === session.id; }).length;
                return h('a', { href: '#/session/' + pack.cohort.id + '/' + session.id, class: 'flex items-center justify-between gap-3 px-4 py-3 hover:bg-slate-50' }, [
                    h('span', {}, [
                        h('span', { class: 'block text-sm font-semibold text-slate-900' }, session.course + ' · ' + formatDate(session.date)),
                        h('span', { class: 'block text-xs text-slate-500' }, session.hours + ' h · ' + marked + ' of ' + pack.memberships.length + ' marked')
                    ]),
                    h('span', { class: 'text-sm font-medium text-teal-700' }, 'Register')
                ]);
            }

            return h('div', {}, [
                header(pack.cohort.name, '#/'),
                expired ? h('div', { class: 'mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900' }, 'This copy has expired. Update it when you have signal before recording more.') : null,
                h('div', { class: 'mb-5 flex flex-wrap gap-2' }, [
                    h('a', { href: '#/results/' + pack.cohort.id, class: 'rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-800' }, 'Exam scores'),
                    pack.permissions.add_participants && !expired ? h('a', { href: '#/add/' + pack.cohort.id, class: 'rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-800' }, 'Add participant') : null,
                    navigator.onLine ? button('Update this cohort', function () {
                        downloadPack(pack.cohort.id).then(function () { setFlash('Cohort updated.', 'good'); render(); }, function (error) { setFlash(error.message, 'bad'); render(); });
                    }, 'ghost') : null
                ]),
                card([
                    h('h2', { class: 'border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-900' }, 'Upcoming sessions'),
                    upcoming.length ? h('div', { class: 'divide-y divide-slate-100' }, upcoming.map(sessionRow)) : h('p', { class: 'px-4 py-4 text-sm text-slate-500' }, 'None scheduled.')
                ], 'mb-4 overflow-hidden'),
                card([
                    h('h2', { class: 'border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-900' }, 'Earlier sessions'),
                    past.length ? h('div', { class: 'divide-y divide-slate-100' }, past.map(sessionRow)) : h('p', { class: 'px-4 py-4 text-sm text-slate-500' }, 'None yet.')
                ], 'overflow-hidden')
            ]);
        });
    }

    function viewSession(cohortId, sessionId) {
        return loadPack(cohortId).then(function (pack) {
            if (!pack) { return notFound(); }
            var session = pack.sessions.find(function (row) { return row.id === sessionId; });
            if (!session) { return notFound(); }
            var editable = pack.permissions.record && !isExpired(pack);
            var marks = {};
            pack.attendance.filter(function (row) { return row.session === sessionId; }).forEach(function (row) { marks[row.membership] = row; });

            var rows = pack.memberships.filter(function (membership) { return membership.status !== 'withdrawn'; }).map(function (membership) {
                var current = marks[membership.id];
                var group = h('div', { class: 'flex flex-wrap gap-1.5', role: 'group', 'aria-label': 'Attendance for ' + membership.name },
                    MARKS.map(function (mark) {
                        var on = current && current.status === mark[0];
                        return h('button', {
                            type: 'button',
                            'aria-pressed': on ? 'true' : 'false',
                            disabled: !editable,
                            class: 'min-w-[4.5rem] rounded-lg border px-2.5 py-2 text-xs font-semibold ' + (on ? mark[2] : 'border-slate-200 bg-white text-slate-600'),
                            onclick: function () {
                                record(pack, 'attendance.set', { session: sessionId, membership: membership.id, status: mark[0] }).then(render);
                            }
                        }, mark[1]);
                    })
                );
                return h('li', { class: 'flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between' }, [
                    h('span', { class: 'text-sm font-medium text-slate-900' }, [membership.name, current && current.pending ? h('span', { class: 'ml-2 text-xs font-normal text-amber-700' }, 'not sent yet') : null]),
                    group
                ]);
            });

            return h('div', {}, [
                header(session.course + ' register', '#/cohort/' + cohortId),
                h('p', { class: 'mb-4 text-sm text-slate-500' }, formatDate(session.date) + ' · ' + session.hours + ' hours'),
                editable ? null : h('div', { class: 'mb-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600' }, 'This register is read-only on this device.'),
                editable && rows.length ? h('div', { class: 'mb-3' }, button('Mark everyone present', function () {
                    var chain = Promise.resolve();
                    pack.memberships.filter(function (membership) { return membership.status !== 'withdrawn' && !(marks[membership.id] && marks[membership.id].status === 'present'); })
                        .forEach(function (membership) {
                            chain = chain.then(function () { return store.queue('attendance.set', { session: sessionId, membership: membership.id, status: 'present' }); })
                                .then(function (op) { applyToPack(pack, 'attendance.set', op.payload, true); });
                        });
                    chain.then(function () { return savePack(pack); }).then(function () {
                        requestBackgroundSync();
                        if (navigator.onLine) { syncNow(true); }
                        render();
                    });
                }, 'ghost')) : null,
                card(rows.length ? h('ul', { class: 'divide-y divide-slate-100' }, rows) : h('p', { class: 'px-4 py-6 text-sm text-slate-500' }, 'No participants yet.'), 'overflow-hidden')
            ]);
        });
    }

    function viewResults(cohortId, courseCode) {
        return loadPack(cohortId).then(function (pack) {
            if (!pack) { return notFound(); }
            var course = pack.courses.find(function (row) { return row.code === courseCode; }) || pack.courses[0];
            if (!course) { return notFound(); }
            var editable = pack.permissions.record && !isExpired(pack);
            var results = {};
            pack.results.filter(function (row) { return row.course === course.code; }).forEach(function (row) { results[row.membership] = row; });

            var tabs = h('nav', { class: 'mb-4 flex flex-wrap gap-1.5', 'aria-label': 'Courses' }, pack.courses.map(function (row) {
                var on = row.code === course.code;
                return h('a', { href: '#/results/' + cohortId + '/' + encodeURIComponent(row.code), class: 'rounded-full px-3 py-1.5 text-xs font-semibold ' + (on ? 'bg-teal-600 text-white' : 'border border-slate-200 bg-white text-slate-700') }, row.code);
            }));

            var rows = pack.memberships.filter(function (membership) { return membership.status !== 'withdrawn'; }).map(function (membership) {
                var result = results[membership.id];
                var locked = result && result.signed_off;
                var input = h('input', {
                    type: 'number', min: '0', max: '100', step: '0.01', inputmode: 'decimal',
                    value: result && result.exam_score !== null ? result.exam_score : '',
                    disabled: !editable || locked,
                    'aria-label': 'Exam score for ' + membership.name,
                    class: 'w-24 rounded-lg border border-slate-200 px-2.5 py-2 text-right text-sm',
                    onchange: function (event) {
                        var raw = event.target.value.trim();
                        var score = raw === '' ? null : Number(raw);
                        if (score !== null && (isNaN(score) || score < 0 || score > 100)) {
                            event.target.classList.add('border-red-500');
                            return;
                        }
                        event.target.classList.remove('border-red-500');
                        record(pack, 'result.set', {
                            membership: membership.id, course: course.code, exam_score: score,
                            base_version: result && typeof result.version === 'number' ? result.version : null
                        }).then(render);
                    }
                });
                return h('li', { class: 'flex items-center justify-between gap-3 px-4 py-3' }, [
                    h('span', { class: 'text-sm font-medium text-slate-900' }, [
                        membership.name,
                        locked ? h('span', { class: 'ml-2 text-xs font-normal text-slate-500' }, 'signed off') : null,
                        result && result.pending ? h('span', { class: 'ml-2 text-xs font-normal text-amber-700' }, 'not sent yet') : null
                    ]),
                    input
                ]);
            });

            return h('div', {}, [
                header('Exam scores', '#/cohort/' + cohortId),
                tabs,
                h('p', { class: 'mb-3 text-sm text-slate-500' }, course.code + ': ' + course.title + '. Scores are percentages and are saved as you leave each box.'),
                card(rows.length ? h('ul', { class: 'divide-y divide-slate-100' }, rows) : h('p', { class: 'px-4 py-6 text-sm text-slate-500' }, 'No participants yet.'), 'overflow-hidden')
            ]);
        });
    }

    function viewAdd(cohortId) {
        return loadPack(cohortId).then(function (pack) {
            if (!pack || !pack.permissions.add_participants) { return notFound(); }
            function input(name, label, type, required) {
                return h('label', { class: 'block' }, [
                    h('span', { class: 'mb-1.5 block text-sm font-medium text-slate-800' }, label + (required ? ' *' : '')),
                    h('input', { name: name, type: type || 'text', required: required, class: 'block w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm', autocomplete: 'off' })
                ]);
            }
            var form = h('form', {
                class: 'grid gap-4 p-5 sm:grid-cols-2',
                onsubmit: function (event) {
                    event.preventDefault();
                    var data = new FormData(form);
                    var first = String(data.get('first_name') || '').trim();
                    var last = String(data.get('last_name') || '').trim();
                    var phone = String(data.get('phone') || '').trim();
                    if (!first || !last) { setFlash('A first name and a last name are required.', 'bad'); render(); return; }
                    if (phone && !/^\+?1?\d{9,15}$/.test(phone)) { setFlash('Use international format for the phone, for example +27821234567.', 'bad'); render(); return; }
                    record(pack, 'participant.create', {
                        client_uuid: store.uuid4(), cohort: pack.cohort.id, first_name: first, last_name: last,
                        phone: phone, church_name: String(data.get('church_name') || '').trim()
                    }).then(function () {
                        setFlash(first + ' ' + last + ' added. They can be marked on registers straight away.', 'good');
                        location.hash = '#/cohort/' + cohortId;
                    });
                }
            }, [
                input('first_name', 'First name', 'text', true),
                input('last_name', 'Last name', 'text', true),
                input('phone', 'Phone', 'tel', false),
                input('church_name', 'Church', 'text', false),
                h('div', { class: 'sm:col-span-2' }, h('button', { type: 'submit', class: 'rounded-full bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white' }, 'Add participant'))
            ]);
            return h('div', {}, [header('Add a participant', '#/cohort/' + cohortId), card(form)]);
        });
    }

    function viewProblems() {
        return store.getAll('problems').then(function (problems) {
            var rows = problems.map(function (problem) {
                var title = problem.kind === 'result.set' ? 'Exam score' : problem.kind === 'attendance.set' ? 'Register mark' : 'New participant';
                return h('li', { class: 'flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between' }, [
                    h('span', {}, [
                        h('span', { class: 'block text-sm font-semibold text-slate-900' }, title + (problem.outcome === 'conflict' ? ' needs your coordinator' : ' was not applied')),
                        h('span', { class: 'block text-xs text-slate-500' }, problem.message || 'No reason given.')
                    ]),
                    button('Dismiss', function () { store.remove('problems', problem.op_id).then(render); }, 'ghost')
                ]);
            });
            return h('div', {}, [
                header('Problems', '#/'),
                h('p', { class: 'mb-4 text-sm text-slate-500' }, 'Conflicts are listed for your coordinator in the portal too. Dismissing here only clears this list.'),
                card(rows.length ? h('ul', { class: 'divide-y divide-slate-100' }, rows) : h('p', { class: 'px-4 py-6 text-sm text-slate-500' }, 'No problems.'), 'overflow-hidden')
            ]);
        });
    }

    function viewLocked() {
        return store.pendingCount().then(function (pending) {
            return card(h('div', { class: 'p-5' }, [
                h('h1', { class: 'text-lg font-semibold text-slate-900' }, 'This device has changes from another account'),
                h('p', { class: 'mt-2 text-sm text-slate-600' }, pending + ' change(s) recorded by ' + state.lockedBy.name + ' have not been sent. Sign in as ' + state.lockedBy.name + ' to send them, or discard them to continue as ' + state.me.name + '.'),
                h('div', { class: 'mt-4 flex flex-wrap gap-3' }, [
                    h('a', { class: 'rounded-full bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white', href: config.loginUrl + '?next=' + encodeURIComponent(config.appUrl) }, 'Sign in as ' + state.lockedBy.name),
                    button('Discard and continue', function () {
                        if (!window.confirm('Discard ' + pending + ' unsent change(s)? This cannot be undone.')) { return; }
                        wipe().then(function () { return store.setMeta({ owner: state.me }); }).then(function () { state.lockedBy = null; render(); });
                    }, 'danger')
                ])
            ]));
        });
    }

    function notFound() {
        return h('div', {}, [header('Not on this device', '#/'), h('p', { class: 'text-sm text-slate-600' }, 'Prepare this cohort for offline use while you have signal.')]);
    }

    // ==================== DEVICE ====================

    function wipe() {
        var cacheWipe = 'caches' in window ? caches.keys().then(function (keys) {
            return Promise.all(keys.filter(function (key) { return key.indexOf('metp-offline') === 0; }).map(function (key) { return caches.delete(key); }));
        }) : Promise.resolve();
        return Promise.all([store.clearStores(['packs', 'outbox', 'problems']), cacheWipe]);
    }

    function clearDevice() {
        store.pendingCount().then(function (pending) {
            var warning = pending ? pending + ' change(s) have not been sent and will be lost. ' : '';
            if (!window.confirm(warning + 'Remove all METP data from this device?')) { return; }
            wipe().then(function () { return store.setMeta({ owner: null, lastSyncAt: null }); }).then(function () {
                setFlash('This device no longer holds any METP data.', 'good');
                render();
            });
        });
    }

    // ==================== ROUTER ====================

    function route() {
        var parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
        if (state.lockedBy) { return viewLocked(); }
        switch (parts[0]) {
        case 'cohort': return viewCohort(parts[1]);
        case 'session': return viewSession(parts[1], parts[2]);
        case 'results': return viewResults(parts[1], parts[2]);
        case 'add': return viewAdd(parts[1]);
        case 'problems': return viewProblems();
        default: return viewHome();
        }
    }

    function render() {
        renderStatus();
        return route().then(function (view) {
            app.replaceChildren(h('div', {}, [signInBanner(), flashBanner(), view]));
        }, function (error) {
            app.replaceChildren(h('p', { class: 'text-sm text-red-700' }, 'Something went wrong: ' + error.message));
        });
    }

    // ==================== START ====================

    function checkAccount() {
        if (!navigator.onLine) { return Promise.resolve(); }
        return fetch(config.meUrl, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } }).then(function (response) {
            if (response.status === 401) { state.signInNeeded = true; return null; }
            if (!response.ok) { return null; }
            return response.json().then(function (me) {
                state.me = me;
                state.signInNeeded = false;
                return Promise.all([store.getMeta(), store.pendingCount()]).then(function (values) {
                    var meta = values[0];
                    var pending = values[1];
                    if (!meta.owner) { return store.setMeta({ owner: { id: me.id, name: me.name } }); }
                    if (meta.owner.id === me.id) { return null; }
                    if (pending) { state.lockedBy = meta.owner; return null; }
                    // I'm wiping another person's cohorts so they never stay readable on a shared device
                    return wipe().then(function () { return store.setMeta({ owner: { id: me.id, name: me.name } }); });
                });
            });
        }, function () { return null; });
    }

    function prepareFromQuery() {
        var params = new URLSearchParams(location.search);
        var cohortId = params.get('prepare');
        if (!cohortId) { return Promise.resolve(); }
        history.replaceState(null, '', config.appUrl + location.hash);
        if (!navigator.onLine) { setFlash('Connect to the internet to prepare a cohort.', 'warn'); return Promise.resolve(); }
        if (state.lockedBy) { return Promise.resolve(); }
        return downloadPack(cohortId).then(function (pack) {
            setFlash(pack.cohort.name + ' is ready to use without signal.', 'good');
            location.hash = '#/cohort/' + pack.cohort.id;
        }, function (error) {
            setFlash(error.message, 'bad');
        });
    }

    function registerWorker() {
        if (!('serviceWorker' in navigator)) { return Promise.resolve(); }
        return navigator.serviceWorker.register(config.swUrl).then(function (registration) {
            state.registration = registration;
        }, function () {});
    }

    window.addEventListener('hashchange', render);
    window.addEventListener('online', function () { checkAccount().then(function () { return syncNow(true); }).then(render); });
    window.addEventListener('offline', renderStatus);
    window.setInterval(function () {
        if (navigator.onLine) {
            store.pendingCount().then(function (pending) { if (pending) { syncNow(true); } });
        }
    }, 60000);
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.addEventListener('message', function (event) {
            if (event.data && event.data.type === 'metp-synced') { render(); }
        });
    }

    var csrf = store.readCookie('csrftoken');
    (csrf ? store.setMeta({ csrf: csrf }) : Promise.resolve())
        .then(registerWorker)
        .then(checkAccount)
        .then(prepareFromQuery)
        .then(function () { return navigator.onLine ? syncNow(true) : null; })
        .then(render);
})();
