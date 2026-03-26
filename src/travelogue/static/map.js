// MapLibre GL JS map initialization and interaction

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
    // Route line (rendered first so it sits beneath markers)
    if (MAP_DATA.route && MAP_DATA.route.features && MAP_DATA.route.features.length > 0) {
      map.addSource('route', {
        type: 'geojson',
        data: MAP_DATA.route,
      });
      map.addLayer({
        id: 'route-line',
        type: 'line',
        source: 'route',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': '#0f4c75',
          'line-width': 2,
          'line-opacity': 0.35,
          'line-dasharray': [4, 3],
        },
      });
    }

    // GPX/KML traces (user-provided routes, more prominent)
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

    // Event markers source (clustered)
    map.addSource('events', {
      type: 'geojson',
      data: MAP_DATA.markers,
      cluster: true,
      clusterMaxZoom: 12,
      clusterRadius: 50,
    });

    // Clustered circles
    map.addLayer({
      id: 'clusters',
      type: 'circle',
      source: 'events',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#0f4c75',
        'circle-radius': ['step', ['get', 'point_count'], 16, 5, 20, 10, 26],
        'circle-opacity': 0.85,
      },
    });

    map.addLayer({
      id: 'cluster-count',
      type: 'symbol',
      source: 'events',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': '{point_count_abbreviated}',
        'text-font': ['Open Sans Bold', 'Arial Unicode MS Bold'],
        'text-size': 12,
      },
      paint: { 'text-color': '#fff' },
    });

    // Individual event markers
    map.addLayer({
      id: 'event-points',
      type: 'circle',
      source: 'events',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': '#e8a838',
        'circle-radius': 8,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#fff',
      },
    });

    fitToMarkers();

    // Click cluster: zoom in to expand
    map.on('click', 'clusters', (e) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ['clusters'] });
      const clusterId = features[0].properties.cluster_id;
      map.getSource('events').getClusterExpansionZoom(clusterId, (err, zoom) => {
        if (err) return;
        map.flyTo({ center: features[0].geometry.coordinates, zoom, duration: 600 });
      });
    });

    // Click event marker: fly to it + show popup
    map.on('click', 'event-points', (e) => {
      const props = e.features[0].properties;
      const coords = e.features[0].geometry.coordinates.slice();

      const currentZoom = map.getZoom();
      const targetZoom = Math.max(currentZoom + 2, 13);

      map.flyTo({
        center: coords,
        zoom: targetZoom,
        duration: 600,
      });

      const thumb = props.thumbnail ? `<img src="/${props.thumbnail}" alt="">` : '';
      const photoLabel = props.asset_count === 1 ? '1 photo' : `${props.asset_count} photos`;
      const html = `
        <div class="map-popup">
          ${thumb}
          <div class="map-popup-body">
            <h4>${props.title || 'Event'}</h4>
            <p>${props.date || ''} &middot; ${photoLabel}</p>
            <a href="/events/${props.event_id}.html">View event &rarr;</a>
          </div>
        </div>`;

      // Small delay so the fly animation starts before the popup appears
      setTimeout(() => {
        new maplibregl.Popup({ closeButton: true, maxWidth: '240px', offset: 14 })
          .setLngLat(coords)
          .setHTML(html)
          .addTo(map);
      }, 300);
    });

    map.on('mouseenter', 'clusters', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'clusters', () => { map.getCanvas().style.cursor = ''; });
    map.on('mouseenter', 'event-points', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'event-points', () => { map.getCanvas().style.cursor = ''; });
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
      btn.classList.add(  'active');

      const filter = btn.dataset.filter;
      if (filter === 'all') {
        map.setFilter('event-points', ['!', ['has', 'point_count']]);
        map.setLayoutProperty('clusters', 'visibility', 'visible');
        map.setLayoutProperty('cluster-count', 'visibility', 'visible');
        if (map.getLayer('route-line')) map.setLayoutProperty('route-line', 'visibility', 'visible');
        fitToMarkers();
      } else {
        map.setFilter('event-points', ['all',
          ['!', ['has', 'point_count']],
          ['==', ['get', 'day_id'], filter],
        ]);
        map.setLayoutProperty('clusters', 'visibility', 'none');
        map.setLayoutProperty('cluster-count', 'visibility', 'none');
        if (map.getLayer('route-line')) map.setLayoutProperty('route-line', 'visibility', 'none');

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
