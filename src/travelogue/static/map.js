// MapLibre GL JS map initialization and interaction

function getWebSrc(props) {
  if (props?.web) return props.web;
  if (props?.thumbnail) return props.thumbnail.replace('/thumbnails/', '/web/');
  return null;
}

// ---------------------------------------------------------------
// Map lightbox — shows a focused set of photos in a full-screen
// overlay. Opened from hover popup image clicks.
// ---------------------------------------------------------------
const mapLightbox = (() => {
  let photos = [];  // [{src, caption}]
  let idx = 0;

  const el        = document.getElementById('map-lightbox');
  const img       = document.getElementById('map-lightbox-img');
  const bg        = document.getElementById('map-lightbox-bg');
  const counter   = document.getElementById('map-lightbox-counter');
  const filmstrip = document.getElementById('map-lightbox-filmstrip');
  const overlay   = document.getElementById('map-lightbox-overlay');
  const btnClose  = document.getElementById('map-lightbox-close');
  const btnPrev   = document.getElementById('map-lightbox-prev');
  const btnNext   = document.getElementById('map-lightbox-next');

  if (!el) return { open: () => {} };

  function buildFilmstrip() {
    filmstrip.innerHTML = '';
    if (photos.length <= 1) return;
    photos.forEach((p, i) => {
      const thumb = document.createElement('img');
      thumb.src = p.src;
      thumb.alt = '';
      if (i === idx) thumb.classList.add('active');
      thumb.addEventListener('click', () => show(i));
      filmstrip.appendChild(thumb);
    });
  }

  function show(i) {
    idx = (i + photos.length) % photos.length;
    const src = photos[idx].src;
    img.src = src;
    if (bg) bg.style.backgroundImage = `url('${src}')`;
    counter.textContent = photos.length > 1 ? `${idx + 1} / ${photos.length}` : '';
    const multi = photos.length > 1;
    btnPrev.style.visibility = multi ? 'visible' : 'hidden';
    btnNext.style.visibility = multi ? 'visible' : 'hidden';
    // Update filmstrip active state
    filmstrip.querySelectorAll('img').forEach((t, i2) => {
      t.classList.toggle('active', i2 === idx);
    });
    // Scroll active thumbnail into view
    const activeThumb = filmstrip.querySelectorAll('img')[idx];
    if (activeThumb) activeThumb.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }

  function close() { el.hidden = true; photos = []; }

  overlay.addEventListener('click', close);
  btnClose.addEventListener('click', close);
  btnPrev.addEventListener('click', () => show(idx - 1));
  btnNext.addEventListener('click', () => show(idx + 1));
  document.addEventListener('keydown', (e) => {
    if (el.hidden) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') show(idx - 1);
    if (e.key === 'ArrowRight') show(idx + 1);
  });

  return {
    open(photoList, startIdx = 0) {
      photos = photoList;
      el.hidden = false;
      buildFilmstrip();
      show(startIdx);
    },
  };
})();

