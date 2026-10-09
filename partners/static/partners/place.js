(function () {
    'use strict';

    var configNode = document.getElementById('place-config');
    if (!configNode || typeof L === 'undefined') {
        return;
    }
    var config = JSON.parse(configNode.textContent);

    function tiles(map) {
        L.tileLayer(config.tileUrl, {
            maxZoom: config.maxZoom,
            attribution: config.attribution,
            referrerPolicy: 'strict-origin-when-cross-origin'
        }).addTo(map);
    }

    function pin(extra) {
        return L.divIcon({
            html: '<span class="p-pin p-pin-teal p-pin-on' + (extra || '') + '"></span>',
            className: 'p-pin-wrap', iconSize: [22, 22], iconAnchor: [11, 11]
        });
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-copy-text]');
        if (!button) {
            return;
        }
        var text = button.dataset.copyText;
        var original = button.textContent;
        var done = function () {
            button.textContent = 'Copied';
            setTimeout(function () { button.textContent = original; }, 1800);
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, function () { window.prompt('Copy the coordinates', text); });
        } else {
            window.prompt('Copy the coordinates', text);
        }
    });

    var view = document.querySelector('[data-place-view]');
    if (view && config.position) {
        var viewMap = L.map(view, { scrollWheelZoom: false });
        tiles(viewMap);
        viewMap.setView([config.position.lat, config.position.lng], 14);
        L.marker([config.position.lat, config.position.lng], { icon: pin(), keyboard: false }).addTo(viewMap);
    }

    var picker = document.querySelector('[data-place-picker]');
    if (!picker) {
        return;
    }
    var form = picker.closest('form');
    var latInput = form.querySelector('[data-place="lat"]');
    var lngInput = form.querySelector('[data-place="lng"]');
    var sourceInput = form.querySelector('[data-place="source"]');
    var status = form.querySelector('[data-place-status]');
    var results = form.querySelector('[data-place-results]');
    var searchInput = form.querySelector('[data-place-search]');
    var start = config.position || config.fallback;

    var map = L.map(picker);
    tiles(map);
    map.setView([start.lat, start.lng], config.position ? 15 : start.zoom || 5);
    var marker = null;

    function say(text) {
        status.textContent = text;
    }

    function setPosition(lat, lng, source, zoom) {
        var point = L.latLng(lat, lng).wrap();
        latInput.value = point.lat.toFixed(6);
        lngInput.value = point.lng.toFixed(6);
        sourceInput.value = source;
        if (!marker) {
            marker = L.marker(point, { icon: pin(), draggable: true, keyboard: false }).addTo(map);
            marker.on('dragend', function () {
                var moved = marker.getLatLng();
                setPosition(moved.lat, moved.lng, 'pin');
            });
        } else {
            marker.setLatLng(point);
        }
        if (zoom) {
            map.setView(point, zoom);
        }
    }

    if (config.position) {
        setPosition(config.position.lat, config.position.lng, sourceInput.value || 'pin');
        sourceInput.value = '';
    }

    map.on('click', function (event) {
        setPosition(event.latlng.lat, event.latlng.lng, 'pin');
        say('Pin placed. Drag it to fine-tune, then save.');
    });

    function typed() {
        var lat = parseFloat(latInput.value);
        var lng = parseFloat(lngInput.value);
        if (isFinite(lat) && isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
            setPosition(lat, lng, 'pin', Math.max(map.getZoom(), 13));
        }
    }
    latInput.addEventListener('change', typed);
    lngInput.addEventListener('change', typed);

    form.querySelector('[data-place-locate]').addEventListener('click', function () {
        if (!navigator.geolocation) {
            say('This browser cannot share its position. Place the pin by hand instead.');
            return;
        }
        if (!window.isSecureContext) {
            say('Your position can only be read over a secure (https) connection.');
            return;
        }
        say('Finding your position');
        navigator.geolocation.getCurrentPosition(function (found) {
            setPosition(found.coords.latitude, found.coords.longitude, 'device', 17);
            say('Pin placed at your position, accurate to about ' + Math.round(found.coords.accuracy) + ' m. Save if you are standing at the location.');
        }, function (error) {
            say(error.code === 1 ? 'Permission to read your position was refused.' : 'Your position could not be found. Try again outside, or place the pin by hand.');
        }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
    });

    function showResults(items) {
        results.textContent = '';
        results.hidden = false;
        if (!items.length) {
            var none = document.createElement('li');
            none.className = 'px-3 py-2 text-sm p-muted';
            none.textContent = 'Nothing found. Try the town or district name.';
            results.appendChild(none);
            return;
        }
        items.forEach(function (item) {
            var li = document.createElement('li');
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'w-full px-3 py-2 text-left text-sm hover:bg-slate-50';
            button.textContent = item.display_name;
            button.addEventListener('click', function () {
                map.setView([parseFloat(item.lat), parseFloat(item.lon)], 14);
                results.hidden = true;
                say('Map moved. Click the exact spot to place the pin.');
            });
            li.appendChild(button);
            results.appendChild(li);
        });
    }

    var searching = false;
    function search() {
        var query = searchInput.value.trim();
        if (!query || searching) {
            return;
        }
        searching = true;
        say('Searching');
        var params = new URLSearchParams({ q: query, format: 'jsonv2', limit: '5', countrycodes: config.countryCode || '' });
        fetch(config.searchUrl + '?' + params.toString(), { headers: { Accept: 'application/json' }, referrerPolicy: 'strict-origin-when-cross-origin' })
            .then(function (response) {
                if (!response.ok) { throw new Error(String(response.status)); }
                return response.json();
            })
            .then(function (items) {
                say('');
                showResults(items);
            })
            .catch(function () {
                say('The search is not available right now. Move the map by hand instead.');
            })
            .then(function () {
                setTimeout(function () { searching = false; }, 1100);
            });
    }
    form.querySelector('[data-place-find]').addEventListener('click', search);
    searchInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            search();
        }
    });
}());
