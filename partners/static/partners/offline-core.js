/*
 * Shared offline storage and sync for the METP register app.
 * Loaded by the page with a <script> tag and by the service worker with importScripts,
 * so it is a classic script that attaches itself to the global object.
 */
(function (root) {
    'use strict';

    var DB_NAME = 'metp-offline';
    var DB_VERSION = 1;
    var BATCH_SIZE = 100;
    var dbPromise = null;
    var flushing = null;

    function promisify(request) {
        return new Promise(function (resolve, reject) {
            request.onsuccess = function () { resolve(request.result); };
            request.onerror = function () { reject(request.error); };
        });
    }

    function finished(transaction) {
        return new Promise(function (resolve, reject) {
            transaction.oncomplete = function () { resolve(); };
            transaction.onerror = function () { reject(transaction.error); };
            transaction.onabort = function () { reject(transaction.error); };
        });
    }

    function openDb() {
        if (dbPromise) { return dbPromise; }
        dbPromise = new Promise(function (resolve, reject) {
            var request = root.indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = function () {
                var db = request.result;
                if (!db.objectStoreNames.contains('meta')) { db.createObjectStore('meta'); }
                if (!db.objectStoreNames.contains('packs')) { db.createObjectStore('packs'); }
                if (!db.objectStoreNames.contains('outbox')) {
                    db.createObjectStore('outbox', { keyPath: 'op_id' }).createIndex('seq', 'seq');
                }
                if (!db.objectStoreNames.contains('problems')) { db.createObjectStore('problems', { keyPath: 'op_id' }); }
            };
            request.onsuccess = function () {
                var db = request.result;
                db.onversionchange = function () { db.close(); dbPromise = null; };
                resolve(db);
            };
            request.onerror = function () { dbPromise = null; reject(request.error); };
        });
        return dbPromise;
    }

    function get(store, key) {
        return openDb().then(function (db) { return promisify(db.transaction(store).objectStore(store).get(key)); });
    }

    function getAll(store) {
        return openDb().then(function (db) { return promisify(db.transaction(store).objectStore(store).getAll()); });
    }

    function put(store, value, key) {
        return openDb().then(function (db) {
            var transaction = db.transaction(store, 'readwrite');
            if (key === undefined) { transaction.objectStore(store).put(value); } else { transaction.objectStore(store).put(value, key); }
            return finished(transaction);
        });
    }

    function remove(store, key) {
        return openDb().then(function (db) {
            var transaction = db.transaction(store, 'readwrite');
            transaction.objectStore(store).delete(key);
            return finished(transaction);
        });
    }

    function clearStores(names) {
        return openDb().then(function (db) {
            var transaction = db.transaction(names, 'readwrite');
            names.forEach(function (name) { transaction.objectStore(name).clear(); });
            return finished(transaction);
        });
    }

    function uuid4() {
        if (root.crypto && typeof root.crypto.randomUUID === 'function') { return root.crypto.randomUUID(); }
        var bytes = new Uint8Array(16);
        root.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        var hex = Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
        return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
    }

    function getMeta() {
        return get('meta', 'state').then(function (state) { return state || {}; });
    }

    function setMeta(changes) {
        return getMeta().then(function (state) {
            var next = Object.assign({}, state, changes);
            return put('meta', next, 'state').then(function () { return next; });
        });
    }

    function deviceId() {
        return getMeta().then(function (state) {
            if (state.device_id) { return state.device_id; }
            var id = uuid4();
            return setMeta({ device_id: id }).then(function () { return id; });
        });
    }

    // I'm keying ops so two edits of the same mark or result can be merged before sending
    function opKey(kind, payload) {
        if (kind === 'result.set') { return 'result:' + payload.membership + ':' + payload.course; }
        if (kind === 'attendance.set') { return 'attendance:' + payload.session + ':' + payload.membership; }
        return null;
    }

    // I'm updating an unsent change in place; one already sent stays as it is and a new op follows it
    function queue(kind, payload) {
        var key = opKey(kind, payload);
        var now = new Date().toISOString();
        return openDb().then(function (db) {
            var transaction = db.transaction(['meta', 'outbox'], 'readwrite');
            var meta = transaction.objectStore('meta');
            var outbox = transaction.objectStore('outbox');
            var queued = null;
            var cursorRequest = outbox.openCursor();
            cursorRequest.onsuccess = function () {
                var cursor = cursorRequest.result;
                if (cursor) {
                    var op = cursor.value;
                    if (key && op.key === key && !op.sent && !queued) {
                        var basePayload = Object.assign({}, op.payload, payload);
                        if (kind === 'result.set') { basePayload.base_version = op.payload.base_version; }
                        op.payload = basePayload;
                        op.recorded_at = now;
                        cursor.update(op);
                        queued = op;
                    }
                    cursor.continue();
                    return;
                }
                if (queued) { return; }
                var stateRequest = meta.get('state');
                stateRequest.onsuccess = function () {
                    var state = stateRequest.result || {};
                    var seq = (state.seq || 0) + 1;
                    state.seq = seq;
                    meta.put(state, 'state');
                    queued = { op_id: uuid4(), kind: kind, key: key, payload: payload, recorded_at: now, seq: seq, sent: false };
                    outbox.put(queued);
                };
            };
            return finished(transaction).then(function () { return queued; });
        });
    }

    function pendingCount() {
        return getAll('outbox').then(function (ops) { return ops.length; });
    }

    function readCookie(name) {
        if (typeof document === 'undefined') { return ''; }
        var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : '';
    }

    function outcomeOf(result) {
        return result.result === 'duplicate' ? result.original : result.result;
    }

    // I'm holding back a second edit of the same result until the first returns its new version
    function flush(config) {
        if (flushing) { return flushing; }
        flushing = (function () {
            var summary = { applied: 0, problems: 0, retry: 0, needsSignIn: false, error: null };
            var csrf = readCookie('csrftoken');
            var stateChain = csrf ? setMeta({ csrf: csrf }) : getMeta();

            return Promise.all([stateChain, deviceId()]).then(function (values) {
                var state = values[0];
                var device = values[1];
                var token = csrf || state.csrf || '';

                function round() {
                    return getAll('outbox').then(function (ops) {
                        ops.sort(function (a, b) { return a.seq - b.seq; });
                        if (!ops.length) { return null; }
                        var batch = [];
                        var seen = {};
                        ops.forEach(function (op) {
                            if (batch.length >= BATCH_SIZE) { return; }
                            if (op.key && seen[op.key]) { return; }
                            if (op.key) { seen[op.key] = true; }
                            batch.push(op);
                        });
                        return markSent(batch).then(function () { return send(batch); }).then(function (keepGoing) {
                            return keepGoing ? round() : null;
                        });
                    });
                }

                function send(batch) {
                    var body = JSON.stringify({
                        device_id: device,
                        operations: batch.map(function (op) {
                            return { op_id: op.op_id, kind: op.kind, payload: op.payload, recorded_at: op.recorded_at };
                        })
                    });
                    return fetch(config.syncUrl, {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token, 'X-Requested-With': 'XMLHttpRequest' },
                        body: body
                    }).then(function (response) {
                        if (response.status === 401 || response.status === 403) { summary.needsSignIn = true; return false; }
                        if (response.status === 429) { summary.error = 'busy'; return false; }
                        if (!response.ok) { summary.error = 'server'; return false; }
                        return response.json().then(function (data) { return apply(batch, data.results || []); });
                    }, function () {
                        summary.error = 'offline';
                        return false;
                    });
                }

                function apply(batch, results) {
                    var byId = {};
                    batch.forEach(function (op) { byId[op.op_id] = op; });
                    var work = [];
                    var sawRetry = false;
                    results.forEach(function (result) {
                        var op = byId[result.op_id];
                        if (!op) { return; }
                        var outcome = outcomeOf(result);
                        if (outcome === 'applied' || outcome === 'superseded') {
                            summary.applied += 1;
                            work.push(remove('outbox', op.op_id).then(function () { return afterApplied(op, result); }));
                        } else if (outcome === 'conflict' || outcome === 'rejected') {
                            summary.problems += 1;
                            work.push(put('problems', {
                                op_id: op.op_id, kind: op.kind, payload: op.payload, outcome: outcome,
                                message: result.message || '', at: new Date().toISOString()
                            }).then(function () { return remove('outbox', op.op_id); }));
                        } else {
                            summary.retry += 1;
                            sawRetry = true;
                        }
                    });
                    // I'm stopping when nothing settled so an unexpected reply can never loop forever
                    return Promise.all(work).then(function () { return !sawRetry && work.length > 0; });
                }

                return round().then(function () {
                    if (!summary.error && !summary.needsSignIn) { return setMeta({ lastSyncAt: new Date().toISOString() }); }
                    return null;
                }).then(function () { return summary; });
            });
        })().then(function (summary) { flushing = null; return summary; }, function (error) { flushing = null; throw error; });
        return flushing;
    }

    function markSent(batch) {
        return openDb().then(function (db) {
            var transaction = db.transaction('outbox', 'readwrite');
            var store = transaction.objectStore('outbox');
            batch.forEach(function (op) { op.sent = true; store.put(op); });
            return finished(transaction);
        });
    }

    // I'm rebasing later edits of the same result on the version the server just returned
    function afterApplied(op, result) {
        if (op.kind !== 'result.set' || typeof result.version !== 'number') { return Promise.resolve(); }
        return getAll('outbox').then(function (ops) {
            var updates = ops.filter(function (other) { return other.key === op.key; });
            return Promise.all(updates.map(function (other) {
                other.payload.base_version = result.version;
                return put('outbox', other);
            }));
        }).then(function () {
            return getAll('packs').then(function (packs) {
                return Promise.all(packs.map(function (pack) {
                    var changed = false;
                    (pack.results || []).forEach(function (row) {
                        if (row.membership === op.payload.membership && row.course === op.payload.course) {
                            row.version = result.version;
                            if (result.status) { row.status = result.status; }
                            changed = true;
                        }
                    });
                    return changed ? put('packs', pack, pack.cohort.id) : null;
                }));
            });
        });
    }

    root.MetpOffline = {
        openDb: openDb,
        get: get,
        getAll: getAll,
        put: put,
        remove: remove,
        clearStores: clearStores,
        getMeta: getMeta,
        setMeta: setMeta,
        deviceId: deviceId,
        queue: queue,
        flush: flush,
        pendingCount: pendingCount,
        readCookie: readCookie,
        uuid4: uuid4
    };
})(typeof self !== 'undefined' ? self : this);
