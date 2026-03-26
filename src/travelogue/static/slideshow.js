// Slideshow controller

(function () {
  const img = document.getElementById('slideshow-img');
  const caption = document.getElementById('slideshow-caption');
  const counter = document.getElementById('slide-counter');
  const thumbsContainer = document.getElementById('slideshow-thumbnails');
  const btnAuto = document.getElementById('btn-auto');

  if (!img || !thumbsContainer) return;

  const thumbs = Array.from(thumbsContainer.querySelectorAll('.slide-thumb'));
  let current = 0;
  let autoInterval = null;

  function goToSlide(index) {
    if (index < 0 || index >= thumbs.length) return;
    current = index;
    const thumb = thumbs[current];
    img.src = thumb.dataset.src || '';
    caption.textContent = thumb.dataset.caption || '';
    if (counter) counter.textContent = `${current + 1} / ${thumbs.length}`;

    thumbs.forEach((t, i) => t.classList.toggle('active', i === current));

    // Scroll thumbnail into view
    thumb.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }

  window.slideNav = function (dir) {
    goToSlide((current + dir + thumbs.length) % thumbs.length);
  };

  window.goToSlide = goToSlide;

  window.slideThumbIndex = function (el) {
    return thumbs.indexOf(el);
  };

  window.toggleAuto = function () {
    if (autoInterval) {
      clearInterval(autoInterval);
      autoInterval = null;
      btnAuto.classList.remove('active');
      btnAuto.textContent = '▶ Auto';
    } else {
      autoInterval = setInterval(() => slideNav(1), 4000);
      btnAuto.classList.add('active');
      btnAuto.textContent = '⏸ Auto';
    }
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === ' ') slideNav(1);
    if (e.key === 'ArrowLeft') slideNav(-1);
    if (e.key === 'Escape' && autoInterval) toggleAuto();
  });

  // Initialize with first slide
  if (thumbs.length > 0) goToSlide(0);
})();
