// capture.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api } = RPI;
    const MAX_PAGES = 8;       // the server reads at most 8 images per receipt
    const MAX_PDF_BYTES = 20 * 1024 * 1024;
    const MAX_SIDE = 2400;     // downscale big phone photos before upload
    const $ = (id) => document.getElementById(id);

    let pages = [];            // {blob, url, kind: 'image' | 'pdf', name}
    let stream = null;
    let uploading = false;
    let homeId = null;         // the home this receipt is added to

    $('backLink').append(icon('back'), 'Receipts');
    $('pickBtn').prepend(icon('image'), h('span', { id: 'pickLabel' }, 'Choose photos or PDF'));
    $('cameraBtn').append(icon('camera'), 'Use camera');
    $('closeCamera').append(icon('x'), 'Close camera');
    $('uploadIcon').append(icon('upload'));
    $('nativeBtn').prepend(icon('camera'), 'Take photo');
    $('fallbackIcon').append(icon('camera'));

    // ---------- camera ----------
    async function startCamera() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        return showFallback('Live camera unavailable',
          window.isSecureContext ? 'This browser can’t open the camera here.'
            : 'Browsers only allow the live camera on HTTPS. Use your phone’s camera instead.');
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 2560 }, height: { ideal: 1440 } }, audio: false,
        });
        $('video').srcObject = stream;
        $('viewfinder').hidden = false;
        $('controls').hidden = false;
        $('fallback').hidden = true;
        $('cameraBtn').hidden = true;
        renderPages();
        $('viewfinder').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } catch (err) {
        const msg = {
          NotAllowedError: 'Camera permission was denied. Allow it in your browser settings, or use a photo instead.',
          NotFoundError: 'No camera was found on this device.',
          NotReadableError: 'Another app is using the camera.',
        }[err.name] || 'The camera couldn’t be started.';
        showFallback('Can’t use the live camera', msg);
      }
    }
    function stopCamera() { if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; } }
    function closeCamera() {
      stopCamera();
      $('viewfinder').hidden = true; $('controls').hidden = true; $('fallback').hidden = true; $('cameraBtn').hidden = false;
      renderPages();
    }
    $('cameraBtn').addEventListener('click', startCamera);
    $('closeCamera').addEventListener('click', closeCamera);
    function showFallback(title, text) {
      $('viewfinder').hidden = true; $('controls').hidden = true; $('fallback').hidden = false;
      $('fallbackTitle').textContent = title; $('fallbackText').textContent = text;
      renderPages();
    }

    $('shutter').addEventListener('click', () => {
      const v = $('video');
      if (!v.videoWidth) return;
      if (pages.length >= MAX_PAGES) return RPI.toast(`Up to ${MAX_PAGES} pages per receipt`, 'error');
      const c = document.createElement('canvas');
      c.width = v.videoWidth; c.height = v.videoHeight;
      c.getContext('2d').drawImage(v, 0, 0);
      $('flash').classList.remove('go'); void $('flash').offsetWidth; $('flash').classList.add('go');
      c.toBlob((blob) => blob && (pages.some((p) => p.kind === 'pdf') ? RPI.toast('Remove the PDF first: photos and PDFs can’t be mixed', 'error') : addPage(blob)), 'image/jpeg', 0.92);
    });

    // ---------- files (native camera + library) ----------
    async function shrink(file) {
      // createImageBitmap honours EXIF orientation, so re-encoding also fixes sideways phone photos.
      try {
        const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
        const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
        const c = document.createElement('canvas');
        c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
        c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
        if (bmp.close) bmp.close();
        return await new Promise((res) => c.toBlob((b) => res(b || file), 'image/jpeg', 0.9));
      } catch (_) { return file; } // the server still validates and re-orients
    }
    const isPdf = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name);
    async function addFiles(files) {
      for (const f of files) {
        if (isPdf(f)) {
          if (pages.some((p) => p.kind === 'image')) { RPI.toast('Remove the photos first: a PDF is read on its own', 'error'); continue; }
          if (f.size > MAX_PDF_BYTES) { RPI.toast(`${f.name} is larger than 20 MB`, 'error'); continue; }
          if (pages.length >= MAX_PAGES) { RPI.toast(`Up to ${MAX_PAGES} files per receipt`, 'error'); break; }
          addPage(f, 'pdf', f.name);
        } else if (f.type.startsWith('image/')) {
          if (pages.some((p) => p.kind === 'pdf')) { RPI.toast('Remove the PDF first: photos and PDFs can’t be mixed', 'error'); continue; }
          if (pages.length >= MAX_PAGES) { RPI.toast(`Up to ${MAX_PAGES} pages per receipt`, 'error'); break; }
          addPage(await shrink(f), 'image');
        } else {
          RPI.toast('Add a photo or a PDF', 'error');
        }
      }
    }
    async function onFiles(input) {
      const files = [...input.files]; input.value = '';
      await addFiles(files);
      revealReadButton();
    }
    // Some mobile webviews (the Home Assistant app among them) don't repaint after the photo picker closes
    // until the page is touched; scrolling the new pages and the read button into view forces the repaint
    // and puts the button where the person is looking.
    function revealReadButton() {
      if (!pages.length) return;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        $('trayCard').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        $('bottomAction').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }));
    }
    for (const id of ['nativeInput', 'pickInput']) $(id).addEventListener('change', (e) => onFiles(e.target));
    // drag and drop (computers): photos or a PDF straight onto the card
    const dz = $('dropzone');
    for (const ev of ['dragenter', 'dragover']) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); });
    for (const ev of ['dragleave', 'drop']) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); });
    dz.addEventListener('drop', async (e) => { await addFiles([...(e.dataTransfer && e.dataTransfer.files || [])]); revealReadButton(); });

    // ---------- pages ----------
    function addPage(blob, kind = 'image', name = '') { pages.push({ blob, url: URL.createObjectURL(blob), kind, name }); renderPages(); }
    function removePage(i) { URL.revokeObjectURL(pages[i].url); pages.splice(i, 1); renderPages(); }
    function renderPages() {
      const n = pages.length;
      // the upload card: full when nothing is added yet, just its buttons once there are pages or the camera is open
      const camOpen = !$('viewfinder').hidden;
      $('uploadCard').classList.toggle('compact', n > 0 || camOpen);
      if ($('pickLabel')) $('pickLabel').textContent = n ? 'Add photos or PDF' : 'Choose photos or PDF';
      $('trayCard').hidden = n === 0;
      // Shown whenever there are pages, even with the live camera (which also has a button by the shutter):
      // pages added from the library or a PDF leave the camera's button scrolled out of view.
      $('bottomAction').hidden = n === 0;
      const pdfMode = pages.some((p) => p.kind === 'pdf');
      $('trayTitle').textContent = pdfMode ? (n === 1 ? '1 PDF' : `${n} PDFs`) : (n === 1 ? '1 page' : `${n} pages`);
      $('tray').replaceChildren(...pages.map((p, i) => h('div', { class: 'page-thumb' },
        p.kind === 'pdf'
          ? h('div', { class: 'pdf-thumb', role: 'button', 'aria-label': `Open ${p.name}`, onclick: () => window.open(p.url, '_blank') }, icon('file'), h('b', {}, 'PDF'), h('small', {}, p.name))
          : h('img', { src: p.url, alt: `Page ${i + 1}`, onclick: () => window.open(p.url, '_blank') }),
        p.kind === 'pdf' ? null : h('span', { class: 'n' }, i + 1),
        h('button', { type: 'button', 'aria-label': `Remove page ${i + 1}`, onclick: () => removePage(i) }, icon('x')))));
      const label = pdfMode ? (n > 1 ? `Read ${n} PDFs` : 'Read PDF') : (n > 1 ? `Read ${n} pages` : 'Read receipt');
      for (const b of [$('doneTop'), $('doneBottom')]) { b.replaceChildren(label, icon('check')); b.disabled = n === 0 || !homeId; }
    }
    renderPages();

    // ---------- upload ----------
    function showProgress() {
      const bar = h('div', {}), text = h('p', { class: 'muted' }, 'Starting…');
      const title = h('h2', { style: 'font-size:1.1rem' }, 'Uploading receipt');
      const actions = h('div', { class: 'row', style: 'justify-content:flex-end;margin-top:14px' });
      const overlay = h('div', { class: 'overlay', role: 'alertdialog', 'aria-live': 'polite' },
        h('div', { class: 'panel' }, title, h('div', { class: 'progress' }, bar), text, actions));
      document.body.append(overlay);
      return {
        set(pct, msg) { bar.style.width = pct + '%'; text.textContent = msg; },
        fail(msg, retry) {
          title.textContent = 'Something went wrong'; text.textContent = msg; text.style.color = 'var(--danger)';
          actions.replaceChildren(
            h('button', { class: 'btn btn-ghost', onclick: () => overlay.remove() }, 'Close'),
            h('button', { class: 'btn btn-primary', onclick: () => { overlay.remove(); retry(); } }, 'Try again'));
        },
      };
    }

    let draftId = null;   // reused on retry so pages aren't duplicated
    let uploaded = 0;
    let notes = [];
    async function upload() {
      if (!pages.length || uploading) return;
      uploading = true;
      const ui = showProgress();
      const total = pages.length;
      try {
        if (!draftId) {
          ui.set(5, 'Creating receipt…');
          draftId = (await api('api/v1/receipt-drafts', { method: 'POST', body: { source: 'camera', home_id: homeId } })).id;
          uploaded = 0;
        }
        for (; uploaded < total; uploaded++) {
          const item = pages[uploaded];
          const isPdfItem = item.kind === 'pdf';
          ui.set(10 + (80 * uploaded) / total, isPdfItem ? `Converting ${item.name}…` : total > 1 ? `Uploading page ${uploaded + 1} of ${total}…` : 'Uploading photo…');
          const form = new FormData();
          form.append('file', item.blob, isPdfItem ? item.name : `receipt_${uploaded + 1}.jpg`);
          try {
            const res = await api(`api/v1/receipt-drafts/${draftId}/images`, { method: 'POST', form });
            if (res.truncated) notes.push(`Only the first ${res.images.length} of ${res.pdf_pages} pages of ${item.name} were used.`);
          } catch (e) {
            // A receipt holds up to 8 pages. If earlier files already filled it, carry on with what is there.
            if (isPdfItem && uploaded > 0 && e.status === 400 && /up to \d+ pages/.test(e.message)) { notes.push(`${item.name} was left out: the receipt is full.`); continue; }
            throw e;
          }
        }
        ui.set(94, 'Starting the reader…');
        try { await api('api/v1/extraction/extract', { method: 'POST', body: { draft_id: draftId } }); }
        catch (e) { console.warn('Extraction not started; the review page offers a retry', e); }
        ui.set(100, 'Done');
        stopCamera();
        if (notes.length) sessionStorage.setItem('capture:note:' + draftId, notes.join(' '));
        uploading = false; pages = [];
        RPI.go('review.html?id=' + encodeURIComponent(draftId));
      } catch (e) {
        uploading = false;
        ui.fail(e.message, upload);
      }
    }
    $('doneTop').addEventListener('click', upload);
    $('doneBottom').addEventListener('click', upload);

    window.addEventListener('beforeunload', (e) => { if (pages.length && !uploading) { e.preventDefault(); e.returnValue = ''; } });
    window.addEventListener('pagehide', stopCamera);
    // Opening the photo library (or switching apps) can end the camera on phones; start it again on return.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible' || uploading) return;
      if (stream && stream.getTracks().some((t) => t.readyState === 'ended')) { stopCamera(); startCamera(); }
    });

    // ---------- which home ----------
    async function loadHome() {
      const box = $('homeBox');
      try {
        const [me, homes] = await Promise.all([RPI.me(), api('api/v1/homes')]);
        homeId = RPI.pickHome(homes);
        if (!homeId) {
          box.replaceChildren(h('div', { class: 'banner warn' }, icon('alert'), h('div', { class: 'banner-body' },
            h('strong', {}, 'No home yet. '),
            h('span', { class: 'banner-text' }, me.is_admin ? 'Create a home first, then come back to scan.' : 'An administrator needs to create a home before receipts can be added.')),
            me.is_admin ? h('a', { class: 'btn btn-sm', href: 'index.html' }, 'Create') : null));
        } else if (homes.length > 1) {
          const sel = h('select', { class: 'select', 'aria-label': 'Home' }, homes.map((x) => h('option', { value: x.id }, x.name)));
          sel.value = homeId;
          sel.addEventListener('change', () => { homeId = sel.value; RPI.setCurrentHome(homeId); });
          box.replaceChildren(h('label', { class: 'field' }, h('span', { class: 'label' }, 'Add to home'), sel));
        } else {
          box.replaceChildren(h('div', { class: 'small muted' }, `Adding to ${homes[0].name}`));
        }
      } catch (e) {
        box.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message)));
      }
      renderPages();
    }
    loadHome();  // the camera starts only when "Use camera" is tapped
  
    // Where the original photo or PDF is also kept (the app's Receipt archive folder option)
    RPI.api('api/v1/archive').then((a) => {
      const note = document.getElementById('archiveNote');
      if (!a || !a.path) return;
      note.hidden = false;
      note.textContent = a.enabled && a.writable ? `Each scan is also kept in ${a.path} (the photo, or the whole PDF).`
        : `The receipt archive folder ${a.path} cannot be used: ${a.problem}. Scans still work; fix it in Admin → App settings.`;
    }).catch(() => {});