(function () {
  if (typeof MAP_DATA === 'undefined' || typeof maplibregl === 'undefined') return;

  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json',
    center: [0, 0],
    zoom: 2,
  });

  map.addControl(new maplibregl.NavigationControl(), 'top-right');

  map.on('load', () => {

    // ---------------------------------------------------------------
    // Layer 0: Event halos — large, faint background circles behind
    // everything else; indicate general event locations without
    // interfering with the photo bubble clusters.
    // ---------------------------------------------------------------
    if (MAP_DATA.events && MAP_DATA.events.features.length > 0) {
      map.addSource('events', {
        type: 'geojson',
        data: MAP_DATA.events,
      });
      map.addLayer({
        id: 'event-halos',
        type: 'circle',
        source: 'events',
        paint: {
          'circle-color': '#4a90c4',
          'circle-radius': 22,
          'circle-opacity': 0.12,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#4a90c4',
          'circle-stroke-opacity': 0.18,
        },
      });
    }

    // ---------------------------------------------------------------
    // Route lines — sit between halos and photo markers
    // ---------------------------------------------------------------
    if (MAP_DATA.route && MAP_DATA.route.features && MAP_DATA.route.features.length > 0) {
      map.addSource('route', {
        type: 'geojson',
        data: MAP_DATA.route,
      });

      // Drive segments: dashed navy
      map.addLayer({
        id: 'route-drive',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'type'], 'drive'],
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': '#0f4c75',
          'line-width': 2.5,
          'line-opacity': 0.4,
          'line-dasharray': [4, 3],
        },
      });

      // Flight segments: lighter, more widely spaced dashes
      map.addLayer({
        id: 'route-flight',
        type: 'line',
        source: 'route',
        filter: ['==', ['get', 'type'], 'flight'],
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': '#7a9cc6',
          'line-width': 1.5,
          'line-opacity': 0.3,
          'line-dasharray': [6, 6],
        },
      });
    }

    // GPX/KML traces
    if (MAP_DATA.gpx_traces && MAP_DATA.gpx_traces.length > 0) {
      map.addSource('gpx', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: MAP_DATA.gpx_traces },
      });
      map.addLayer({
        id: 'gpx-line',
        type: 'line',
        source: 'gpx',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#3282b8', 'line-width': 2.5, 'line-opacity': 0.7 },
      });
    }

    // ---------------------------------------------------------------
    // Layer 1: Photo markers (clustered) — amber/gold, primary layer
    // ---------------------------------------------------------------
    map.addSource('photos', {
      type: 'geojson',
      data: MAP_DATA.markers,
      cluster: true,
      clusterMaxZoom: 14,
      clusterRadius: 40,
    });

    map.addLayer({
      id: 'photo-clusters',
      type: 'circle',
      source: 'photos',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#e8a838',
        'circle-radius': ['step', ['get', 'point_count'], 14, 10, 18, 50, 24],
        'circle-opacity': 0.85,
        'circle-stroke-width': 2,
        'circle-stroke-color': 'rgba(255,255,255,0.6)',
      },
    });

    map.addLayer({
      id: 'photo-cluster-count',
      type: 'symbol',
      source: 'photos',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': '{point_count_abbreviated}',
        'text-font': ['Open Sans Bold', 'Arial Unicode MS Bold'],
        'text-size': 11,
      },
      paint: { 'text-color': '#fff' },
    });

    map.addLayer({
      id: 'photo-points',
      type: 'circle',
      source: 'photos',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': '#e8a838',
        'circle-radius': 5,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#fff',
      },
    });

    fitToMarkers();

    // --- Click handlers ---

    // Maximum cluster size for showing all photos on hover
    const CLUSTER_HOVER_MAX = 6;

    // Shared hover popup — sticky: stays open while mouse is over the popup element
    let hoverPopup = null;
    let hideTimer = null;

    function scheduleHide() {
      hideTimer = setTimeout(() => {
        if (hoverPopup) { hoverPopup.remove(); hoverPopup = null; }
      }, 120);
    }

    function cancelHide() {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    }

    function showHoverPopup(coords, html) {
      cancelHide();
      if (hoverPopup) hoverPopup.remove();
      hoverPopup = new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        maxWidth: '480px',
        offset: 14,
        className: 'hover-popup',
      })
        .setLngLat(coords)
        .setHTML(html)
        .addTo(map);

      // Make the popup sticky: moving mouse into it cancels the close timer
      const el = hoverPopup.getElement();
      el.addEventListener('mouseenter', cancelHide);
      el.addEventListener('mouseleave', scheduleHide);
    }

    // Click photo cluster: zoom to expand
    map.on('click', 'photo-clusters', async (e) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ['photo-clusters'] });
      const clusterId = features[0].properties.cluster_id;
      const zoom = await map.getSource('photos').getClusterExpansionZoom(clusterId);
      map.flyTo({ center: features[0].geometry.coordinates, zoom, duration: 600 });
    });

    // Hover cluster: if small enough, show all photos as a grid
    map.on('mouseenter', 'photo-clusters', async (e) => {
      map.getCanvas().style.cursor = 'pointer';
      cancelHide();
      const feature = e.features[0];
      const count = feature.properties.point_count;
      const coords = feature.geometry.coordinates.slice();
      if (count > CLUSTER_HOVER_MAX) return;

      const clusterId = feature.properties.cluster_id;
      const leaves = await map.getSource('photos').getClusterLeaves(clusterId, CLUSTER_HOVER_MAX, 0);
      const validLeaves = leaves.filter(f => f.properties.thumbnail && getWebSrc(f.properties));

      // Build photo list for lightbox (use web path derived from thumbnail path)
      const clusterPhotos = validLeaves.map(f => {
        const webSrc = getWebSrc(f.properties);
        return {
          src: webSrc,
          caption: f.properties.date || '',
          eventId: f.properties.event_id,
          assetId: f.properties.asset_id,
        };
      });

      const thumbs = validLeaves.map((f, i) => {
        return `<img src="${f.properties.thumbnail}" alt="" class="cluster-hover-img"
                     data-cluster-idx="${i}" style="cursor:pointer">`;
      }).join('');

      const date = validLeaves[0]?.properties?.date || '';
      const html = `
        <div class="map-popup-cluster-hover">
          <div class="cluster-hover-grid" id="cluster-grid-${clusterId}">${thumbs}</div>
          ${date ? `<p class="cluster-hover-date">${date} &middot; ${count} photos</p>` : ''}
        </div>`;
      showHoverPopup(coords, html);

      // Wire clicks after popup is in DOM
      requestAnimationFrame(() => {
        document.querySelectorAll(`[data-cluster-idx]`).forEach(img => {
          img.addEventListener('click', () => {
            mapLightbox.open(clusterPhotos, parseInt(img.dataset.clusterIdx));
          });
        });
      });
    });

    map.on('mouseleave', 'photo-clusters', () => {
      map.getCanvas().style.cursor = '';
      scheduleHide();
    });

    // Hover individual photo: show large thumbnail + date, click opens lightbox
    map.on('mouseenter', 'photo-points', (e) => {
      map.getCanvas().style.cursor = 'pointer';
      cancelHide();
      const props = e.features[0].properties;
      const coords = e.features[0].geometry.coordinates.slice();
      const webSrc = getWebSrc(props);
      const thumb = webSrc
        ? `<img src="${props.thumbnail}" alt="" class="map-popup-hover-img"
                id="hover-single-img" style="cursor:pointer">`
        : '';
      const date = props.date ? `<p class="hover-popup-date">${props.date}</p>` : '';
      const html = `<div class="map-popup-hover">${thumb}${date}</div>`;
      showHoverPopup(coords, html);

      // Wire lightbox click after popup is in DOM
      if (webSrc) {
        const photoEntry = [{
          src: webSrc,
          caption: props.date || '',
          eventId: props.event_id,
          assetId: props.asset_id,
        }];
        requestAnimationFrame(() => {
          const imgEl = document.getElementById('hover-single-img');
          if (imgEl) imgEl.addEventListener('click', () => mapLightbox.open(photoEntry, 0));
        });
      }
    });

    map.on('mouseleave', 'photo-points', () => {
      map.getCanvas().style.cursor = '';
      scheduleHide();
    });

    // Click photo marker on map canvas: also open lightbox
    map.on('click', 'photo-points', (e) => {
      const props = e.features[0].properties;
      const webSrc = getWebSrc(props);
      if (!webSrc) return;
      mapLightbox.open([{ src: webSrc, caption: props.date || '' }], 0);
    });
  });

  function fitToMarkers() {
    const features = MAP_DATA.markers.features;
    if (!features.length) return;
    const lngs = features.map(f => f.geometry.coordinates[0]);
    const lats = features.map(f => f.geometry.coordinates[1]);
    const bounds = [
      [Math.min(...lngs) - 0.5, Math.min(...lats) - 0.5],
      [Math.max(...lngs) + 0.5, Math.max(...lats) + 0.5],
    ];
    map.fitBounds(bounds, { padding: 60, maxZoom: 14 });
  }

  // Day filter buttons
  document.querySelectorAll('.map-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.map-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const filter = btn.dataset.filter;
      if (filter === 'all') {
        map.setFilter('photo-points', ['!', ['has', 'point_count']]);
        map.setLayoutProperty('photo-clusters', 'visibility', 'visible');
        map.setLayoutProperty('photo-cluster-count', 'visibility', 'visible');
        if (map.getLayer('event-halos')) {
          map.setFilter('event-halos', null);
          map.setLayoutProperty('event-halos', 'visibility', 'visible');
        }
        if (map.getLayer('route-drive')) map.setLayoutProperty('route-drive', 'visibility', 'visible');
        if (map.getLayer('route-flight')) map.setLayoutProperty('route-flight', 'visibility', 'visible');
        fitToMarkers();
      } else {
        map.setFilter('photo-points', ['all',
          ['!', ['has', 'point_count']],
          ['==', ['get', 'day_id'], filter],
        ]);
        map.setLayoutProperty('photo-clusters', 'visibility', 'none');
        map.setLayoutProperty('photo-cluster-count', 'visibility', 'none');
        if (map.getLayer('event-halos')) {
          map.setFilter('event-halos', ['==', ['get', 'day_id'], filter]);
        }
        if (map.getLayer('route-drive')) map.setLayoutProperty('route-drive', 'visibility', 'none');
        if (map.getLayer('route-flight')) map.setLayoutProperty('route-flight', 'visibility', 'none');

        if (MAP_DATA.day_bounds && MAP_DATA.day_bounds[filter]) {
          const b = MAP_DATA.day_bounds[filter];
          map.fitBounds([[b.min_lon, b.min_lat], [b.max_lon, b.max_lat]], {
            padding: 80,
            maxZoom: 15,
            duration: 600,
          });
        }
      }
    });
  });
})();
