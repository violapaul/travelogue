// Simple lightbox for full-size image viewing

let currentImages = [];
let currentIndex = 0;

function openLightbox(src) {
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lightbox-img');
  if (!lb || !img) return;
  img.src = src;
  lb.removeAttribute('hidden');
  document.addEventListener('keydown', lightboxKeyHandler);
}

function openLightboxFromGrid(index) {
  if (typeof IMAGES === 'undefined') return;
  currentImages = IMAGES;
  currentIndex = index;
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lightbox-img');
  if (!lb || !img) return;
  img.src = currentImages[currentIndex];
  lb.removeAttribute('hidden');
  document.addEventListener('keydown', lightboxKeyHandler);
}

function lightboxNav(dir) {
  if (!currentImages.length) return;
  currentIndex = (currentIndex + dir + currentImages.length) % currentImages.length;
  const img = document.getElementById('lightbox-img');
  if (img) img.src = currentImages[currentIndex];
}

function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.setAttribute('hidden', '');
  document.removeEventListener('keydown', lightboxKeyHandler);
}

function lightboxKeyHandler(e) {
  if (e.key === 'Escape') closeLightbox();
  if (e.key === 'ArrowRight') lightboxNav(1);
  if (e.key === 'ArrowLeft') lightboxNav(-1);
}
