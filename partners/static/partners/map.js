(function () {
    'use strict';

    var app = document.querySelector('[data-map-app]');
    var configNode = document.getElementById('map-config');
    if (!app || !configNode || typeof L === 'undefined') {
        return;
    }
    var config = JSON.parse(configNode.textContent);
    var TRIP_KEY = 'metp-map-trip';
    var GRADUATION_WINDOW_DAYS = 90;

    var els = {
        filters: app.querySelector('[data-map-filters]'),
        country: app.querySelector('#map-country'),
        partner: app.querySelector('#map-partner'),
        count: app.querySelector('[data-map-count]'),
        rows: app.querySelector('[data-map-rows]'),
        panel: app.querySelector('[data-map-panel]'),
        panelDefault: app.querySelector('[data-map-panel-default]'),
        panelBody: app.querySelector('[data-map-panel-body]'),
        missing: app.querySelector('[data-map-missing]'),
        missingTitle: app.querySelector('[data-map-missing-title]'),
        missingList: app.querySelector('[data-map-missing-list]'),
        trip: app.querySelector('[data-map-trip]'),
        tripList: app.querySelector('[data-map-trip-list]'),
        tripCount: app.querySelector('[data-map-trip-count]'),
        tripRoute: app.querySelector('[data-map-trip-route]'),
        tripSheet: app.querySelector('[data-map-trip-sheet]'),
        tripNearest: app.querySelector('[data-map-trip-nearest]'),
        tripClear: app.querySelector('[data-map-trip-clear]')
    };

    var map = L.map(app.querySelector('[data-map-canvas]'), { worldCopyJump: true, minZoom: 2 });
    L.tileLayer(config.tileUrl, {
        maxZoom: config.maxZoom,
        attribution: config.attribution,
        referrerPolicy: 'strict-origin-when-cross-origin'
    }).addTo(map);
    map.setView([2, 22], 3);

    var cluster = L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 48,
        iconCreateFunction: function (group) {
            var count = group.getChildCount();
            return L.divIcon({
                html: '<span>' + count + '</span>',
                className: 'p-cluster' + (count >= 10 ? ' p-cluster-lg' : ''),
                iconSize: L.point(46, 46)
            });
        }
    });
    map.addLayer(cluster);

    var features = [];
    var byId = {};
    var markers = {};
    var areas = L.layerGroup().addTo(map);
    var selectedId = null;
    var today = new Date();
    var trip = loadTrip();

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined && text !== null) {
            node.textContent = text;
        }
        return node;
    }

    function pinIcon(feature) {
        var classes = 'p-pin p-pin-' + feature.colour;
        if (feature.sensitive) {
            classes = 'p-pin p-pin-approx';
        } else if (!feature.confirmed) {
            classes += ' p-pin-unconfirmed';
        }
        if (feature.id === selectedId) {
            classes += ' p-pin-on';
        }
        return L.divIcon({ html: '<span class="' + classes + '"></span>', className: 'p-pin-wrap', iconSize: [22, 22], iconAnchor: [11, 11] });
    }

    function markerFor(feature) {
        if (!markers[feature.id]) {
            var marker = L.marker([feature.lat, feature.lng], { icon: pinIcon(feature), title: feature.name, keyboard: true });
            var tip = el('span', null, feature.sensitive ? 'Sensitive location, ' + feature.country_name : feature.name);
            marker.bindTooltip(tip, { direction: 'top', offset: [0, -10], className: 'p-tip' });
            marker.on('click', function () { select(feature.id, false); });
            markers[feature.id] = marker;
        }
        return markers[feature.id];
    }

    function filters() {
        var form = new FormData(els.filters);
        return {
            q: (form.get('q') || '').toString().trim().toLowerCase(),
            country: form.get('country') || '',
            partner: form.get('partner') || '',
            statuses: form.getAll('status'),
            running: form.get('running') === 'on',
            graduation: form.get('graduation') === 'on'
        };
    }

    function graduationSoon(feature) {
        var event = feature.next_event;
        if (!event || event.kind !== 'graduation') {
            return false;
        }
        var days = (new Date(event.date + 'T00:00:00') - today) / 86400000;
        return days <= GRADUATION_WINDOW_DAYS;
    }

    function matches(feature, f) {
        if (f.statuses.indexOf(feature.status) === -1) { return false; }
        if (f.country && feature.country !== f.country) { return false; }
        if (f.partner && feature.partner_id !== f.partner) { return false; }
        if (f.running && !feature.running) { return false; }
        if (f.graduation && !graduationSoon(feature)) { return false; }
        if (f.q) {
            var haystack = [feature.name, feature.partner, feature.city, feature.region, feature.country_name].join(' ').toLowerCase();
            if (haystack.indexOf(f.q) === -1) { return false; }
        }
        return true;
    }

    function render(fit) {
        var f = filters();
        var shown = features.filter(function (feature) { return matches(feature, f); });
        cluster.clearLayers();
        areas.clearLayers();
        cluster.addLayers(shown.map(markerFor));
        shown.forEach(function (feature) {
            if (feature.sensitive) {
                L.circle([feature.lat, feature.lng], {
                    radius: config.approximateRadius, color: '#0E1B33', weight: 1, dashArray: '4 4',
                    fillOpacity: 0.06, interactive: false
                }).addTo(areas);
            }
        });
        renderRows(shown);
        els.count.textContent = shown.length + ' of ' + features.length + ' location' + (features.length === 1 ? '' : 's') + ' shown';
        if (fit && shown.length) {
            map.fitBounds(L.latLngBounds(shown.map(function (feature) { return [feature.lat, feature.lng]; })).pad(0.2), { maxZoom: 9 });
        }
    }

    function renderRows(shown) {
        els.rows.textContent = '';
        if (!shown.length) {
            var empty = el('tr');
            var cell = el('td', 'p-empty', features.length ? 'No locations match these filters.' : 'No locations have a map position yet.');
            cell.colSpan = 6;
            empty.appendChild(cell);
            els.rows.appendChild(empty);
            return;
        }
        shown.forEach(function (feature) {
            var row = el('tr', feature.id === selectedId ? 'p-row-on' : '');
            row.dataset.rowId = feature.id;
            var name = el('td');
            var button = el('button', 'p-link text-left', feature.name);
            button.type = 'button';
            button.dataset.mapSelect = feature.id;
            name.appendChild(button);
            if (!feature.confirmed) {
                name.appendChild(el('span', 'ml-2 p-badge p-badge-warn', 'Position estimated'));
            }
            row.appendChild(name);
            row.appendChild(el('td', null, feature.partner || 'Withheld'));
            row.appendChild(el('td', 'whitespace-nowrap', feature.country_name));
            var status = el('td');
            status.appendChild(el('span', 'p-badge ' + (feature.status === 'active' ? 'p-badge-good' : feature.status === 'preparing' ? 'p-badge-info' : 'p-badge-muted'), feature.status_label));
            row.appendChild(status);
            var event = feature.next_event;
            row.appendChild(el('td', 'whitespace-nowrap', event ? event.kind_label + ', ' + event.date_label : ''));
            var actions = el('td', 'text-right');
            var show = el('button', 'p-btn-ghost p-btn-sm', 'Show');
            show.type = 'button';
            show.dataset.mapSelect = feature.id;
            show.setAttribute('aria-label', 'Show ' + feature.name + ' on the map');
            actions.appendChild(show);
            row.appendChild(actions);
            els.rows.appendChild(row);
        });
    }

    function renderMissing(missing) {
        if (!missing.length) {
            els.missing.hidden = true;
            return;
        }
        els.missing.hidden = false;
        els.missingTitle.textContent = missing.length + ' location' + (missing.length === 1 ? ' has' : 's have') + ' no map position yet';
        els.missingList.textContent = '';
        missing.forEach(function (item) {
            var li = el('li');
            var link = el('a', 'font-semibold underline', item.name);
            link.href = item.url;
            li.appendChild(link);
            li.appendChild(document.createTextNode(' ' + [item.partner, item.country_name].filter(Boolean).join(', ')));
            els.missingList.appendChild(li);
        });
    }

    function fillSelect(select, pairs) {
        pairs.sort(function (a, b) { return a[1].localeCompare(b[1]); }).forEach(function (pair) {
            var option = el('option', null, pair[1]);
            option.value = pair[0];
            select.appendChild(option);
        });
    }

    function refreshIcon(id) {
        if (markers[id] && byId[id]) {
            markers[id].setIcon(pinIcon(byId[id]));
        }
    }

    function select(id, pan) {
        var feature = byId[id];
        if (!feature) {
            return;
        }
        var previous = selectedId;
        selectedId = id;
        refreshIcon(previous);
        refreshIcon(id);
        Array.prototype.forEach.call(els.rows.querySelectorAll('tr'), function (row) {
            row.classList.toggle('p-row-on', row.dataset.rowId === id);
        });
        if (pan && markers[id]) {
            cluster.zoomToShowLayer(markers[id], function () {
                map.panTo([feature.lat, feature.lng]);
            });
        }
        els.panelDefault.hidden = true;
        els.panelBody.hidden = false;
        els.panelBody.textContent = 'Loading';
        fetch(feature.panel_url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
            .then(function (response) {
                if (!response.ok) { throw new Error(String(response.status)); }
                return response.text();
            })
            .then(function (html) {
                if (selectedId !== id) { return; }
                els.panelBody.innerHTML = html;
                updateTripButtons();
                if (window.matchMedia('(max-width: 1023px)').matches) {
                    els.panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            })
            .catch(function () {
                els.panelBody.textContent = 'These details could not be loaded. Check your connection and try again.';
            });
    }

    function copy(text, button) {
        var done = function () {
            var original = button.textContent;
            button.textContent = 'Copied';
            setTimeout(function () { button.textContent = original; }, 1800);
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, function () { window.prompt('Copy the coordinates', text); });
        } else {
            window.prompt('Copy the coordinates', text);
        }
    }

    function loadTrip() {
        try {
            var stored = JSON.parse(window.localStorage.getItem(TRIP_KEY) || '[]');
            return Array.isArray(stored) ? stored.slice(0, config.maxStops) : [];
        } catch (error) {
            return [];
        }
    }

    function saveTrip() {
        try {
            window.localStorage.setItem(TRIP_KEY, JSON.stringify(trip));
        } catch (error) {
            return;
        }
    }

    function distance(a, b) {
        var rad = Math.PI / 180;
        var dLat = (b.lat - a.lat) * rad;
        var dLng = (b.lng - a.lng) * rad;
        var h = Math.pow(Math.sin(dLat / 2), 2) + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.pow(Math.sin(dLng / 2), 2);
        return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
    }

    function orderedTrip() {
        if (!els.tripNearest.checked || trip.length < 3) {
            return trip.slice();
        }
        var remaining = trip.slice(1);
        var route = [trip[0]];
        while (remaining.length) {
            var here = route[route.length - 1];
            remaining.sort(function (a, b) { return distance(here, a) - distance(here, b); });
            route.push(remaining.shift());
        }
        return route;
    }

    function renderTrip() {
        els.trip.hidden = trip.length === 0;
        els.tripCount.textContent = trip.length + ' of ' + config.maxStops + ' stops';
        els.tripList.textContent = '';
        var ordered = orderedTrip();
        ordered.forEach(function (stop, index) {
            var li = el('li', 'flex items-center justify-between gap-3 px-5 py-3');
            var label = el('span', 'flex min-w-0 items-center gap-3');
            label.appendChild(el('span', 'p-dot p-dot-on', String(index + 1)));
            var text = el('span', 'min-w-0');
            text.appendChild(el('span', 'block truncate text-sm font-semibold p-navy', stop.name));
            var detail = stop.partner || '';
            if (index > 0) {
                detail += (detail ? ' · ' : '') + Math.round(distance(ordered[index - 1], stop)) + ' km from the last stop';
            }
            text.appendChild(el('span', 'block truncate text-xs p-muted', detail));
            label.appendChild(text);
            li.appendChild(label);
            var remove = el('button', 'p-btn-ghost p-btn-sm', 'Remove');
            remove.type = 'button';
            remove.dataset.tripRemove = stop.id;
            remove.setAttribute('aria-label', 'Remove ' + stop.name + ' from the trip');
            li.appendChild(remove);
            els.tripList.appendChild(li);
        });
        var points = ordered.map(function (stop) { return stop.lat + ',' + stop.lng; });
        if (points.length) {
            var params = new URLSearchParams({ api: '1', destination: points[points.length - 1], travelmode: 'driving' });
            if (points.length > 1) {
                params.set('waypoints', points.slice(0, -1).join('|'));
            }
            els.tripRoute.href = 'https://www.google.com/maps/dir/?' + params.toString();
            var sheet = new URLSearchParams();
            trip.forEach(function (stop) { sheet.append('location', stop.id); });
            sheet.set('order', els.tripNearest.checked ? 'nearest' : 'chosen');
            els.tripSheet.href = config.tripUrl + '?' + sheet.toString();
        }
        updateTripButtons();
    }

    function updateTripButtons() {
        Array.prototype.forEach.call(app.querySelectorAll('[data-trip-add]'), function (button) {
            var inTrip = trip.some(function (stop) { return stop.id === button.dataset.tripAdd; });
            button.disabled = inTrip || trip.length >= config.maxStops;
            button.textContent = inTrip ? 'On the trip' : trip.length >= config.maxStops ? 'Trip is full' : 'Add to trip';
        });
    }

    els.filters.addEventListener('submit', function (event) { event.preventDefault(); });
    els.filters.addEventListener('input', function () { render(false); });
    els.filters.addEventListener('change', function () { render(true); });
    els.tripNearest.addEventListener('change', renderTrip);
    els.tripClear.addEventListener('click', function () {
        trip = [];
        saveTrip();
        renderTrip();
    });

    app.addEventListener('click', function (event) {
        var target = event.target.closest('[data-map-select],[data-copy],[data-trip-add],[data-trip-remove]');
        if (!target) {
            return;
        }
        if (target.dataset.mapSelect) {
            select(target.dataset.mapSelect, true);
        } else if (target.dataset.copy) {
            copy(target.dataset.copy, target);
        } else if (target.dataset.tripAdd) {
            if (trip.length < config.maxStops && !trip.some(function (stop) { return stop.id === target.dataset.tripAdd; })) {
                trip.push({
                    id: target.dataset.tripAdd,
                    name: target.dataset.name,
                    partner: target.dataset.partner,
                    lat: parseFloat(target.dataset.lat),
                    lng: parseFloat(target.dataset.lng)
                });
                saveTrip();
                renderTrip();
            }
        } else if (target.dataset.tripRemove) {
            trip = trip.filter(function (stop) { return stop.id !== target.dataset.tripRemove; });
            saveTrip();
            renderTrip();
        }
    });

    fetch(config.dataUrl, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(function (response) {
            if (!response.ok) { throw new Error(String(response.status)); }
            return response.json();
        })
        .then(function (data) {
            features = data.features;
            today = new Date(data.today + 'T00:00:00');
            features.forEach(function (feature) { byId[feature.id] = feature; });
            var countries = {};
            var partners = {};
            features.forEach(function (feature) {
                countries[feature.country] = feature.country_name;
                if (feature.partner_id) { partners[feature.partner_id] = feature.partner; }
            });
            fillSelect(els.country, Object.keys(countries).map(function (key) { return [key, countries[key]]; }));
            fillSelect(els.partner, Object.keys(partners).map(function (key) { return [key, partners[key]]; }));
            renderMissing(data.missing);
            trip = trip.filter(function (stop) { return byId[stop.id] && !byId[stop.id].sensitive; });
            render(true);
            renderTrip();
        })
        .catch(function () {
            els.count.textContent = 'The map could not load its locations. Refresh the page to try again.';
        });
}());
