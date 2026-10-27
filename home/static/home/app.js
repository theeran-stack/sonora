(() => {
  const $ = id => document.getElementById(id);
  const fmt = s => {
    if (!Number.isFinite(s) || isNaN(s) || s < 0) return '0:00';
    return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
  };

  // Initial tracks from Django database or Claude reference fallbacks
  let initialRaw = [];
  const scriptEl = $('sonora-tracks-data');
  if (scriptEl && scriptEl.textContent) {
    try {
      initialRaw = JSON.parse(scriptEl.textContent);
    } catch (e) {
      initialRaw = [];
    }
  }

  const DEFAULT_TRACKS = [
    ["Glass Horizon", "Nova Reyes", "Afterglow", 243, 260],
    ["Low Tide Signal", "Kaito Mori", "Harbor Lights", 198, 190],
    ["Violet Hours", "Nova Reyes", "Afterglow", 221, 275],
    ["Paper Satellites", "Ines Calder", "Orbit Notes", 276, 215],
    ["Neon Cartography", "Delta Fern", "Night Maps", 189, 300],
    ["Quiet Machinery", "Ines Calder", "Orbit Notes", 254, 170],
    ["Slow Static", "Kaito Mori", "Harbor Lights", 207, 235],
    ["Mint Circuit", "Delta Fern", "Night Maps", 232, 160],
    ["Ember Static", "Nova Reyes", "Afterglow", 215, 120],
    ["Soft Focus", "Nova Reyes", "Afterglow", 262, 200],
    ["Golden Receipt", "Nova Reyes", "Afterglow", 190, 310],
    ["Last Train Home", "Nova Reyes", "Afterglow", 284, 90],
    ["Amber Window", "Nova Reyes", "Afterglow", 236, 250],
    ["Salt and Wire", "Kaito Mori", "Harbor Lights", 201, 140],
    ["Tidal Memory", "Kaito Mori", "Harbor Lights", 247, 180],
    ["Blue Lantern", "Kaito Mori", "Harbor Lights", 219, 60],
    ["Mooring Lines", "Kaito Mori", "Harbor Lights", 268, 330],
    ["Fog Horn Waltz", "Kaito Mori", "Harbor Lights", 193, 110],
    ["Apogee", "Ines Calder", "Orbit Notes", 231, 270],
    ["Weightless Tuesday", "Ines Calder", "Orbit Notes", 258, 150],
    ["Signal Bloom", "Ines Calder", "Orbit Notes", 212, 220],
    ["Dust Lane", "Ines Calder", "Orbit Notes", 245, 40],
    ["Slow Comet", "Ines Calder", "Orbit Notes", 279, 290],
    ["Midnight Grid", "Delta Fern", "Night Maps", 203, 100],
    ["Rooftop Relay", "Delta Fern", "Night Maps", 226, 240],
    ["Afterhours Atlas", "Delta Fern", "Night Maps", 271, 70],
    ["Street Lamp Code", "Delta Fern", "Night Maps", 196, 320],
    ["Dawn Protocol", "Delta Fern", "Night Maps", 239, 130]
  ].map((t, i) => ({
    id: i,
    title: t[0],
    artist: t[1],
    album: t[2],
    dur: t[3],
    duration: fmt(t[3]),
    g: i % 3 === 2
      ? 'linear-gradient(135deg,hsl(30 6% 24%),hsl(30 8% 52%))'
      : `linear-gradient(135deg,hsl(${8 + (t[4] % 34)} 85% 50%),hsl(${28 + (t[4] % 30)} 92% 62%))`,
    audio: null,
    source_type: 'demo'
  }));

  function parseDuration(d) {
    if (typeof d === 'number') return d;
    if (!d) return 180;
    const parts = String(d).split(':').map(Number);
    if (parts.length === 2) return (parts[0] * 60) + parts[1];
    if (parts.length === 3) return (parts[0] * 3600) + (parts[1] * 60) + parts[2];
    return 180;
  }

  // Load favorites & history from localStorage
  const favSet = new Set(JSON.parse(localStorage.getItem('sonora_favorites') || '[]'));
  let historyList = JSON.parse(localStorage.getItem('sonora_history') || '[]');
  let customPlaylists = JSON.parse(localStorage.getItem('sonora_custom_playlists') || '[]');

  let T = [];
  if (initialRaw && initialRaw.length > 0) {
    T = initialRaw.map((t, i) => {
      const durSec = parseDuration(t.duration || t.dur);
      const h = 8 + ((i * 7) % 34);
      const dbId = t.id !== undefined ? t.id : i;
      const gradient = t.cover_fallback || (t.cover && !t.cover.startsWith('http') && !t.cover.startsWith('/')
        ? t.cover
        : (i % 3 === 2
            ? 'linear-gradient(135deg,hsl(30 6% 24%),hsl(30 8% 52%))'
            : `linear-gradient(135deg,hsl(${h} 85% 50%),hsl(${h + 20} 92% 62%))`));

      const coverUrl = (t.cover && (t.cover.startsWith('/') || t.cover.startsWith('http')))
        ? t.cover
        : `/cover/${dbId}/`;

      const audioUrl = (t.audio && (t.audio.startsWith('/') || t.audio.startsWith('http')))
        ? t.audio
        : `/stream/${dbId}/`;

      const key = `${t.title}::${t.artist}`;
      return {
        id: i,
        key: key,
        db_id: dbId,
        title: t.title || 'Untitled',
        artist: t.artist || 'Unknown artist',
        album: t.album || 'Local library',
        dur: durSec,
        duration: t.duration || fmt(durSec),
        cover: coverUrl,
        cover_fallback: gradient,
        g: gradient,
        audio: audioUrl,
        source_type: t.source_type || 'local',
        format: t.format || 'MP3',
        h: h
      };
    });
  } else {
    T = DEFAULT_TRACKS.map(t => ({ ...t, key: `${t.title}::${t.artist}`, cover_fallback: t.g }));
  }

  const au = $('au');
  const AF = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];
  const RM = matchMedia('(prefers-reduced-motion:reduce)').matches;
  const PRE = {
    Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    'Bass boost': [7, 6, 4, 2, 0, 0, 0, 0, 0, 0],
    Rock: [5, 3, -3, -5, -2, 2, 5, 6, 6, 6],
    Pop: [-2, 0, 3, 5, 4, 1, -1, -2, -2, -2],
    Vocal: [-3, -3, -1, 3, 5, 5, 3, 1, 0, -1],
    Treble: [0, 0, 0, 0, 0, 2, 4, 6, 7, 7],
    Classical: [0, 0, 0, 0, 0, 0, -3, -3, -3, -5],
    Custom: null
  };

  let ctx = null, eqF = [], eqV = PRE.Flat.slice(), dry, wet, pan, master, an, fd, monoN, comp;
  let eight = false, monoOn = false, compOn = false, per = 8, ang = 0;
  let vol = 100, muted = false, ab = 0, pA = 0, pB = 0, sleepT = null;
  let hue = 24, hueC = 24, lvl = 0;
  let cur = 0, pos = 0, playing = false, shuf = false, rep = false, timer = null;
  let activeNav = 'home';
  let activePlaylist = null;

  T.forEach((t, i) => {
    t.h = t.h || (8 + ((i * 7) % 34));
  });

  // Real audio playback directly via Django streaming endpoint (/stream/<id>/)

  function initAudio() {
    if (ctx) return;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = ctx.createMediaElementSource(au);
      eqF = AF.map((f, i) => {
        const b = ctx.createBiquadFilter();
        b.type = i === 0 ? 'lowshelf' : i === 9 ? 'highshelf' : 'peaking';
        b.frequency.value = f;
        b.Q.value = 1.1;
        b.gain.value = eqV[i];
        return b;
      });
      comp = ctx.createDynamicsCompressor();
      monoN = ctx.createGain();
      monoN.channelCountMode = 'explicit';
      monoN.channelInterpretation = 'speakers';

      dry = ctx.createGain();
      wet = ctx.createGain();
      pan = ctx.createPanner();
      pan.panningModel = 'HRTF';
      pan.distanceModel = 'inverse';
      pan.refDistance = 1;
      pan.rolloffFactor = 0.6;

      master = ctx.createGain();
      an = ctx.createAnalyser();
      an.fftSize = 256;
      fd = new Uint8Array(an.frequencyBinCount);

      [src, ...eqF, comp, monoN].reduce((a, b) => { a.connect(b); return b; });
      monoN.connect(dry);
      dry.connect(master);
      monoN.connect(pan);
      pan.connect(wet);
      wet.connect(master);
      master.connect(an);
      an.connect(ctx.destination);

      applyFx();
      setVol(vol);
    } catch (e) {
      console.warn('Web Audio note:', e);
    }
  }

  function applyFx() {
    if (!ctx) return;
    const t = ctx.currentTime;
    dry.gain.setTargetAtTime(eight ? 0 : 1, t, 0.15);
    wet.gain.setTargetAtTime(eight ? 2.2 : 0, t, 0.15);
    monoN.channelCount = monoOn ? 1 : 2;
    comp.threshold.value = compOn ? -30 : 0;
    comp.ratio.value = compOn ? 12 : 1;
  }

  function setVol(v) {
    vol = Math.max(0, Math.min(200, v));
    if ($('vol')) {
      $('vol').value = vol;
      $('vol').style.setProperty('--p', (vol / 2) + '%');
    }
    if ($('vl')) $('vl').textContent = vol + '%';
    if (master) master.gain.value = muted ? 0 : (vol / 100);
    au.volume = Math.min(1, vol / 100);
  }

  function setArtwork(el, coverVal, gradFallback) {
    if (!el) return;
    const fb = gradFallback || 'linear-gradient(135deg, #F26A1B, #FF9F4A)';
    if (coverVal && (coverVal.startsWith('http://') || coverVal.startsWith('https://') || coverVal.startsWith('/'))) {
      el.style.backgroundImage = `url("${coverVal}"), ${fb}`;
      el.style.backgroundSize = 'cover';
      el.style.backgroundPosition = 'center';
    } else {
      el.style.backgroundImage = 'none';
      el.style.background = coverVal || fb;
    }
  }

  function toggleFavorite(key) {
    if (favSet.has(key)) {
      favSet.delete(key);
    } else {
      favSet.add(key);
    }
    localStorage.setItem('sonora_favorites', JSON.stringify([...favSet]));
    updateFavUI();
    if (activeNav === 'favorites') {
      render();
    }
  }

  function updateFavUI() {
    const currentTrack = T[cur];
    const isCurFav = currentTrack ? favSet.has(currentTrack.key) : false;
    const favNow = $('favNow');
    if (favNow) {
      favNow.setAttribute('aria-pressed', isCurFav);
      favNow.setAttribute('title', isCurFav ? 'Remove from favorites' : 'Add to favorites');
    }
    document.querySelectorAll('.row-fav').forEach(btn => {
      const idx = +btn.dataset.favI;
      const t = T[idx];
      if (t) {
        const isFav = favSet.has(t.key);
        btn.setAttribute('aria-pressed', isFav);
      }
    });
  }

  function recordHistory(track) {
    if (!track || !track.key) return;
    historyList = [track.key, ...historyList.filter(k => k !== track.key)].slice(0, 50);
    localStorage.setItem('sonora_history', JSON.stringify(historyList));
  }

  function getVisibleTracks() {
    const q = ($('q') ? $('q').value : '').trim().toLowerCase();
    let list = T;

    if (activeNav === 'favorites') {
      list = T.filter(t => favSet.has(t.key));
    } else if (activeNav === 'history') {
      const map = new Map(T.map(t => [t.key, t]));
      list = historyList.map(k => map.get(k)).filter(Boolean);
    } else if (activePlaylist) {
      const pLow = activePlaylist.toLowerCase();
      list = T.filter(t => (t.album + ' ' + t.title + ' ' + t.artist).toLowerCase().includes(pLow));
    }

    if (q) {
      list = list.filter(t => (t.title + ' ' + t.artist + ' ' + t.album).toLowerCase().includes(q));
    }
    return list;
  }

  function buildFolders() {
    const F = {};
    const visibleTracks = getVisibleTracks();
    visibleTracks.forEach(t => {
      const alb = t.album || 'Local library';
      (F[alb] = F[alb] || []).push(t);
    });

    const chev = d => `<svg viewBox="0 0 24 24"><path d="${d}"/></svg>`;
    const container = $('folders');
    if (!container) return;

    if (Object.keys(F).length === 0) {
      container.innerHTML = `<p class="empty" style="margin-bottom:20px;">No albums match the current view or search.</p>`;
      return;
    }

    container.innerHTML = Object.entries(F).map(([n, ts]) => `
      <section class="folder">
        <div class="fhead">
          <svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
          <div><h3>${n}</h3><small>${ts.length} tracks · My musicc › ${n}</small></div>
          <div class="fnav">
            <button class="ib" aria-label="Slide ${n} left">${chev("M15 5l-7 7 7 7")}</button>
            <button class="ib" aria-label="Slide ${n} right">${chev("M9 5l7 7-7 7")}</button>
          </div>
        </div>
        <div class="slider" tabindex="0" aria-label="${n} tracks">
          ${ts.map((t, i) => {
            const hasRealCover = t.cover && (t.cover.startsWith('/') || t.cover.startsWith('http'));
            const fb = t.cover_fallback || t.g || 'linear-gradient(135deg, #F26A1B, #FF9F4A)';
            const artStyle = hasRealCover
              ? `background-image:url('${t.cover}'), ${fb};background-size:cover;background-position:center;`
              : `background:${fb};`;
            return `
              <button class="card slide" style="--i:${Math.min(i, 8)};--g:${fb};" data-i="${t.id}" data-track-id="${t.db_id || t.id}">
                <span class="art" style="${artStyle}" loading="lazy"></span>
                <b>${t.title}</b>
                <span>${t.artist} · ${fmt(t.dur)}</span>
              </button>
            `;
          }).join('')}
        </div>
        <div class="prog"><b></b></div>
      </section>
    `).join('');

    container.querySelectorAll('.folder').forEach(f => {
      const s = f.querySelector('.slider');
      const pb = f.querySelector('.prog b');
      const [l, r] = f.querySelectorAll('.fnav .ib');
      if (!s) return;

      const step = () => (s.firstElementChild ? s.firstElementChild.offsetWidth + 16 : 180);
      const upd = () => {
        const m = s.scrollWidth - s.clientWidth;
        if (l) l.disabled = s.scrollLeft < 4;
        if (r) r.disabled = s.scrollLeft > m - 4;
        if (pb && s.scrollWidth > 0) {
          pb.style.width = (s.clientWidth / s.scrollWidth * 100) + '%';
          pb.style.marginLeft = (s.scrollLeft / s.scrollWidth * 100) + '%';
        }
      };

      s.addEventListener('scroll', upd, { passive: true });
      addEventListener('resize', upd);
      requestAnimationFrame(upd);

      if (l) l.onclick = () => s.scrollBy({ left: -step() * 3, behavior: 'smooth' });
      if (r) r.onclick = () => s.scrollBy({ left: step() * 3, behavior: 'smooth' });

      const io = new IntersectionObserver(es => {
        es.forEach(e => e.target.classList.toggle('vis', e.intersectionRatio > 0.6));
      }, { root: s, threshold: [0.6] });
      s.querySelectorAll('.slide').forEach(c => io.observe(c));

      let d = null, moved = 0;
      s.addEventListener('pointerdown', e => {
        if (e.pointerType !== 'mouse') return;
        d = { x: e.clientX, l: s.scrollLeft };
        moved = 0;
      });
      addEventListener('pointermove', e => {
        if (!d) return;
        const dx = e.clientX - d.x;
        if (Math.abs(dx) > 5) {
          moved = 1;
          s.classList.add('dragging');
        }
        if (moved) s.scrollLeft = d.l - dx;
      });
      addEventListener('pointerup', () => {
        if (!d) return;
        d = null;
        if (moved) {
          s.classList.remove('dragging');
          s.scrollTo({ left: Math.round(s.scrollLeft / step()) * step(), behavior: 'smooth' });
        }
      });
      s.addEventListener('click', e => {
        if (moved) {
          e.stopPropagation();
          e.preventDefault();
          moved = 0;
        }
      }, true);
    });
  }

  function render() {
    document.querySelectorAll('.slide').forEach(s => s.classList.toggle('on', +s.dataset.i === cur));
    const list = $('list');
    if (!list) return;

    const visible = getVisibleTracks();
    const heading = $('listHeading');
    const sub = $('listSub');

    if (heading) {
      heading.textContent =
        activeNav === 'favorites' ? 'Favorites' :
        activeNav === 'history' ? 'Recently Played' :
        activePlaylist ? `Playlist: ${activePlaylist}` : 'All tracks';
    }
    if (sub) {
      sub.textContent = `${visible.length} track${visible.length === 1 ? '' : 's'}`;
    }

    if (visible.length === 0) {
      const emptyMsg =
        activeNav === 'favorites' ? 'No favorite tracks yet. Click the heart icon on any song to add it here.' :
        activeNav === 'history' ? 'No playback history recorded yet.' :
        'No tracks found matching your search. Try another query or scan music.';
      list.innerHTML = `<p class="empty">${emptyMsg}</p>`;
      return;
    }

    list.innerHTML = visible.map(t => {
      const hasRealCover = t.cover && (t.cover.startsWith('/') || t.cover.startsWith('http'));
      const fb = t.cover_fallback || t.g || 'linear-gradient(135deg, #F26A1B, #FF9F4A)';
      const coverStyle = hasRealCover
        ? `background-image:url('${t.cover}'), ${fb};background-size:cover;background-position:center;`
        : `background:${fb};`;
      const isFav = favSet.has(t.key);
      return `
        <button class="row${t.id === cur ? ' on' : ''}" data-i="${t.id}" data-track-id="${t.db_id || t.id}">
          <span class="art" style="${coverStyle}"></span>
          <span class="t"><b>${t.title}</b><span>${t.artist}</span></span>
          <span class="al">${t.album}</span>
          <span class="row-fav" data-fav-i="${t.id}" aria-pressed="${isFav}" aria-label="Favorite">
            <svg viewBox="0 0 24 24"><path d="M12 21s-8-5-8-11a4.5 4.5 0 018-2.5A4.5 4.5 0 0120 10c0 6-8 11-8 11z"/></svg>
          </span>
          <time>${fmt(t.dur)}</time>
        </button>
      `;
    }).join('');

    updateFavUI();
  }

  function tick() {
    const seek = $('seek');
    const curEl = $('cur');
    if (seek) {
      seek.value = pos;
      seek.style.setProperty('--p', (pos / (T[cur]?.dur || 1) * 100) + '%');
    }
    if (curEl) curEl.textContent = fmt(pos);
  }

  function setPlay(on) {
    playing = on;
    const app = $('app');
    if (app) app.classList.toggle('playing', on);
    const pi = $('pi');
    if (pi) {
      pi.innerHTML = on
        ? '<path d="M7 4h3v16H7zM14 4h3v16h-3z" fill="currentColor"/>'
        : '<path d="M7 4l13 8-13 8z" fill="currentColor"/>';
    }
    const playBtn = $('play');
    if (playBtn) playBtn.setAttribute('aria-label', on ? 'Pause' : 'Play');

    if (on) {
      initAudio();
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      const playPromise = au.play();
      if (playPromise !== undefined) {
        playPromise.catch(e => {
          if (e.name !== 'AbortError') {
            console.warn('Playback error:', e);
            setPlay(false);
          }
        });
      }
    } else {
      au.pause();
    }
  }

  function load(i) {
    if (!T.length) return;
    cur = (i + T.length) % T.length;
    pos = 0;
    const t = T[cur];

    // Record to playback history
    recordHistory(t);

    const streamUrl = t.audio || (t.db_id ? `/stream/${t.db_id}/` : t.url);
    if (streamUrl && au.src !== streamUrl && !au.src.endsWith(streamUrl)) {
      au.src = streamUrl;
      au.load();
    }

    const spd = $('spd');
    au.defaultPlaybackRate = au.playbackRate = spd ? +spd.value : 1;

    if ($('pTitle')) $('pTitle').textContent = t.title;
    if ($('pArtist')) $('pArtist').textContent = t.artist;
    setArtwork($('pArt'), t.cover, t.cover_fallback || t.g);
    setArtwork($('discCover'), t.cover, t.cover_fallback || t.g);

    if ($('hTitle')) $('hTitle').textContent = t.title;
    if ($('hSub')) $('hSub').textContent = t.artist + ' — ' + t.album;
    if ($('dur')) $('dur').textContent = fmt(t.dur);
    if ($('seek')) $('seek').max = t.dur;

    hue = t.h || 24;
    ab = 0;
    abUi();
    tick();
    updateFavUI();
    render();

    if (playing) {
      initAudio();
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      const p = au.play();
      if (p !== undefined) {
        p.catch(e => {
          if (e.name !== 'AbortError') setPlay(false);
        });
      }
    }

    const c = document.querySelector('.slide.on');
    if (c) c.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }

  au.onerror = e => {
    console.error('Audio playback error on track:', T[cur], au.error);
    setPlay(false);
  };

  function next() {
    load(shuf ? Math.floor(Math.random() * T.length) : cur + 1);
  }

  au.ontimeupdate = () => {
    pos = au.currentTime;
    if (ab === 2 && pos >= pB) au.currentTime = pA;
    tick();
  };

  au.onloadedmetadata = () => {
    if (Number.isFinite(au.duration) && au.duration > 0) {
      T[cur].dur = Math.round(au.duration);
      T[cur].duration = fmt(au.duration);
      if ($('seek')) $('seek').max = Math.round(au.duration);
      if ($('dur')) $('dur').textContent = fmt(au.duration);
    }
  };

  au.onended = () => {
    if (rep) {
      au.currentTime = 0;
      au.play();
    } else {
      next();
    }
  };

  function abUi() {
    const abBtn = $('ab');
    if (abBtn) {
      abBtn.setAttribute('aria-pressed', ab === 2);
      abBtn.textContent = ab === 1 ? 'A–…' : 'A–B';
    }
  }

  // Player controls
  if ($('play')) $('play').onclick = () => setPlay(!playing);
  if ($('next')) $('next').onclick = next;
  if ($('prev')) $('prev').onclick = () => (au.currentTime > 3 ? (au.currentTime = 0) : load(cur - 1));
  if ($('back')) $('back').onclick = () => { au.currentTime = Math.max(0, au.currentTime - 10); };
  if ($('fwd')) $('fwd').onclick = () => { au.currentTime = Math.min(au.duration || 9999, au.currentTime + 10); };

  if ($('seek')) {
    $('seek').oninput = e => {
      pos = +e.target.value;
      au.currentTime = pos;
      tick();
    };
  }

  if ($('vol')) {
    $('vol').oninput = e => setVol(+e.target.value);
    $('vol').style.setProperty('--p', '50%');
  }

  if ($('mute')) {
    $('mute').onclick = () => {
      muted = !muted;
      $('mute').setAttribute('aria-pressed', muted);
      setVol(vol);
    };
  }

  if ($('shuf')) {
    $('shuf').onclick = e => {
      shuf = !shuf;
      e.currentTarget.setAttribute('aria-pressed', shuf);
    };
  }

  if ($('rep')) {
    $('rep').onclick = e => {
      rep = !rep;
      e.currentTarget.setAttribute('aria-pressed', rep);
    };
  }

  if ($('ab')) {
    $('ab').onclick = () => {
      ab = (ab + 1) % 3;
      if (ab === 1) pA = au.currentTime;
      if (ab === 2) {
        pB = au.currentTime;
        if (pB <= pA + 0.5) ab = 0;
      }
      abUi();
    };
  }

  if ($('favNow')) {
    $('favNow').onclick = () => {
      if (T[cur]) toggleFavorite(T[cur].key);
    };
  }

  // Delegate clicks in folders and track list
  if ($('folders')) {
    $('folders').onclick = e => {
      const c = e.target.closest('.slide');
      if (c && c.dataset.i !== undefined) {
        load(+c.dataset.i);
        setPlay(true);
      }
    };
  }

  if ($('list')) {
    $('list').onclick = e => {
      const favBtn = e.target.closest('.row-fav');
      if (favBtn) {
        e.stopPropagation();
        e.preventDefault();
        const idx = +favBtn.dataset.favI;
        if (T[idx]) toggleFavorite(T[idx].key);
        return;
      }
      const r = e.target.closest('.row');
      if (r && r.dataset.i !== undefined) {
        load(+r.dataset.i);
        setPlay(true);
      }
    };
  }

  if ($('q')) {
    $('q').oninput = () => {
      buildFolders();
      render();
    };
  }

  // Navigation bar wiring
  function setActiveNav(navId) {
    activeNav = navId;
    activePlaylist = null;
    const navButtons = [$('navHome'), $('navLib'), $('navFav'), $('navHist')].filter(Boolean);
    navButtons.forEach(b => b.removeAttribute('aria-current'));
    const activeBtn = $(`nav${navId.charAt(0).toUpperCase() + navId.slice(1)}`);
    if (activeBtn) activeBtn.setAttribute('aria-current', 'true');

    // Mobile nav
    const mNavButtons = [$('mNavHome'), $('mNavLib'), $('mNavFav'), $('mNavHist')].filter(Boolean);
    mNavButtons.forEach(b => {
      b.classList.remove('btn-primary');
      b.classList.add('btn-ghost');
    });
    const activeMBtn = $(`mNav${navId.charAt(0).toUpperCase() + navId.slice(1)}`);
    if (activeMBtn) {
      activeMBtn.classList.add('btn-primary');
      activeMBtn.classList.remove('btn-ghost');
    }

    buildFolders();
    render();
  }

  if ($('navHome')) $('navHome').onclick = () => setActiveNav('home');
  if ($('mNavHome')) $('mNavHome').onclick = () => setActiveNav('home');

  if ($('navLib')) $('navLib').onclick = () => {
    setActiveNav('home');
    const el = $('list');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  if ($('mNavLib')) $('mNavLib').onclick = () => {
    setActiveNav('home');
    const el = $('list');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  if ($('navFav')) $('navFav').onclick = () => setActiveNav('favorites');
  if ($('mNavFav')) $('mNavFav').onclick = () => setActiveNav('favorites');

  if ($('navHist')) $('navHist').onclick = () => setActiveNav('history');
  if ($('mNavHist')) $('mNavHist').onclick = () => setActiveNav('history');

  // Playlists
  function renderPlaylists() {
    const container = $('playlistsList');
    if (!container) return;
    const baseList = ['Night drive', 'Deep focus', 'Studio sessions'];
    const allPl = [...new Set([...baseList, ...customPlaylists])];
    container.innerHTML = allPl.map(p => `
      <button class="pl-link" data-filter="${p}">${p}</button>
    `).join('');

    container.querySelectorAll('.pl-link').forEach(btn => {
      btn.onclick = () => {
        activePlaylist = btn.dataset.filter;
        activeNav = 'playlist';
        buildFolders();
        render();
      };
    });
  }
  renderPlaylists();

  if ($('newPlBtn')) {
    $('newPlBtn').onclick = () => {
      const name = prompt('Name your playlist:');
      if (name && name.trim()) {
        const clean = name.trim();
        if (!customPlaylists.includes(clean)) {
          customPlaylists.push(clean);
          localStorage.setItem('sonora_custom_playlists', JSON.stringify(customPlaylists));
          renderPlaylists();
        }
      }
    };
  }

  // Audio effects drawer
  function sw(id, fn) {
    const el = $(id);
    if (!el) return;
    el.onclick = () => {
      const on = el.getAttribute('aria-checked') !== 'true';
      el.setAttribute('aria-checked', on);
      fn(on);
    };
  }

  sw('s8', on => { eight = on; applyFx(); });
  sw('sm', on => { monoOn = on; applyFx(); });
  sw('sc', on => { compOn = on; applyFx(); });

  if ($('r8')) {
    $('r8').oninput = e => {
      per = +e.target.value;
      if ($('r8v')) $('r8v').textContent = per + ' s';
    };
  }

  if ($('spd')) {
    $('spd').oninput = e => {
      const v = +e.target.value;
      au.defaultPlaybackRate = au.playbackRate = v;
      if ($('spv')) $('spv').textContent = v.toFixed(2) + '×';
    };
  }

  if ($('sl')) {
    $('sl').onchange = e => {
      clearTimeout(sleepT);
      const val = +e.target.value;
      if (val) {
        sleepT = setTimeout(() => {
          setPlay(false);
          $('sl').value = '0';
        }, val * 60000);
      }
    };
  }

  if ($('pre')) {
    $('pre').innerHTML = Object.keys(PRE).map(k => `<option>${k}</option>`).join('');
    $('pre').onchange = e => {
      const p = PRE[e.target.value];
      if (!p) return;
      eqV = p.slice();
      eqV.forEach((g, idx) => {
        const inputs = $('eq') ? $('eq').querySelectorAll('input') : [];
        if (inputs[idx]) inputs[idx].value = g;
        if (eqF[idx]) eqF[idx].gain.value = g;
      });
    };
  }

  if ($('eq')) {
    $('eq').innerHTML = AF.map((f, i) => `
      <label>
        <input type='range' min='-12' max='12' step='1' value='0' data-b='${i}' aria-label='${f >= 1000 ? (f / 1000) + ' kHz' : f + ' Hz'}'>
        <span>${f >= 1000 ? (f / 1000) + 'k' : f}</span>
      </label>
    `).join('');

    $('eq').oninput = e => {
      const i = +e.target.dataset.b;
      eqV[i] = +e.target.value;
      if (eqF[i]) eqF[i].gain.value = eqV[i];
      if ($('pre')) $('pre').value = 'Custom';
    };
  }

  function drawer(o) {
    const fxp = $('fxp');
    if (!fxp) return;
    fxp.classList.toggle('open', o);
    fxp.inert = !o;
    if ($('fx')) $('fx').setAttribute('aria-pressed', o);
  }

  if ($('fx')) $('fx').onclick = () => drawer(!$('fxp').classList.contains('open'));
  if ($('fxc')) $('fxc').onclick = () => drawer(false);

  // File & folder picking with background server upload
  if ($('bFiles') && $('fFiles')) $('bFiles').onclick = () => $('fFiles').click();
  if ($('bDir') && $('fDir')) $('bDir').onclick = () => $('fDir').click();

  if ($('fFiles')) $('fFiles').onchange = e => { handleUserFiles(e.target.files); e.target.value = ''; };
  if ($('fDir')) $('fDir').onchange = e => { handleUserFiles(e.target.files); e.target.value = ''; };

  async function handleUserFiles(fileList) {
    const ok = ['mp3', 'wav', 'flac', 'm4a', 'ogg'];
    const fs = [...fileList].filter(f => ok.includes(f.name.split('.').pop().toLowerCase()));
    if (!fs.length) {
      if ($('scanTitle')) $('scanTitle').textContent = 'No supported audio found';
      if ($('scanSub')) $('scanSub').textContent = 'Choose MP3, WAV, FLAC, M4A or OGG files.';
      return;
    }

    // 1. Add locally to immediate in-browser playback
    const have = new Set(T.map(t => t.title + t.artist));
    const fresh = [];
    fs.forEach(f => {
      const base = f.name.slice(0, f.name.lastIndexOf('.')).split(' - ');
      const title = base.pop();
      const artist = base[0] || 'Unknown artist';
      const key = `${title}::${artist}`;
      if (have.has(key)) return;
      const id = T.length;
      const h = 8 + ((id * 7) % 34);
      const t = {
        id,
        key,
        h,
        title,
        artist,
        album: (f.webkitRelativePath || '').split('/').slice(-2, -1)[0] || 'Added files',
        dur: 180,
        url: URL.createObjectURL(f),
        g: id % 3 === 2
          ? 'linear-gradient(135deg,hsl(30 6% 24%),hsl(30 8% 52%))'
          : `linear-gradient(135deg,hsl(${h} 85% 50%),hsl(${h + 20} 92% 62%))`
      };
      T.push(t);
      fresh.push(t);
    });

    let n = 0;
    if ($('scanTitle')) $('scanTitle').textContent = 'Loading audio files';
    await Promise.all(fresh.map(t => new Promise(res => {
      const a = new Audio();
      a.preload = 'metadata';
      const d = () => {
        if ($('scanBar')) $('scanBar').style.width = (++n / fresh.length * 100) + '%';
        if ($('scanSub')) $('scanSub').textContent = 'Processing metadata… ' + n + ' of ' + fresh.length;
        res();
      };
      a.onloadedmetadata = () => { t.dur = a.duration || 180; d(); };
      a.onerror = () => { d(); };
      a.src = t.url;
    })));

    buildFolders();
    render();

    // 2. Upload to Django server in background
    try {
      const fdData = new FormData();
      fs.forEach(f => fdData.append('audio_files', f));
      fetch('/api/upload/', { method: 'POST', body: fdData })
        .then(r => r.json())
        .then(res => {
          if ($('scanTitle')) $('scanTitle').textContent = 'Library updated';
          if ($('scanSub')) $('scanSub').textContent = `${fresh.length} file(s) saved and indexed.`;
          setTimeout(() => { if ($('scanBar')) $('scanBar').style.width = '0'; }, 1500);
        })
        .catch(() => {});
    } catch (e) {}
  }

  // Scan music button integration
  const scanBtn = $('scanBtn');
  if (scanBtn) {
    let pollTimer = null;
    scanBtn.onclick = () => {
      scanBtn.disabled = true;
      if ($('scanTitle')) $('scanTitle').textContent = 'Scanning your music folder';
      if ($('scanSub')) $('scanSub').textContent = 'Scanning C:\\Users\\Theeran\\Documents\\My musicc...';
      if ($('scanBar')) $('scanBar').style.width = '25%';

      fetch('/api/scan/', {
        method: 'POST',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'Content-Type': 'application/json'
        }
      })
      .then(r => r.json())
      .then(() => {
        let progress = 30;
        pollTimer = setInterval(() => {
          fetch('/api/scan-status/')
            .then(r => r.json())
            .then(data => {
              if (data.is_scanning) {
                progress = Math.min(progress + 15, 90);
                if ($('scanBar')) $('scanBar').style.width = progress + '%';
                if ($('scanSub')) $('scanSub').textContent = `Reading metadata… ${data.scanned_count || 0} files found`;
              } else {
                clearInterval(pollTimer);
                scanBtn.disabled = false;
                if ($('scanBar')) $('scanBar').style.width = '100%';
                if ($('scanTitle')) $('scanTitle').textContent = 'Scan complete';
                if ($('scanSub')) $('scanSub').textContent = `Library updated: ${data.total_tracks || 0} tracks available`;
                if ($('scanNum')) $('scanNum').textContent = `${data.total_tracks || 0} tracks`;
                setTimeout(() => {
                  if ($('scanBar')) $('scanBar').style.width = '0';
                  window.location.reload();
                }, 1200);
              }
            })
            .catch(() => clearInterval(pollTimer));
        }, 800);
      })
      .catch(() => {
        scanBtn.disabled = false;
        if ($('scanTitle')) $('scanTitle').textContent = 'Scan ready';
      });
    };
  }

  // Keyboard navigation & playback shortcuts
  addEventListener('keydown', e => {
    const tg = e.target;
    const k = e.key;
    if (/INPUT|SELECT|TEXTAREA/.test(tg.tagName) || (tg.classList && tg.classList.contains('slider')) || e.ctrlKey || e.metaKey) return;

    if (k === ' ') {
      if (tg.tagName === 'BUTTON') return;
      e.preventDefault();
      setPlay(!playing);
    } else if (k === 'ArrowLeft') {
      if ($('back')) $('back').click();
    } else if (k === 'ArrowRight') {
      if ($('fwd')) $('fwd').click();
    } else if (k === 'ArrowUp') {
      e.preventDefault();
      setVol(vol + 5);
    } else if (k === 'ArrowDown') {
      e.preventDefault();
      setVol(vol - 5);
    } else if (k === 'm' || k === 'M') {
      if ($('mute')) $('mute').click();
    } else if (k === 'n' || k === 'N') {
      next();
    } else if (k === 'p' || k === 'P') {
      if ($('prev')) $('prev').click();
    } else if (k === 's' || k === 'S') {
      if ($('shuf')) $('shuf').click();
    } else if (k === 'l' || k === 'L') {
      if ($('rep')) $('rep').click();
    } else if (k === 'a' || k === 'A') {
      if ($('ab')) $('ab').click();
    } else if (k === '[' || k === ']') {
      const s = $('spd');
      if (s) {
        s.value = +s.value + (k === ']' ? 0.25 : -0.25);
        s.oninput({ target: s });
      }
    } else if (k === 'Escape') {
      drawer(false);
    }
  });

  // Dynamic ambient aura & turntable animation loop
  const bl = [...document.querySelectorAll('.aura i')];
  const Pt = [{ x: innerWidth * 0.6, y: innerHeight * 0.3 }, { x: innerWidth * 0.4, y: innerHeight * 0.6 }];
  const ms = { x: innerWidth * 0.5, y: innerHeight * 0.5 };
  const rs = document.documentElement.style;

  addEventListener('pointermove', e => {
    ms.x = e.clientX;
    ms.y = e.clientY;
  });

  let lt = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - lt) / 1000);
    const W = innerWidth;
    const H = innerHeight;
    lt = now;

    if (eight && !RM) ang = (ang + (dt * 6.2832 / per)) % 6.2832;

    if (ctx) {
      if (eight && pan) {
        const x = Math.sin(ang) * 4;
        const z = -Math.cos(ang) * 4;
        if (pan.positionX) {
          pan.positionX.value = x;
          pan.positionZ.value = z;
        } else if (pan.setPosition) {
          pan.setPosition(x, 0, z);
        }
      }
      if (an && fd) {
        an.getByteFrequencyData(fd);
        let s = 0;
        for (let i = 0; i < 6; i++) s += fd[i];
        lvl += ((s / 1530) - lvl) * 0.2;
      }
    }

    if (eight && $('od')) {
      $('od').style.transform = `translate(${Math.sin(ang) * 38}px, ${-Math.cos(ang) * 38}px)`;
    }

    const c = document.querySelector('.slide.on');
    const r = c && c.getBoundingClientRect();
    const a = r && r.width && r.bottom > 0 && r.top < H
      ? { x: r.left + r.width / 2, y: r.top + r.height / 2 }
      : { x: W * 0.68, y: H * 0.28 };

    const tg = eight
      ? [{ x: W / 2 + Math.sin(ang) * W * 0.32, y: H / 2 - Math.cos(ang) * H * 0.28 },
         { x: W / 2 - Math.sin(ang) * W * 0.32, y: H / 2 + Math.cos(ang) * H * 0.28 }]
      : [a, ms];

    hueC += (hue - hueC) * 0.04;

    tg.forEach((q, k) => {
      const p = Pt[k];
      const e = bl[k] ? bl[k].style : null;
      if (!p || !e) return;
      p.x += (q.x - p.x) * 0.05;
      p.y += (q.y - p.y) * 0.05;
      e.setProperty('--x', p.x + 'px');
      e.setProperty('--y', p.y + 'px');
      e.setProperty('--s', 1 + lvl * 0.6);
      e.setProperty('--h', hueC + k * 16);
    });

    const hero = document.querySelector('.hero');
    if (hero) {
      const hr = hero.getBoundingClientRect();
      rs.setProperty('--hx', ((Pt[0].x - hr.left) / hr.width * 100) + '%');
      rs.setProperty('--hy', ((Pt[0].y - hr.top) / hr.height * 100) + '%');
    }
    rs.setProperty('--px', (Pt[0].x / W * 100) + '%');
    rs.setProperty('--py', (Pt[0].y / H * 100) + '%');
    rs.setProperty('--hue', hueC);

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // Initialize UI
  buildFolders();
  if ($('hCount')) $('hCount').textContent = T.length + ' tracks';
  if ($('scanNum')) $('scanNum').textContent = T.length + ' tracks';
  setVol(100);
  load(cur);
})();
